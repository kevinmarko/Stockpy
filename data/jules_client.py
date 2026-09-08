"""HTTP client for Google's Jules coding-agent REST API
(https://jules.googleapis.com/v1alpha/) — the single network seam every Jules
consumer in this platform goes through. See docs/JULES_INTEGRATION.md for the
full setup/safety writeup.

Why this is simpler than data/fmp_client.py
--------------------------------------------
``data/fmp_client.py`` is this repo's own precedent for "a new opt-in external
API integration with an API key," and this module deliberately mirrors its
*shape* (lazy ``settings.X`` credential read, a single internal exception
class, thin wrappers returning raw parsed JSON) but NOT its throttle/retry/
circuit-breaker machinery. That machinery exists in ``fmp_client.py``
specifically because FMP's rate limit is per-ACCOUNT and shared by MANY
concurrent consumers (fundamentals, quotes, bars, analyst, earnings, macro,
insider/sector) hammered from an 8-thread pool in ``data_engine.py``. Jules
still has exactly ONE consumer — one MCP tool (``investyo_mcp_server.py``)
plus one CLI script (``scripts/jules_dispatch.py``). There is still no
SHARED budget across multiple consumers to protect, and FMP-style
per-account throttle/retry/circuit-breaker machinery remains the wrong model
here — that reasoning is unchanged, and this module still doesn't have it.

**What DID change (2026-09 hardening pass — see ``docs/JULES_INTEGRATION.md``
Sec 4 and ``.claude/jules_confirm_hard_gate_implementation_plan.md``)**: the
original text here reasoned from "invoked by a human, at human cadence, at
most a handful of times a day" to "no cooldown breaker needed." That
reasoning quietly assumed the ONLY caller is a human typing at a keyboard —
but this module's actual callers are an MCP tool and a CLI script, both of
which an AGENT can drive too, and an agent looping (accidentally, via a
retry, a scheduled/unattended session, or a bug) can trivially exceed "human
cadence" in a way a human operator physically cannot. That is a real,
different risk from the "shared budget across many consumers" question the
paragraph above is actually about, and closing it doesn't require FMP-style
machinery: :func:`_enforce_dispatch_cooldown` reads the last recorded
dispatch timestamp straight out of the existing dispatch ledger (no new
shared-budget primitive, no new state file) and refuses a second dispatch
within ``settings.JULES_DISPATCH_COOLDOWN_SECONDS``. This is honestly a
friction mechanism against an ACCIDENTAL rapid/looping dispatch, not a gate
against a single deliberate call — a determined caller trivially waits out
the cooldown, or calls :func:`dispatch_session` directly instead of through
whatever loop tripped it. If Jules ever grows a second, high-frequency
consumer, FMP-style shared-limiter machinery is still the right escalation
for THAT — not a missing piece today.

See also the "Dispatch approval — prompt-hash pinning" section below for the
second half of this hardening pass.

Credential handling — ``settings.JULES_API_KEY``, NEVER ``os.environ``
------------------------------------------------------------------------
Same rule as every other credential in this codebase (see
``data/fmp_client.py``'s own docstring for the full incident history):
pydantic-settings' ``env_file=".env"`` populates the ``settings`` singleton
directly, NOT the real process ``os.environ``. The read below is therefore a
lazy ``from settings import settings`` **inside** each function, never at
module scope, so a test can monkeypatch the singleton and import of this
module never touches configuration.

Setting ``JULES_API_KEY`` alone changes nothing — ``settings.JULES_ENABLED``
must also be explicitly true (see settings.py's own field description for why
its default is False and why it is a ``settings_keysets.DANGEROUS_KEYS``
member).

``automationMode`` is hardcoded, not configurable
--------------------------------------------------
Jules's ``automationMode`` enum has exactly two values:
``AUTOMATION_MODE_UNSPECIFIED`` (no automation) and ``AUTO_CREATE_PR``. This
module hardcodes ``_AUTOMATION_MODE = "AUTO_CREATE_PR"`` for
:func:`dispatch_session` rather than exposing it as a parameter — the whole
point of this integration is dispatching a session that opens a PR, and a
caller-supplied ``automationMode`` would let the meaning of the ``confirm``
gate (in ``investyo_mcp_server.py``'s ``dispatch_jules_task``) drift out of
sync with whether the call actually creates a PR. Do not add an
``automationMode`` parameter without re-deriving this reasoning.

Dispatch ledger — idempotency + audit
---------------------------------------
Every successful :func:`dispatch_session` call is appended to
``output/jules_dispatched.jsonl`` (one JSON object per line), mirroring
``execution/receipts_store.py``'s append-only-JSONL pattern in *spirit* only
— this integration has none of that module's multi-file reconciliation
machinery, since there is nothing here to reconcile against a broker. The
ledger exists so a retried/duplicate call does not silently fire a second
autonomous session against the same target: :func:`dispatch_session` refuses
(unless ``force=True``) when an identical ``dedup_key`` — same UTC day, same
source/branch/title/prompt — was already dispatched today. A different day's
identical prompt is allowed, exactly matching ``receipts_store.py``'s
date-scoped ``dedup_key`` reasoning.

Dispatch approval — prompt-hash pinning (Phase 1, 2026-09)
-------------------------------------------------------------
:func:`request_dispatch_approval` records a durable, single-use pre-approval
for a LATER :func:`dispatch_session` call, pinned to a sha256 hash of the
exact ``source|branch|title|prompt`` content (:func:`_content_hash` — the
same normalization :func:`_compute_dedup_key` already used, now shared by
both so the dedup ledger's truncated hash and the approval's full hash can
never silently diverge on what counts as "the same content"). The returned
``approval_token`` is stored in ``output/jules_pending_approvals.json``
(a small JSON dict, not a SQL store — this is a handful of short-lived
pending approvals, not the multi-file broker-reconciliation problem
``execution/receipts_store.py`` solves) and expires after
``settings.JULES_APPROVAL_TTL_SECONDS`` (default 600s). :func:`dispatch_session`
requires a matching, unexpired, not-already-used ``approval_token`` whose
recorded hash equals a freshly computed hash of the CURRENT dispatch content
— on any mismatch it raises :class:`JulesApprovalMismatch` naming exactly
which check failed (missing token / unknown token / expired / already used /
content changed since approval), never one collapsed message.

**Read this honestly, not as more than it is.** This closes the "approved
prompt X, dispatched prompt Y" swap/drift risk — an approval can only ever
authorize the EXACT content it was requested for. It does **not** prove a
human reviewed that content, and it does **not** stop a single agent turn
from calling :func:`request_dispatch_approval` and :func:`dispatch_session`
back-to-back with matching content in one turn — that remains exactly as
easy as setting ``confirm=True`` alone always was. See
``docs/JULES_INTEGRATION.md`` Sec 4 for the full, unsoftened statement of
what this does and does not achieve.

The approvals store is deliberately FAIL-CLOSED on a read failure
(:func:`_load_pending_approvals` — a missing or corrupt file degrades to
"no pending approvals exist," never to "every token is valid"), the exact
opposite tolerance direction from the dispatch ledger's dedup check
(:func:`_check_dispatch_dedup`, which fails OPEN to "not a duplicate").
That asymmetry is intentional: the dedup ledger is a convenience/
idempotency feature where failing open is the safe default; the approvals
store is an actual safety gate this whole mechanism exists to enforce, so a
read failure there must never be interpreted as permission.

Real implementation, not a scaffold
-------------------------------------
``list_sources``/``dispatch_session`` make real HTTP calls against the Jules
REST API (``GET /sources`` / ``POST /sessions``) — every consumer of this
module (the MCP tools, the CLI script, their tests) is written against these
exact signatures and bodies.

Error-contract discipline: ``response.json()`` is always called INSIDE the
same ``try/except`` that wraps the raw ``requests`` call (or its own
dedicated try/except raising :class:`JulesUnavailable`) — a malformed/empty
2xx body must degrade the same way a transport error or non-2xx status does,
never escape as a raw ``JSONDecodeError``. See CONSTRAINT #6 below.

The dispatch ledger's check-then-write sequence (``_check_dispatch_dedup``
followed by the POST and ``_record_dispatch``) is protected end-to-end by an
OS-level advisory lock (see ``_dispatch_lock`` below) so two concurrent/
retried calls for the same source/branch/title/prompt on the same day cannot
both pass the dedup check before either records its dispatch.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

JULES_BASE_URL = "https://jules.googleapis.com/v1alpha"

# Jules's automationMode enum has exactly two values; see the module
# docstring's "automationMode is hardcoded" section for why this is a
# constant and not a dispatch_session() parameter.
_AUTOMATION_MODE = "AUTO_CREATE_PR"

_LEDGER_FILENAME = "jules_dispatched.jsonl"
_APPROVALS_FILENAME = "jules_pending_approvals.json"


class JulesUnavailable(Exception):
    """Raised when a Jules request could not be served.

    Named for the CONDITION (Jules is not serving us this call) rather than
    for any one cause of it: a missing/rejected key, JULES_ENABLED=False, an
    unknown ``source``, or an HTTP failure all leave the caller with the same
    fact and the same remedy — do not dispatch, surface a clear message.

    CONSTRAINT #6: callers at the MCP-tool / CLI boundary catch this and
    return a clear string; it never crosses the MCP transport boundary as a
    raw exception (mirrors ``FMPUnavailable``'s own contract in
    ``data/fmp_client.py``).
    """


class JulesConfirmationRequired(JulesUnavailable):
    """Raised by :func:`dispatch_session` when called with ``confirm`` not
    exactly ``True``.

    Subclasses :class:`JulesUnavailable` rather than a bare exception so the
    existing ``except JulesUnavailable`` boundary at every current call site
    (``investyo_mcp_server.py``'s ``dispatch_jules_task``,
    ``scripts/jules_dispatch.py``'s ``_cmd_create_session``) keeps working
    unchanged, AND so a future third caller that forgets its own
    confirm-gate still gets a clear, catchable failure — never an unhandled
    crash through the MCP transport boundary — instead of silently being
    allowed to dispatch. The safety property (never dispatch unconfirmed)
    now lives centrally in :func:`dispatch_session` itself, not only in each
    caller's own pre-check.
    """


class JulesApprovalMismatch(JulesUnavailable):
    """Raised by :func:`dispatch_session` when ``approval_token`` fails
    validation against the pending-approvals store recorded by
    :func:`request_dispatch_approval` — see this module's "Dispatch
    approval — prompt-hash pinning" docstring section for the full design.

    Every raise site names EXACTLY which check failed (missing token /
    unknown token / expired / already used / content changed since
    approval) — deliberately never one collapsed message, so a caller or a
    log reader can tell these apart. Subclasses :class:`JulesUnavailable` so
    the existing ``except JulesUnavailable`` boundary at every current call
    site keeps working unchanged for this new failure mode too, exactly as
    :class:`JulesConfirmationRequired` already does.

    HONESTY (see the module docstring): this raises the bar against an
    "approved X, dispatched Y" content mismatch. It does not, and cannot,
    prove a human reviewed the approved content — an agent can call
    :func:`request_dispatch_approval` and :func:`dispatch_session`
    back-to-back with matching content in a single turn.
    """


class JulesDispatchCooldownActive(JulesUnavailable):
    """Raised by :func:`dispatch_session` when called again before
    ``settings.JULES_DISPATCH_COOLDOWN_SECONDS`` have elapsed since the last
    successful dispatch recorded in the ledger — see this module's "Why
    this is simpler than data/fmp_client.py" docstring section for the full
    reasoning. Subclasses :class:`JulesUnavailable` for the same reason
    :class:`JulesConfirmationRequired`/:class:`JulesApprovalMismatch` do.

    HONESTY: this is friction against an ACCIDENTAL rapid/looping dispatch
    (a retry, a scheduled/unattended session, a bug), not a gate against a
    single deliberate call — waiting out the cooldown (or setting
    ``settings.JULES_DISPATCH_COOLDOWN_SECONDS=0``) trivially clears it.
    """


def _ledger_path() -> Path:
    """Lazy settings read (see module docstring) — never module-level."""
    from settings import settings

    return settings.OUTPUT_DIR / _LEDGER_FILENAME


def _approvals_path() -> Path:
    """Lazy settings read (see module docstring) — never module-level."""
    from settings import settings

    return settings.OUTPUT_DIR / _APPROVALS_FILENAME


_LOCK_FILENAME = "jules_dispatched.jsonl.lock"
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_INTERVAL_SECONDS = 0.05


@contextmanager
def _dispatch_lock() -> Iterator[None]:
    """Cross-process advisory lock guarding two related critical sections in
    this module: (1) the dedup-check → POST → ledger-write sequence in
    :func:`dispatch_session`, closing the TOCTOU race where two concurrent/
    retried calls for the same source/branch/title/prompt on the same day
    could both pass ``_check_dispatch_dedup`` before either one appends to
    the ledger; and (2), since the 2026-09 approval-hash-pinning addition,
    the read-validate-mark-used-save sequence in
    :func:`_consume_dispatch_approval` / :func:`request_dispatch_approval`
    against ``output/jules_pending_approvals.json``. Both are local,
    low-contention, human/agent-cadence state for this same integration —
    reusing one lock file for both avoids a second, parallel locking
    primitive with no real concurrency benefit here.

    Lock mechanism choice: this codebase has no existing ``fcntl``/
    ``filelock`` convention to follow — ``execution/receipts_store.py``,
    ``sizing/cap_audit_store.py``, ``desktop/run_history_store.py``, and
    ``execution/kill_switch.py`` all rely on atomic write-then-rename
    (``os.replace``) for a SINGLE write, not on any file-locking primitive,
    because none of them protects a multi-step check-then-write sequence the
    way this ledger's dedup gate needs to. Per this module's own "no shared
    budget, no concurrency to serialize against" reasoning (see the top-of-
    file docstring) this is a rare-contention, human-cadence case, so rather
    than introduce a first-of-its-kind ``fcntl.flock`` dependency this uses a
    plain stdlib ``O_CREAT | O_EXCL`` lock-file, atomic on the POSIX
    filesystems this macOS/Linux-only codebase runs on.

    Degrades to "proceed without the lock" (never blocks a real dispatch)
    when the lock file itself cannot be created for a reason OTHER than it
    already existing (e.g. a read-only output directory) — matching this
    module's existing OSError-tolerant posture in ``_check_dispatch_dedup``/
    ``_record_dispatch``. A lock that is genuinely held by a concurrent
    dispatch instead raises :class:`JulesUnavailable` after
    ``_LOCK_ACQUIRE_TIMEOUT_SECONDS`` — a stuck lock must never silently wait
    forever, but its failure mode is "ask the human to retry", not "silently
    double-dispatch."
    """
    ledger_path = _ledger_path()
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # best-effort; a real problem surfaces from os.open below

    fd: Optional[int] = None
    held = False
    deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            held = True
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise JulesUnavailable(
                    f"Timed out waiting for the Jules dispatch ledger lock "
                    f"({lock_path}); another dispatch may be in progress. "
                    "Please try again shortly."
                )
            time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        except OSError:
            # Can't create a lock file at all (e.g. unwritable output dir) --
            # degrade to unprotected rather than blocking a real dispatch on
            # a local filesystem problem.
            break
    try:
        yield
    finally:
        if held and fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(lock_path)
            except OSError:
                pass


def _content_hash(source: str, branch: str, title: str, prompt: str) -> str:
    """Full-length sha256 hex digest of the identifying dispatch fields, in
    the exact ``source|branch|title|prompt`` order/format
    :func:`_compute_dedup_key` used before this was extracted out of it —
    the single shared normalization every hash derived from these four
    fields now goes through, so the dedup ledger's truncated hash
    (:func:`_compute_dedup_key`) and the approval-token's full hash
    (:func:`request_dispatch_approval` / :func:`_consume_dispatch_approval`)
    can never silently diverge on what counts as "the same dispatch
    content."
    """
    return hashlib.sha256(f"{source}|{branch}|{title}|{prompt}".encode("utf-8")).hexdigest()


def _compute_dedup_key(source: str, branch: str, title: str, prompt: str) -> str:
    """``{UTC date}:{first 16 hex chars of _content_hash(...)}``.

    Date-scoped exactly like ``execution/receipts_store.py``'s own
    ``dedup_key`` — a different day's identical prompt is a legitimate new
    dispatch, not a duplicate.
    """
    day = time.strftime("%Y-%m-%d", time.gmtime())
    digest = _content_hash(source, branch, title, prompt)[:16]
    return f"{day}:{digest}"


def _check_dispatch_dedup(dedup_key: str) -> bool:
    """Return True if ``dedup_key`` already has a ledger entry (i.e. this
    exact dispatch already happened today). Dead-letter resilient: a
    corrupt/missing ledger degrades to "not a duplicate" (False) rather than
    raising — a read failure here must never itself block a real dispatch."""
    path = _ledger_path()
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("dedup_key") == dedup_key:
                    return True
    except OSError:
        return False
    return False


def _record_dispatch(
    *,
    dedup_key: str,
    source: str,
    branch: str,
    title: str,
    prompt: str,
    session_name: str,
) -> None:
    """Append one record to the dispatch ledger. Best-effort: a write failure
    is swallowed (logged at DEBUG by the caller if desired) rather than
    raised — the real Jules session already exists at this point, so failing
    the caller over a local audit-log write would be strictly worse than a
    missing audit line."""
    path = _ledger_path()
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dedup_key": dedup_key,
        "source": source,
        "branch": branch,
        "title": title,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        "session_name": session_name,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


# ===========================================================================
# Dispatch approval — prompt-hash pinning (see module docstring's dedicated
# section for the full design)
# ===========================================================================


def _load_pending_approvals() -> Dict[str, Any]:
    """Return the full pending-approvals store as a dict, or ``{}`` on any
    read failure.

    FAIL-CLOSED, the opposite tolerance direction from
    :func:`_check_dispatch_dedup`'s fail-open convention, deliberately: the
    dedup ledger is a convenience/idempotency feature where treating a
    corrupt read as "not a duplicate" is the safe default; this store is the
    actual safety gate the approval mechanism exists to enforce, so a read
    failure here must degrade to "no pending approvals exist" — every token
    is correctly treated as unapproved — never to "every token is valid."
    """
    path = _approvals_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # ValueError covers json.JSONDecodeError (a ValueError subclass) --
        # same degrade-safe treatment as list_sources()/dispatch_session()'s
        # own response.json() handling, just fail-closed here instead.
        return {}
    return data if isinstance(data, dict) else {}


def _prune_expired_approvals(approvals: Dict[str, Any]) -> Dict[str, Any]:
    """Drop any entry (used or not) whose ``expires_at`` has passed, so the
    store never grows unbounded across many approval requests. A malformed
    entry (non-dict, or an ``expires_at`` that isn't a real number) is
    dropped too rather than kept in an unparseable state."""
    now = time.time()
    pruned: Dict[str, Any] = {}
    for token, entry in approvals.items():
        if not isinstance(entry, dict):
            continue
        try:
            expires_at = float(entry.get("expires_at", 0))
        except (TypeError, ValueError):
            continue
        if now <= expires_at:
            pruned[token] = entry
    return pruned


def _save_pending_approvals(approvals: Dict[str, Any]) -> None:
    """Atomic write-then-rename (temp file + ``os.replace``), mirroring
    ``execution/receipts_store.py::append_placed``'s convention.

    Unlike that function (and unlike :func:`_record_dispatch` above), this
    does NOT swallow ``OSError`` — a write failure here must be visible to
    the caller. ``_record_dispatch``'s best-effort tolerance is correct
    because the real Jules session it's logging already exists by the time
    it runs; a failed write here, by contrast, IS the entire side effect
    :func:`request_dispatch_approval`/:func:`_consume_dispatch_approval`
    are trying to have — silently losing it would leave a caller believing
    an approval was recorded (or consumed) when it wasn't. Callers translate
    this to :class:`JulesUnavailable` at the public-function boundary.
    """
    path = _approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(approvals, f)
    os.replace(tmp, path)


def request_dispatch_approval(prompt: str, source: str, branch: str, title: str) -> Dict[str, Any]:
    """Records a durable, single-use, prompt-hash-pinned pre-approval for a
    LATER :func:`dispatch_session` call with this EXACT
    ``prompt``/``source``/``branch``/``title``. Returns
    ``{"approval_token": str, "prompt_hash": str, "expires_at": float}``
    (``expires_at`` is a UTC epoch-seconds float).

    Purely local bookkeeping — no network call, and deliberately no
    ``JULES_ENABLED``/``JULES_API_KEY`` gate here (those are re-checked at
    dispatch time regardless, and an approval by itself authorizes nothing
    on its own). The returned ``approval_token`` expires after
    ``settings.JULES_APPROVAL_TTL_SECONDS`` (default 600s / 10 minutes) and,
    once consumed by a :func:`dispatch_session` call (successful or not),
    can never authorize a second dispatch attempt.

    Raises :class:`JulesUnavailable` if the approval cannot be durably
    persisted (see :func:`_save_pending_approvals`).

    HONESTY, stated plainly (see the module docstring's dedicated section
    and ``docs/JULES_INTEGRATION.md`` Sec 4 for the fuller context): this
    closes an "approved X, dispatched Y" content-mismatch risk. It does NOT
    prove a human reviewed this content, and does NOT stop a single agent
    turn from calling this function and :func:`dispatch_session`
    back-to-back with matching content — raises the bar against accidental
    drift, not a deliberate bypass.
    """
    prompt_hash = _content_hash(source, branch, title, prompt)
    now = time.time()

    from settings import settings

    ttl_seconds = float(getattr(settings, "JULES_APPROVAL_TTL_SECONDS", 600))
    expires_at = now + ttl_seconds
    approval_token = secrets.token_urlsafe(24)

    with _dispatch_lock():
        approvals = _load_pending_approvals()
        approvals = _prune_expired_approvals(approvals)
        approvals[approval_token] = {
            "prompt_hash": prompt_hash,
            "source": source,
            "branch": branch,
            "title": title,
            "created_at": now,
            "expires_at": expires_at,
            "used": False,
        }
        try:
            _save_pending_approvals(approvals)
        except OSError as exc:
            raise JulesUnavailable(
                f"Could not persist Jules dispatch approval: {exc}"
            ) from exc

    return {
        "approval_token": approval_token,
        "prompt_hash": prompt_hash,
        "expires_at": expires_at,
    }


def _consume_dispatch_approval(
    approval_token: Optional[str], *, prompt: str, source: str, branch: str, title: str
) -> None:
    """Validate ``approval_token`` against the pending-approvals store and,
    if every check passes, mark it used and persist that immediately — so it
    can authorize at most one dispatch ATTEMPT (whether or not that attempt
    goes on to succeed at the network layer), rather than only "at most one
    successful dispatch." Raises :class:`JulesApprovalMismatch` naming
    EXACTLY which check failed:

    - no ``approval_token`` supplied at all;
    - the token does not match any recorded pending approval (unknown,
      already expired-and-pruned, or never existed);
    - the token's recorded approval has expired (still present but past its
      ``expires_at``);
    - the token has already been used to authorize a prior dispatch attempt
      (single-use);
    - the token is valid and unused, but its recorded ``prompt_hash``
      doesn't match a freshly computed hash of the CURRENT
      ``prompt``/``source``/``branch``/``title`` — the exact swap/drift
      attack this mechanism exists to catch.

    Reuses :func:`_dispatch_lock` (the same advisory lock file the dispatch
    ledger uses) to guard this read-modify-write — see that function's
    docstring for why one lock file covers both critical sections.
    """
    if not approval_token:
        raise JulesApprovalMismatch(
            "dispatch_session() requires a valid approval_token. Call "
            "request_dispatch_approval(prompt, source, branch, title) first "
            "and pass the approval_token it returns -- no approval_token "
            "was supplied."
        )

    current_hash = _content_hash(source, branch, title, prompt)

    with _dispatch_lock():
        approvals = _load_pending_approvals()
        entry = approvals.get(approval_token)
        if not isinstance(entry, dict):
            raise JulesApprovalMismatch(
                "No pending approval found for this approval_token -- it "
                "may have expired, already been consumed, or never existed. "
                "Call request_dispatch_approval(...) again."
            )

        try:
            expires_at = float(entry.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0.0
        if time.time() > expires_at:
            raise JulesApprovalMismatch(
                "This approval_token has expired. Call "
                "request_dispatch_approval(...) again for a fresh one."
            )

        if entry.get("used"):
            raise JulesApprovalMismatch(
                "This approval_token has already been used to authorize a "
                "prior dispatch attempt (single-use). Call "
                "request_dispatch_approval(...) again for a fresh one."
            )

        if entry.get("prompt_hash") != current_hash:
            raise JulesApprovalMismatch(
                "The prompt/source/branch/title being dispatched do not "
                "match what was approved for this approval_token -- "
                "refusing to dispatch. Call request_dispatch_approval(...) "
                "again with the exact content you intend to dispatch."
            )

        entry["used"] = True
        approvals[approval_token] = entry
        approvals = _prune_expired_approvals(approvals)
        try:
            _save_pending_approvals(approvals)
        except OSError:
            # Best-effort here, UNLIKE request_dispatch_approval()'s own
            # persist (which raises): every check above has already passed
            # -- the operator has a valid, matching, unexpired, unused
            # approval AND confirm=True -- so this dispatch is genuinely
            # authorized. Failing it over a local disk hiccup recording
            # "used" would be strictly worse than the narrow residual risk
            # this leaves (this exact token could in principle be replayed
            # if the disk write keeps failing), the same trade-off
            # _record_dispatch's own docstring already makes for the
            # dispatch ledger itself.
            pass


# ===========================================================================
# Dispatch cooldown (see module docstring's "Why this is simpler than
# data/fmp_client.py" section for the full design)
# ===========================================================================


def _last_dispatch_timestamp() -> Optional[float]:
    """UTC epoch seconds of the most recently recorded successful dispatch
    in the ledger, or ``None`` if the ledger is empty/missing/corrupt.

    Fail-OPEN (mirrors :func:`_check_dispatch_dedup`'s own tolerance, NOT
    :func:`_load_pending_approvals`'s fail-closed one): the cooldown is a
    friction mechanism against an accidental rapid/looping dispatch, not a
    safety-critical gate, so a read failure here must never itself block a
    legitimate dispatch.
    """
    path = _ledger_path()
    if not path.exists():
        return None
    last_ts: Optional[str] = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = record.get("ts")
                if isinstance(ts, str) and ts:
                    last_ts = ts  # the ledger is append-only in chronological order
    except OSError:
        return None
    if last_ts is None:
        return None
    try:
        return calendar.timegm(time.strptime(last_ts, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _enforce_dispatch_cooldown() -> None:
    """Raise :class:`JulesDispatchCooldownActive` if called before
    ``settings.JULES_DISPATCH_COOLDOWN_SECONDS`` have elapsed since the last
    successful dispatch recorded in the ledger. Called from
    :func:`dispatch_session` BEFORE any network call and before consuming an
    approval token, so a cooldown-blocked attempt never wastes a one-time
    approval. ``JULES_DISPATCH_COOLDOWN_SECONDS <= 0`` disables this check
    entirely.

    Not lock-protected (unlike the approval/dedup critical sections): a race
    between two concurrent calls both reading the ledger as "cooldown not
    active" is a low-severity residual (this is friction, not a hard
    concurrency-safe gate — every other gate, including the per-token
    single-use approval check, still applies independently).
    """
    from settings import settings

    cooldown = float(getattr(settings, "JULES_DISPATCH_COOLDOWN_SECONDS", 60.0))
    if cooldown <= 0:
        return
    last_ts = _last_dispatch_timestamp()
    if last_ts is None:
        return
    elapsed = time.time() - last_ts
    if elapsed < cooldown:
        remaining = cooldown - elapsed
        raise JulesDispatchCooldownActive(
            f"Jules dispatch cooldown active: {remaining:.1f}s remaining "
            f"(cooldown={cooldown:.0f}s since the last successful dispatch). "
            "This protects against an accidental rapid/looping dispatch, "
            "not against a single deliberate call."
        )


def list_sources() -> Dict[str, Any]:
    """``GET /sources`` — list the GitHub repos connected to this Jules
    account. Raises :class:`JulesUnavailable` if ``JULES_API_KEY`` is unset,
    ``JULES_ENABLED`` is False, or the request fails.

    Returns the raw parsed JSON response (``{"sources": [...]}"``) — no
    reshaping, matching ``data/fmp_client.py``'s "wrappers return raw JSON,
    consumers do the mapping" convention.
    """
    from settings import settings

    if not settings.JULES_ENABLED:
        raise JulesUnavailable(
            "Jules integration is disabled (settings.JULES_ENABLED=False)."
        )
    if not settings.JULES_API_KEY:
        raise JulesUnavailable(
            "JULES_API_KEY is not set (settings.JULES_API_KEY); request skipped."
        )

    url = f"{JULES_BASE_URL}/sources"
    try:
        response = requests.get(
            url,
            headers={
                "X-Goog-Api-Key": settings.JULES_API_KEY,
                "Accept": "application/json",
            },
            timeout=settings.JULES_REQUEST_TIMEOUT_SECONDS,
        )

        status = getattr(response, "status_code", None)
        if status is None or not (200 <= int(status) < 300):
            raise JulesUnavailable(f"Jules returned HTTP {status} for GET /sources.")

        return response.json()
    except requests.RequestException as exc:
        raise JulesUnavailable(f"Jules transport error on GET /sources: {exc}") from exc
    except ValueError as exc:
        # response.json() raises a json.JSONDecodeError (a ValueError
        # subclass, same for stdlib json and simplejson) on a malformed or
        # empty 2xx body. This must degrade the same way a transport error
        # or non-2xx status does (CONSTRAINT #6) rather than escape as a raw
        # JSONDecodeError.
        raise JulesUnavailable(
            f"Jules returned a malformed JSON response for GET /sources: {exc}"
        ) from exc


def format_sources(sources_response: Dict[str, Any]) -> List[Dict[str, str]]:
    """Normalize a raw ``GET /sources`` response body into a flat list of
    ``{"name": str, "owner": str, "repo": str}`` dicts.

    Single shared source-list formatter for every consumer that renders
    ``list_sources()``'s output — previously ``investyo_mcp_server.py``'s
    ``list_jules_sources`` and ``scripts/jules_dispatch.py``'s
    ``_cmd_list_sources`` each reimplemented this parsing independently and
    had already drifted (different fallback strings for an unnamed source).
    ``"unknown"`` is the canonical fallback here (the MCP tool's prior
    choice; the CLI script's prior ``"<unknown>"`` is retired in favor of
    this shared one).

    Tolerates the same edge cases :func:`dispatch_session` itself must
    tolerate: an explicit ``{"sources": null}`` (``.get(...) or []``, not
    ``.get(..., [])`` — the default only applies when the key is absent) and
    a non-dict entry in the list.
    """
    sources = (
        (sources_response.get("sources") or []) if isinstance(sources_response, dict) else []
    )
    normalized: List[Dict[str, str]] = []
    for src in sources:
        if isinstance(src, dict):
            name = src.get("name") or "unknown"
            github_repo = src.get("githubRepo")
            github_repo = github_repo if isinstance(github_repo, dict) else {}
            owner = str(github_repo.get("owner", "?"))
            repo = str(github_repo.get("repo", "?"))
        else:
            name = str(src) if src is not None else "unknown"
            owner = "?"
            repo = "?"
        normalized.append({"name": name, "owner": owner, "repo": repo})
    return normalized


def dispatch_session(
    prompt: str,
    source: str,
    branch: str,
    title: str,
    *,
    force: bool = False,
    confirm: bool = False,
    approval_token: Optional[str] = None,
) -> Dict[str, Any]:
    """``POST /sessions`` — start a Jules session against ``source`` on
    ``branch`` with ``prompt``, in the hardcoded ``AUTO_CREATE_PR`` automation
    mode (see module docstring). Validates ``source`` against a fresh
    :func:`list_sources` call first and raises :class:`JulesUnavailable` if it
    is not in the connected-sources list — a wrong ``source`` means
    dispatching an autonomous coding agent at the WRONG external repo, so
    this must never pass through blind.

    ``confirm`` MUST be exactly ``True`` or this raises
    :class:`JulesConfirmationRequired` immediately, before any network call
    or settings check — this is the central enforcement of the "never
    dispatch without the operator's explicit go-ahead" safety property.
    Every existing caller (``investyo_mcp_server.py``'s ``dispatch_jules_task``,
    ``scripts/jules_dispatch.py``'s ``_cmd_create_session``) ALSO gates on its
    own ``confirm``/``--confirm`` before ever calling this function — that
    caller-side gate is what produces a nice user-facing message instead of
    a raised exception, and stays in place unchanged; this parameter is the
    additional guarantee that a future third caller cannot bypass the gate
    by forgetting its own check.

    ``approval_token`` MUST be a valid, unexpired, not-already-used token
    from a prior :func:`request_dispatch_approval` call for this EXACT
    ``prompt``/``source``/``branch``/``title``, checked immediately after
    the cooldown gate below and before any network call — see
    :func:`_consume_dispatch_approval` for the five distinct failure modes
    it raises :class:`JulesApprovalMismatch` for. This closes the "approved
    X, dispatched Y" content-mismatch risk; see that function's docstring
    and the module docstring's dedicated section for the honest statement
    of what it does and doesn't achieve.

    Before any of the above, ``settings.JULES_DISPATCH_COOLDOWN_SECONDS``
    must have elapsed since the last successful dispatch, or this raises
    :class:`JulesDispatchCooldownActive` — see :func:`_enforce_dispatch_cooldown`.
    Checked first (before consuming the approval) so a cooldown-blocked
    attempt never wastes a one-time approval token.

    Refuses (raises :class:`JulesUnavailable`) if an identical dispatch
    (same UTC day, same source/branch/title/prompt) was already recorded in
    the ledger today, unless ``force=True``. The dedup check, the POST
    itself, and the ledger write are protected end-to-end by
    :func:`_dispatch_lock` so a concurrent/retried call for the same
    dispatch cannot race past the dedup check before either one records it.

    Returns the raw parsed JSON response from ``POST /sessions``.
    """
    if confirm is not True:
        raise JulesConfirmationRequired(
            "dispatch_session() requires confirm=True: dispatching a Jules "
            "session opens a real, unsupervised PR on the target repo. This "
            "must never be set without the operator's explicit go-ahead for "
            "this exact prompt/branch/title."
        )

    # Phase 2 (cooldown) runs first and before any network call, so a
    # cooldown-blocked attempt never burns the one-time approval token
    # checked next.
    _enforce_dispatch_cooldown()

    # Phase 1 (prompt-hash pinning) -- also before any network call.
    _consume_dispatch_approval(
        approval_token, prompt=prompt, source=source, branch=branch, title=title
    )

    from settings import settings

    if not settings.JULES_ENABLED:
        raise JulesUnavailable(
            "Jules integration is disabled (settings.JULES_ENABLED=False)."
        )
    if not settings.JULES_API_KEY:
        raise JulesUnavailable(
            "JULES_API_KEY is not set (settings.JULES_API_KEY); request skipped."
        )

    sources_response = list_sources()
    known_sources = [
        s.get("name")
        for s in (sources_response.get("sources") or [])
        if isinstance(s, dict)
    ]
    if source not in known_sources:
        raise JulesUnavailable(
            f"'{source}' is not in the connected Jules sources: {known_sources}. "
            "Call list_sources() to see what's actually connected."
        )

    with _dispatch_lock():
        dedup_key = _compute_dedup_key(source, branch, title, prompt)
        if not force and _check_dispatch_dedup(dedup_key):
            raise JulesUnavailable(
                f"An identical dispatch (source={source!r}, branch={branch!r}, "
                f"title={title!r}) was already recorded today (dedup_key={dedup_key}). "
                "Pass force=True to dispatch anyway."
            )

        url = f"{JULES_BASE_URL}/sessions"
        body = {
            "prompt": prompt,
            "sourceContext": {
                "source": source,
                "githubRepoContext": {"startingBranch": branch},
            },
            "automationMode": _AUTOMATION_MODE,
            "title": title,
        }
        try:
            response = requests.post(
                url,
                headers={
                    "X-Goog-Api-Key": settings.JULES_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=body,
                timeout=settings.JULES_REQUEST_TIMEOUT_SECONDS,
            )

            status = getattr(response, "status_code", None)
            if status is None or not (200 <= int(status) < 300):
                raise JulesUnavailable(f"Jules returned HTTP {status} for POST /sessions.")

            payload = response.json()
        except requests.RequestException as exc:
            raise JulesUnavailable(f"Jules transport error on POST /sessions: {exc}") from exc
        except ValueError as exc:
            # response.json() raises a json.JSONDecodeError (a ValueError
            # subclass) on a malformed or empty 2xx body -- must degrade to
            # JulesUnavailable the same way a transport error or non-2xx
            # status does (CONSTRAINT #6), not escape as a raw exception.
            raise JulesUnavailable(
                f"Jules returned a malformed JSON response for POST /sessions: {exc}"
            ) from exc

        session_name = ""
        if isinstance(payload, dict):
            session_name = payload.get("name", "") or ""
        _record_dispatch(
            dedup_key=dedup_key,
            source=source,
            branch=branch,
            title=title,
            prompt=prompt,
            session_name=session_name,
        )
        return payload
