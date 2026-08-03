"""Runtime settings store — WRITE PATH.

The other half of :mod:`runtime_flags`. That module reads
``output/runtime_flags.json`` and layers it onto the already-constructed
``settings`` singleton; **nothing in this codebase wrote that file until this
module existed**, which is what made the read path provably inert. This is the
first and only writer.

Two public entry points, both of which NEVER raise (CONSTRAINT #6) and always
return a :class:`WriteResult`::

    write_override("BETA_LOOKBACK_DAYS", 300, actor="pilots_api")
    delete_override("BETA_LOOKBACK_DAYS", actor="pilots_api")

--------------------------------------------------------------------------
Why this module is NOT a stdlib-only leaf (and :mod:`runtime_flags` is)
--------------------------------------------------------------------------
``runtime_flags.py`` is imported BY ``settings.py``, so it can never import
``settings``, ``gui.env_io``, or anything that does. That constraint left one
documented hole, quoted from its own module docstring:

    "this module does not refuse ``gui/env_io.py::SECRET_KEYS`` fields. It
    cannot import that set (``gui.env_io`` imports ``settings``), and
    duplicating an 80-key credential list into a leaf module is a drift hazard
    worse than the gap. Refusing to WRITE a secret into the store belongs in
    the writer, which has no such import restriction."

This module closes exactly that hole. It is only ever called from
request-handling code that runs long after ``settings.py`` has finished
importing (an API endpoint), so it imports ``settings``, ``gui.env_io``,
``settings_keysets``, and ``runtime_flags`` normally. **It must never be
imported by ``settings.py`` or by ``runtime_flags.py``** — that would
reintroduce the circular import the leaf constraint exists to prevent.
AST-enforced by ``tests/test_runtime_flags_writer.py::TestModuleWiring``.

--------------------------------------------------------------------------
The five refusal gates, in order
--------------------------------------------------------------------------
1. **Secrets** (``gui.env_io.SECRET_KEYS``). A credential must never be
   persisted into a plaintext JSON file that the read path will happily load
   into a live process. This gate runs FIRST, before the "is it a real field"
   check — 38 of the 40 secret keys ARE real ``Settings`` fields, so ordering
   is what makes the refusal attributable to secrecy rather than to a
   coincidence.
2. **Bootstrap keys** (``settings_keysets.BOOTSTRAP_KEYS``). The read path
   already refuses to APPLY these; refusing to STORE them too means the file
   never accumulates entries that are silently inert. The refusal reason is
   the per-field string from ``BOOTSTRAP_KEY_REASONS``, not a generic message.

   The two sets are not disjoint: ``DATABASE_URL`` and ``MCP_DATABASE_URL_RO``
   are in BOTH, and gate 1 wins for them, so their refusal reason is the secret
   one. That is the right way round — a DSN can embed credentials, and
   "this is a secret" is both the stronger statement and the one an operator
   must not be encouraged to work around. ``settings_keysets.py``'s own
   ``DATABASE_URL`` reason says as much ("SECRET too (may embed credentials),
   but that is a separate mechanism"). Pinned by
   ``tests/test_runtime_flags_writer.py::TestBootstrapRefusal``.
3. **Unknown field names.** ``Settings`` sets ``extra="ignore"``, so a typo
   would otherwise be stored forever and skipped forever, with nothing to
   show for it.
4. **Validation.** Via ``Settings.__pydantic_validator__.validate_assignment``
   — never ``pydantic.TypeAdapter``, which coerces to the annotated type but
   silently BYPASSES ``@field_validator``s (verified: it passes
   ``ROBINHOOD_EXECUTION_MODE="garbage"`` straight through, where the real
   validator collapses anything outside ``{off, review, live}`` to the inert
   ``off`` precisely so a bad value can never arm live execution). Same
   mechanism as ``runtime_flags.apply_overrides`` and ``api/pilots_api.py``'s
   ``PUT /llm/setting``.
5. **JSON-serializability of the COERCED value.** The store is a JSON file; a
   value that survives validation but cannot be serialized would otherwise
   fail halfway through the write. Defensive in practice — the only live
   ``Settings`` value that is not JSON-safe is ``OUTPUT_DIR`` (a ``PosixPath``),
   and that is already refused by gate 2.

Not a gate, deliberately: ``gui.env_io.ALLOWED_KEYS``. That is the ``.env``
writer's allowlist for a DIFFERENT mechanism (durable, next-launch, unaffected
by ``BOOTSTRAP_KEYS``) — see ``settings_keysets.py``'s own "Relationship to the
OTHER key sets" section. Also not a gate: ``settings_keysets.DANGEROUS_KEYS``.
Those need an operator confirmation step, which is a UI concern belonging to
the calling layer; this module would have no way to tell "the operator
confirmed" from "the caller forgot to ask".

--------------------------------------------------------------------------
Validation happens on a COPY; the live apply is delegated to the read path
--------------------------------------------------------------------------
Gate 4 validates against ``settings.settings.model_copy()``, not the live
singleton. ``model_copy()`` shallow-copies ``__dict__`` (verified: the copy's
``__dict__`` is a distinct object), so ``validate_assignment`` on it runs every
``@field_validator`` and produces the real coerced value while leaving the
singleton untouched.

That matters because of env-pinning. If validation wrote straight to the live
singleton, an ``env_pinned`` write — which by definition must NOT move this
process's value — would already have moved it before we could report otherwise,
and there is no clean way to put it back. Validating on a copy means the live
singleton is mutated by exactly ONE code path: ``runtime_flags.apply_overrides``,
called at the end of a successful write. Precedence (real shell env > store >
``.env`` > default) therefore has a single implementation, and this module
cannot drift from it. It also means a refusal at any gate leaves the process
byte-identical to before the call.

--------------------------------------------------------------------------
Concurrency: what is guaranteed, and what is not
--------------------------------------------------------------------------
Read-modify-write on a shared file races. The rigor here is scoped to this
artifact: ``output/runtime_flags.json`` on a single-operator machine.

*Guaranteed.* A module-level ``threading.RLock`` serializes the entire
read-merge-write-replace sequence, so **concurrent writers inside one process
cannot lose each other's keys**. This is the realistic case: FastAPI runs
``def`` handlers in a threadpool, so two near-simultaneous ``PUT``s from the
PWA are genuinely concurrent threads in the daemon process. The file itself is
replaced via a temp file + ``os.replace`` — the same write-then-rename idiom as
``gui/env_io.py::write_many_atomic`` (which also uses ``os.replace``) and
``execution/kill_switch.py::activate`` (which uses ``Path.rename``) — so a
concurrent READER never observes a partial file.

*Narrowed.* The existing file is re-read from disk INSIDE the lock,
immediately before the merge — never cached from an earlier point in the call —
so a cross-process write that lands before we start merging is preserved.

*Not guaranteed.* There is no file lock, so a cross-PROCESS writer (a Streamlit
session, a CLI) that lands between our read and our ``os.replace`` is
last-writer-wins and its key is lost. Closing that would need an OS-level lock
plus stale-lock recovery, which is disproportionate for a single-operator
artifact edited by hand a few times a day. This is the same residual limitation
``gui/env_io.py::write_many_atomic`` documents for ``.env``, and is not made
worse here.

--------------------------------------------------------------------------
Recovery from a damaged store, and why the two damage modes differ
--------------------------------------------------------------------------
``runtime_flags.load_store`` reports a file-level error for several distinct
conditions. This module splits them, because overwriting is right for one and
destructive for the other:

* **Unparseable / wrong shape** (truncated JSON, a top-level array, ``flags``
  not an object). Nothing can be salvaged programmatically, but the BYTES can
  be: the file is renamed to ``runtime_flags.json.corrupt.<UTC timestamp>``
  before a fresh store is written, and a WARNING names the quarantine path. The
  write proceeds. Nothing is destroyed; an operator can inspect or hand-merge.
* **Unsupported schema version.** NOT corruption — it is a valid file written
  by a NEWER build. Overwriting it with a ``version: 1`` file would destroy
  real, current state that this build simply cannot read. So the write is
  REFUSED (``ok=False``, ``persisted=False``, ``applies="refused"``) and the
  file is left exactly as found.

--------------------------------------------------------------------------
The audit log
--------------------------------------------------------------------------
Every call — accepted or refused — appends exactly one JSON object to
``output/runtime_flags_audit.jsonl`` (a sibling of the store, so redirecting
the store path in a test redirects the audit log with it).

**The audit record never contains the value, even on success.** Not "we are
careful not to log it" — :func:`_append_audit` has no parameter that could
carry it, so leaking one is structurally impossible rather than a matter of
discipline. The reason: a field not formally in ``SECRET_KEYS`` can still
receive sensitive-shaped input (an operator fat-fingering a token into the
wrong field name is exactly how a credential ends up somewhere nobody audits
for credentials). Refusal reasons ARE recorded, and are safe by construction:
they are either fixed strings from this module / ``BOOTSTRAP_KEY_REASONS``, or
pydantic's ``errors()[0]["msg"]``, which describes the constraint
("Input should be a valid integer") without echoing the input. ``str(exc)`` is
never used — it embeds ``input_value=`` (verified in this repo's tests).

*Partial failure.* If the store write succeeds but the audit append fails, the
call still reports ``ok=True`` / ``persisted=True``: the value genuinely IS
persisted and live, and reporting failure would invite a retry that
double-writes. The audit failure is logged at ERROR (louder than every other
log in this module) because a gap in the audit trail is a real integrity
problem — just not a correctness one for the write itself.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import runtime_flags
import settings as settings_module
import settings_keysets
from gui import env_io

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_FILENAME",
    "APPLIES_IMMEDIATELY",
    "APPLIES_NEXT_DAEMON_RESTART",
    "APPLIES_ENV_PINNED",
    "APPLIES_REFUSED",
    "WriteResult",
    "audit_path",
    "write_override",
    "delete_override",
]

#: Sibling of the store file. Git-ignored (``/output/`` is blanket-ignored, and
#: the artifact is also named explicitly in ``.gitignore``).
AUDIT_FILENAME = "runtime_flags_audit.jsonl"

# ---------------------------------------------------------------------------
# `applies` vocabulary
# ---------------------------------------------------------------------------
#: The coerced value is live on THIS process's ``settings`` singleton now.
#:
#: Honest scope: it means the singleton's attribute moved, which is all this
#: module can observe. A field whose consumers captured it into some engine
#: object at construction time still needs a restart before the change has real
#: effect — that per-field classification lives in
#: ``docs/settings_liveness.json`` (78 of 320 fields are ``restart_required``)
#: and is the calling layer's to surface. This module deliberately does not
#: consult it: doing so would put the same classification in two places, and
#: the settings-UI task owns it.
APPLIES_IMMEDIATELY = "immediately"

#: Persisted to the store, but NOT live in this process. Reached when the
#: post-write re-apply could not land the key (e.g. the file was clobbered by
#: another process in the same instant), or when ``delete_override`` could not
#: recompute the reverted value. The stored state is correct and a fresh
#: process will pick it up.
APPLIES_NEXT_DAEMON_RESTART = "next_daemon_restart"

#: Persisted, but a REAL shell export of the same name wins in this process, so
#: the live value did not move. Persisting anyway is deliberate: the override is
#: durable for a future process that is not pinned, and for after the pinning
#: shell export goes away. ``WriteResult.applied_value`` reports the live
#: (env-sourced) value, not what was written, so a caller can see the gap.
APPLIES_ENV_PINNED = "env_pinned"

#: Nothing was written and nothing in this process changed.
APPLIES_REFUSED = "refused"

# Serializes the read-merge-write-replace sequence within one process. RLock
# rather than Lock so a future nested helper cannot self-deadlock.
_WRITE_LOCK = threading.RLock()

# Deliberately reusing runtime_flags' private helper instead of duplicating it.
# It is the single implementation of "describe a rejected value WITHOUT echoing
# it", and two copies of a security-relevant rule is precisely the drift hazard
# that kept SECRET_KEYS out of runtime_flags.py in the first place.
_safe_validation_reason = runtime_flags._safe_validation_reason


@dataclass(frozen=True)
class WriteResult:
    """Outcome of one :func:`write_override` / :func:`delete_override` call.

    Always returned; these functions never raise (CONSTRAINT #6).
    """

    #: The field name the call was about, echoed verbatim.
    key: str
    #: Whether the call did what was asked. ``False`` means nothing was
    #: persisted and nothing in this process changed.
    ok: bool
    #: The value now LIVE on this process's ``settings`` singleton, if ``ok``.
    #: For ``applies="env_pinned"`` this is the env-sourced value that won, NOT
    #: the value that was written — the point is to make the gap visible.
    #: ``None`` on refusal, and on a delete of a name that is not a field.
    applied_value: Optional[Any] = None
    #: Human-readable reason when not ``ok``. NEVER embeds the rejected value.
    #: ``None`` whenever ``ok`` is ``True``, so a caller can safely treat a
    #: non-empty reason as an error to surface.
    reason: Optional[str] = None
    #: Whether ``output/runtime_flags.json`` was actually updated on disk.
    #: ``False`` for every refusal, and for a delete of an absent key.
    persisted: bool = False
    #: One of :data:`APPLIES_IMMEDIATELY`, :data:`APPLIES_NEXT_DAEMON_RESTART`,
    #: :data:`APPLIES_ENV_PINNED`, :data:`APPLIES_REFUSED`.
    applies: str = APPLIES_REFUSED


# ===========================================================================
# Paths
# ===========================================================================


def _resolved_store_path(path: Optional[Any] = None) -> Path:
    """The store file this call will write, with symlinks resolved.

    ``resolve()`` matches ``gui/env_io.py::write_many_atomic``'s
    ``ENV_PATH.resolve()`` — if the store is a symlink, the write must replace
    the file it points AT, not swap the link for a regular file (which would
    silently detach the operator's intended location).
    """
    return Path(runtime_flags.store_path(path)).resolve()


def audit_path(path: Optional[Any] = None) -> Path:
    """Location of the append-only audit log for the store at ``path``.

    Always a sibling of the store file, which is what makes tests isolated for
    free: redirecting the store (via the ``path`` argument or
    ``runtime_flags.PATH_OVERRIDE_ENV_VAR``) redirects the audit log too, so no
    test can append to the operator's real trail.
    """
    return _resolved_store_path(path).with_name(AUDIT_FILENAME)


def _utc_now_iso() -> str:
    """Timezone-aware UTC, ISO 8601 (e.g. ``2026-08-03T12:00:00.123456+00:00``)."""
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# Audit log
# ===========================================================================


def _append_audit(
    store: Path,
    *,
    action: str,
    key: str,
    actor: str,
    ok: bool,
    persisted: bool,
    applies: str,
    reason: Optional[str] = None,
) -> bool:
    """Append exactly one JSON line to the audit log. Returns success.

    Note the signature: there is NO parameter that could carry the value being
    written. That is the mechanism by which "the audit log never records a
    stored value" is guaranteed rather than merely intended — and it is also
    why ``exc_info=True`` below is safe, since no stored value is ever in this
    function's frame to appear in a traceback.

    Never raises. An audit failure must not fail a write that already
    succeeded (see the module docstring's partial-failure note).
    """
    record = {
        "ts": _utc_now_iso(),
        "action": action,
        "key": key,
        "actor": str(actor or ""),
        "ok": bool(ok),
        "persisted": bool(persisted),
        "applies": applies,
    }
    if reason:
        record["reason"] = reason

    target = store.with_name(AUDIT_FILENAME)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Text-mode append; one small line per call. O_APPEND makes concurrent
        # appends of a short line atomic on POSIX, so interleaved writers
        # produce interleaved LINES, never a mangled one.
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return True
    except Exception as exc:
        logger.error(
            "runtime_flags_writer: FAILED to append the audit record for %s to %s "
            "(%s). The store write itself is unaffected — if it reported ok, the "
            "value IS persisted and live; only the audit trail is incomplete.",
            key,
            target,
            type(exc).__name__,
            exc_info=True,
        )
        return False


def _refuse(
    store: Path, key: str, reason: str, actor: str, *, action: str
) -> WriteResult:
    """Build (and audit) a refusal. Nothing on disk or in-process is touched."""
    logger.warning(
        "runtime_flags_writer: refusing to %s %s — %s", action, key, reason
    )
    _append_audit(
        store,
        action=action,
        key=key,
        actor=actor,
        ok=False,
        persisted=False,
        applies=APPLIES_REFUSED,
        reason=reason,
    )
    return WriteResult(
        key=key,
        ok=False,
        applied_value=None,
        reason=reason,
        persisted=False,
        applies=APPLIES_REFUSED,
    )


# ===========================================================================
# Store file I/O
# ===========================================================================


def _read_raw_flags(store: Path) -> dict[str, Any]:
    """The store's ``flags`` object with every entry's ENVELOPE intact.

    ``runtime_flags.load_store`` is the authority on *whether the file is
    usable*, and this module calls it for exactly that. But it answers a
    different question than the merge needs: it UNWRAPS each
    ``{"value": ...}`` envelope and discards the siblings, so rebuilding the
    file from its output would stamp every untouched key with fresh
    ``updated_at``/``updated_by`` metadata — destroying the provenance of
    overrides this call was not asked to modify.

    So the file is re-read here, verbatim, for the merge. Returns ``{}`` on any
    failure; callers only reach this after ``load_store`` already confirmed the
    file is well-formed (or after quarantining it).
    """
    try:
        raw = json.loads(store.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    flags = raw.get("flags")
    if not isinstance(flags, Mapping):
        return {}
    return {str(k): v for k, v in flags.items()}


def _on_disk_version(store: Path) -> Optional[Any]:
    """The store's ``version`` field, or ``None`` if it cannot be determined.

    Used only to tell "written by a newer build" apart from "damaged", which is
    the difference between refusing the write and quarantining the file.
    """
    try:
        raw = json.loads(store.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(raw, Mapping):
        return raw.get("version")
    return None


def _quarantine(store: Path) -> Optional[Path]:
    """Rename a damaged store aside so a fresh one can be written.

    Returns the quarantine path, or ``None`` if the rename failed (in which
    case the caller proceeds and the damaged bytes are lost — logged loudly,
    but a write that can never succeed is worse).
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = store.with_name(f"{store.name}.corrupt.{stamp}")
    suffix = 0
    while dest.exists():
        suffix += 1
        dest = store.with_name(f"{store.name}.corrupt.{stamp}.{suffix}")
    try:
        os.replace(store, dest)
        return dest
    except Exception:
        logger.error(
            "runtime_flags_writer: could not move the damaged store %s aside; "
            "its contents will be replaced.",
            store,
            exc_info=True,
        )
        return None


def _atomic_write_json(store: Path, payload: Mapping[str, Any]) -> None:
    """Write ``payload`` to ``store`` via temp file + ``os.replace``.

    Same write-then-rename idiom as ``gui/env_io.py::write_many_atomic``
    (``os.replace``) and ``execution/kill_switch.py::activate``
    (``Path.rename``): a concurrent reader sees either the old file or the new
    one, never a half-written one. ``os.replace`` rather than ``Path.rename``
    because the destination normally already exists. The temp name carries
    pid + thread id so two writers can never collide on it.

    Raises on failure — the caller converts that into a ``WriteResult``.
    """
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_name(f"{store.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Preserve an existing file's mode, so an operator who chmod'ed the
        # store keeps that on every subsequent write.
        try:
            if store.exists():
                os.chmod(tmp, store.stat().st_mode & 0o7777)
        except OSError:  # pragma: no cover - defensive
            pass
        os.replace(tmp, store)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _merge_and_replace(
    store: Path,
    *,
    upserts: Optional[Mapping[str, Any]] = None,
    deletes: Iterable[str] = (),
) -> tuple[bool, Optional[str]]:
    """Apply ``upserts``/``deletes`` to the store on disk. Caller holds the lock.

    Returns ``(changed, error)``. ``changed`` is ``False`` only when a delete
    targeted a key that was not there — deleting something absent is a clean
    no-op, not a failure. ``error`` is a human-readable string when the write
    was refused or failed, in which case the file is untouched.

    The read happens HERE, inside the lock and immediately before the write, so
    a concurrent update that has already landed is merged rather than clobbered.
    """
    upserts = dict(upserts or {})
    deletes = tuple(deletes)

    existing: dict[str, Any] = {}
    if store.exists():
        # `load_store` is the gate: writer and reader must never disagree about
        # whether a file is usable.
        _, load_error = runtime_flags.load_store(store)
        if load_error is not None:
            version = _on_disk_version(store)
            if version is not None and version != runtime_flags.SCHEMA_VERSION:
                # A valid file from a NEWER build. Overwriting it with a
                # version-1 file would destroy current state this build simply
                # cannot read. Refuse; leave the file exactly as found.
                return False, (
                    f"the runtime settings store on disk declares schema version "
                    f"{version!r}, but this build writes version "
                    f"{runtime_flags.SCHEMA_VERSION}. Refusing to overwrite a "
                    f"store written by a newer build."
                )
            quarantined = _quarantine(store)
            logger.warning(
                "runtime_flags_writer: the runtime settings store at %s is "
                "damaged (%s). It has been moved aside to %s and a fresh store "
                "is being written; no previous override survives this, but the "
                "original bytes are preserved for inspection.",
                store,
                load_error,
                quarantined if quarantined is not None else "<move failed>",
            )
            existing = {}
        else:
            existing = _read_raw_flags(store)

    merged = dict(existing)
    changed = False
    for key in deletes:
        if key in merged:
            del merged[key]
            changed = True
    for key, entry in upserts.items():
        merged[key] = entry
        changed = True

    if not changed:
        return False, None

    payload = {"version": runtime_flags.SCHEMA_VERSION, "flags": merged}
    try:
        _atomic_write_json(store, payload)
    except Exception as exc:
        logger.error(
            "runtime_flags_writer: could not write the runtime settings store at "
            "%s (%s).",
            store,
            type(exc).__name__,
            exc_info=True,
        )
        return False, f"could not write the runtime settings store ({type(exc).__name__})"

    return True, None


# ===========================================================================
# Applying to this process
# ===========================================================================


def _reapply(store: Path) -> Optional[runtime_flags.ApplyReport]:
    """Re-run the READ path against the live singleton. Never raises.

    This is the ONLY place this module mutates ``settings.settings``. Delegating
    keeps precedence (real shell env > store > ``.env`` > default) implemented
    exactly once, in ``runtime_flags``, so the writer cannot drift from the
    reader about which keys are allowed to move.

    It re-applies every key in the store, not just the one just written. That is
    the correct semantic — the store is the source of truth — and it is
    idempotent for keys that were already applied.
    """
    try:
        return runtime_flags.apply_overrides(settings_module.settings, path=store)
    except Exception:  # pragma: no cover - apply_overrides is itself defensive
        logger.warning(
            "runtime_flags_writer: re-applying the runtime settings store failed; "
            "the write is persisted but this process's settings were not "
            "refreshed.",
            exc_info=True,
        )
        return None


def _is_env_pinned(key: str) -> bool:
    """True when a REAL shell export of ``key`` wins over the store.

    Delegates to ``runtime_flags.real_environment_keys``, which subtracts out
    names that are only in ``os.environ`` because some entry point ran
    ``load_dotenv(..., override=False)``.
    """
    try:
        return key.upper() in runtime_flags.real_environment_keys()
    except Exception:  # pragma: no cover - defensive
        # Failing towards "pinned" would wrongly claim the value did not move
        # when it did. Failing towards "not pinned" is checked against the
        # ApplyReport by the caller anyway, so a wrong answer here downgrades
        # to `next_daemon_restart` rather than to a false claim.
        return False


# ===========================================================================
# Public API
# ===========================================================================


def write_override(
    key: str,
    raw_value: Any,
    *,
    actor: str = "",
    path: Optional[Any] = None,
) -> WriteResult:
    """Persist one ``Settings`` field override and apply it to this process.

    Parameters
    ----------
    key:
        A ``Settings.model_fields`` name. Refused if it is a secret
        (``gui.env_io.SECRET_KEYS``), a bootstrap key
        (``settings_keysets.BOOTSTRAP_KEYS``), or not a real field.
    raw_value:
        Any JSON-expressible value. Coerced through the field's own validator,
        so ``"300"`` for an ``int`` field is fine and lands as ``300``.
    actor:
        Free-form provenance recorded as ``updated_by`` in the store and in the
        audit log (e.g. ``"pilots_api"``). Never interpreted.
    path:
        Store location override. Additive to the agreed contract and mirrors
        ``runtime_flags.apply_overrides(..., path=...)``; production callers omit
        it and get ``output/runtime_flags.json``.

    Returns
    -------
    WriteResult
        Always. This function never raises — see CONSTRAINT #6.
    """
    try:
        return _write_override_inner(key, raw_value, actor=actor, path=path)
    except Exception as exc:  # pragma: no cover - the outermost net
        logger.error(
            "runtime_flags_writer: unexpected failure writing %s (%s).",
            key,
            type(exc).__name__,
            exc_info=True,
        )
        return WriteResult(
            key=str(key),
            ok=False,
            applied_value=None,
            reason=f"unexpected writer failure ({type(exc).__name__})",
            persisted=False,
            applies=APPLIES_REFUSED,
        )


def _write_override_inner(
    key: str, raw_value: Any, *, actor: str, path: Optional[Any]
) -> WriteResult:
    store = _resolved_store_path(path)

    # -- Gate 1: secrets ----------------------------------------------------
    # First, before the field-existence check: 38 of the 40 SECRET_KEYS are
    # real Settings fields, so this ordering is what makes the refusal
    # attributable to secrecy rather than to a coincidence of naming.
    if key in env_io.SECRET_KEYS:
        return _refuse(
            store,
            key,
            "secret fields can never be stored in the runtime settings store",
            actor,
            action="write",
        )

    # -- Gate 2: bootstrap keys --------------------------------------------
    if key in settings_keysets.BOOTSTRAP_KEYS:
        return _refuse(
            store,
            key,
            settings_keysets.BOOTSTRAP_KEY_REASONS[key],
            actor,
            action="write",
        )

    # -- Gate 3: must be a real field --------------------------------------
    target = settings_module.settings
    model_cls = type(target)
    if key not in model_cls.model_fields:
        return _refuse(
            store,
            key,
            f"{key!r} is not a Settings field name",
            actor,
            action="write",
        )

    # -- Gate 4: validate + coerce, on a COPY -------------------------------
    # See the module docstring: the live singleton is never mutated here, so a
    # refusal leaves this process byte-identical, and an env-pinned write
    # cannot accidentally move a value it is not allowed to move.
    probe = target.model_copy()
    try:
        model_cls.__pydantic_validator__.validate_assignment(probe, key, raw_value)
    except Exception as exc:
        return _refuse(
            store, key, _safe_validation_reason(exc), actor, action="write"
        )
    coerced = getattr(probe, key)

    # -- Gate 5: the coerced value has to survive a JSON round trip ---------
    try:
        json.dumps(coerced)
    except (TypeError, ValueError):
        return _refuse(
            store,
            key,
            (
                f"the coerced value is not JSON-serializable "
                f"({type(coerced).__name__}); the runtime settings store is a "
                f"JSON file"
            ),
            actor,
            action="write",
        )

    # -- Persist ------------------------------------------------------------
    entry = {
        "value": coerced,
        "updated_at": _utc_now_iso(),
        "updated_by": str(actor or ""),
    }
    with _WRITE_LOCK:
        changed, error = _merge_and_replace(store, upserts={key: entry})
    if error is not None:
        return _refuse(store, key, error, actor, action="write")

    # -- Apply to this process ---------------------------------------------
    report = _reapply(store)
    if _is_env_pinned(key):
        # Persisted for a future, unpinned process — but this process's value
        # did NOT move, and saying "immediately" here would be a false claim.
        applies = APPLIES_ENV_PINNED
        applied_value = getattr(settings_module.settings, key, None)
    elif report is not None and key in report.applied:
        applies = APPLIES_IMMEDIATELY
        applied_value = report.applied[key]
    else:
        # Persisted, but the re-apply did not land it. Should not happen — we
        # just wrote a validated value for an unpinned key — so log it, and
        # report the durable-but-not-live truth rather than guessing.
        applies = APPLIES_NEXT_DAEMON_RESTART
        applied_value = getattr(settings_module.settings, key, None)
        logger.warning(
            "runtime_flags_writer: %s was persisted but is not live in this "
            "process; a fresh process will pick it up.",
            key,
        )

    _append_audit(
        store,
        action="write",
        key=key,
        actor=actor,
        ok=True,
        persisted=bool(changed),
        applies=applies,
    )
    logger.info(
        "runtime_flags_writer: stored override for %s (actor=%r, applies=%s).",
        key,
        str(actor or ""),
        applies,
    )
    return WriteResult(
        key=key,
        ok=True,
        applied_value=applied_value,
        reason=None,
        persisted=bool(changed),
        applies=applies,
    )


def delete_override(
    key: str, *, actor: str = "", path: Optional[Any] = None
) -> WriteResult:
    """Remove one field's stored override and revert this process to its
    ``.env`` / environment / default value.

    The operator's "reset to default" action.

    Unlike :func:`write_override` this applies NO classification gates. Deletion
    can only ever REDUCE what the store overrides, so refusing (say) a secret
    key would achieve nothing except stranding a hand-added entry that the read
    path would keep trying to apply. A key that was never in the store is a
    clean no-op: ``ok=True``, ``persisted=False``.

    The revert cannot be done by re-running the read path — that only APPLIES
    stored keys, it has no memory of what a field held before an override
    landed (which may have been applied in a previous process). So the baseline
    is recomputed by constructing a fresh ``Settings()``, which re-reads real
    env vars and ``.env`` and does NOT consult the store, and that value is
    assigned through the same validated-assignment machinery. It is skipped
    when the override was never in force anyway (bootstrap key, env-pinned
    name, or a name that is not a field).

    Returns
    -------
    WriteResult
        Always. This function never raises — see CONSTRAINT #6.
    """
    try:
        return _delete_override_inner(key, actor=actor, path=path)
    except Exception as exc:  # pragma: no cover - the outermost net
        logger.error(
            "runtime_flags_writer: unexpected failure deleting %s (%s).",
            key,
            type(exc).__name__,
            exc_info=True,
        )
        return WriteResult(
            key=str(key),
            ok=False,
            applied_value=None,
            reason=f"unexpected writer failure ({type(exc).__name__})",
            persisted=False,
            applies=APPLIES_REFUSED,
        )


def _delete_override_inner(
    key: str, *, actor: str, path: Optional[Any]
) -> WriteResult:
    store = _resolved_store_path(path)

    with _WRITE_LOCK:
        changed, error = _merge_and_replace(store, deletes=(key,))
    if error is not None:
        return _refuse(store, key, error, actor, action="delete")

    target = settings_module.settings
    model_cls = type(target)
    is_field = key in model_cls.model_fields

    if not changed:
        # Never there. Nothing to remove, nothing to revert, nothing pending.
        applied_value = getattr(target, key, None) if is_field else None
        _append_audit(
            store,
            action="delete",
            key=key,
            actor=actor,
            ok=True,
            persisted=False,
            applies=APPLIES_IMMEDIATELY,
        )
        return WriteResult(
            key=key,
            ok=True,
            applied_value=applied_value,
            reason=None,
            persisted=False,
            applies=APPLIES_IMMEDIATELY,
        )

    # Removed from the store. Now put this process back to what .env /
    # environment / the field default produce — but only where the override
    # could have been in force in the first place.
    if not is_field:
        applies = APPLIES_IMMEDIATELY
        applied_value = None
    elif key in settings_keysets.BOOTSTRAP_KEYS:
        # The read path never applied it, so nothing to revert.
        applies = APPLIES_IMMEDIATELY
        applied_value = getattr(target, key, None)
    elif _is_env_pinned(key):
        # A real shell export was winning; the live value is already correct.
        applies = APPLIES_ENV_PINNED
        applied_value = getattr(target, key, None)
    else:
        try:
            baseline = getattr(model_cls(), key)
            model_cls.__pydantic_validator__.validate_assignment(
                target, key, baseline
            )
            _reapply(store)
            applies = APPLIES_IMMEDIATELY
            applied_value = getattr(target, key, None)
        except Exception:
            # The entry IS gone from disk, so a fresh process reverts cleanly;
            # this process just keeps the stale value. Report that honestly
            # rather than claiming a revert that did not happen.
            logger.warning(
                "runtime_flags_writer: removed the stored override for %s, but "
                "could not recompute its .env/default value in this process; "
                "the previous value stays live until restart.",
                key,
                exc_info=True,
            )
            applies = APPLIES_NEXT_DAEMON_RESTART
            applied_value = getattr(target, key, None)

    _append_audit(
        store,
        action="delete",
        key=key,
        actor=actor,
        ok=True,
        persisted=True,
        applies=applies,
    )
    logger.info(
        "runtime_flags_writer: removed stored override for %s (actor=%r, "
        "applies=%s).",
        key,
        str(actor or ""),
        applies,
    )
    return WriteResult(
        key=key,
        ok=True,
        applied_value=applied_value,
        reason=None,
        persisted=True,
        applies=applies,
    )
