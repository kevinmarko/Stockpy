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
has exactly ONE consumer — one MCP tool (``investyo_mcp_server.py``) plus one
CLI script (``scripts/jules_dispatch.py``) — invoked by a human, at human
cadence, at most a handful of times a day. There is no shared budget to
protect and no concurrency to serialize against, so this module has no
throttle and no cooldown breaker. If Jules ever grows a second, high-frequency
consumer, THAT is the point to add FMP-style shared-limiter machinery — not a
missing piece today.

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

import hashlib
import json
import os
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


def _ledger_path() -> Path:
    """Lazy settings read (see module docstring) — never module-level."""
    from settings import settings

    return settings.OUTPUT_DIR / _LEDGER_FILENAME


_LOCK_FILENAME = "jules_dispatched.jsonl.lock"
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_INTERVAL_SECONDS = 0.05


@contextmanager
def _dispatch_lock() -> Iterator[None]:
    """Cross-process advisory lock guarding the dedup-check → POST → ledger-
    write sequence in :func:`dispatch_session`, closing the TOCTOU race where
    two concurrent/retried calls for the same source/branch/title/prompt on
    the same day could both pass ``_check_dispatch_dedup`` before either one
    appends to the ledger.

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


def _compute_dedup_key(source: str, branch: str, title: str, prompt: str) -> str:
    """``{UTC date}:{first 16 hex chars of a sha256 of the identifying fields}``.

    Date-scoped exactly like ``execution/receipts_store.py``'s own
    ``dedup_key`` — a different day's identical prompt is a legitimate new
    dispatch, not a duplicate.
    """
    day = time.strftime("%Y-%m-%d", time.gmtime())
    digest = hashlib.sha256(f"{source}|{branch}|{title}|{prompt}".encode("utf-8")).hexdigest()[:16]
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
