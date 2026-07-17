"""
llm/status_store.py — last-real-call telemetry for the LLM providers.
======================================================================

The problem this closes: when an LLM key is missing or invalid, analyst
narratives degrade silently to ``None`` (``llm/providers.py`` swallows a 401
into the identical ``None`` as a timeout), and the operator has no visibility.
This module records what actually happened on the last **real** provider call
— written from ``llm/providers.py``'s own except blocks — so the GUI / PWA can
surface a misconfiguration honestly, WITHOUT ever probing a provider (a probe
would spend the operator's money to test a key).

The honesty rule that governs everything here
----------------------------------------------
We never claim "your key is invalid *now*". We claim *"the last real Claude
call was rejected: authentication"* — a past-tense, timestamped fact that
stays true forever. Callers render it that way.

Two staleness bounds, each scoped to the claim it governs
---------------------------------------------------------
* **Key-identity verdicts** (``ok`` / ``auth``) are properties of the KEY —
  true until the key changes. They are bounded by a truncated one-way
  **fingerprint** of the key: a verdict whose fingerprint doesn't match the
  CURRENT key is discarded as ``source="key_rotated"``. Fixing a key therefore
  clears a false alarm INSTANTLY, with zero LLM calls — the whole payoff.
* **Transient verdicts** (``rate_limit`` / ``network`` / ``timeout`` /
  ``schema`` / ``unknown``) are properties of the world at that moment. They
  are bounded by ``settings.LLM_STATUS_MAX_AGE_HOURS``: past that, the record
  is reported ``source="expired"`` and never claimed as current.

Design invariants (all pinned by tests)
----------------------------------------
* **No SDK import.** Failures are classified by ``type(exc).__name__`` and an
  HTTP status read off the exception object — never by importing ``anthropic``
  / ``openai`` / ``google.genai`` (which would defeat the providers' lazy-import
  invariant). Matches ``data/robinhood_portfolio.verify_credentials``'s "only
  logs exception type names" convention.
* **Imports nothing from the ``llm`` package** — a cycle-proof leaf, so
  ``import llm.status_store`` can run from inside ``llm/providers.py`` safely.
* **The key fingerprint never crosses the module boundary.** ``read_status`` /
  ``read_all`` strip it, so it cannot reach an API response by construction —
  not by a reviewer remembering to. The KEY VALUE itself is never persisted,
  logged, or returned; only a truncated one-way digest, and only internally.
* **Never raises** (CONSTRAINT #6): every read/write degrades to an honest
  empty shape or a no-op. ``llm/providers.py`` depends on this — a telemetry
  write must never break the analyst-commentary soft-fail contract.
* **No fabricated data** (CONSTRAINT #4): a provider with no recorded call
  reports ``source="none"`` with every field ``None`` — never a fake "ok".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

LLM_STATUS_FILENAME = "llm_status.json"
_STATUS_VERSION = 1
_FINGERPRINT_LEN = 12  # hex chars → 48 bits: enough to detect rotation, not a key ID

# The three providers this platform can call, and the settings attribute
# holding each one's key. Duplicated here (not imported from
# gui.ai_control_center) to keep this a leaf module with no llm/gui deps.
PROVIDERS: Tuple[str, ...] = ("claude", "gemini", "openai")
_PROVIDER_KEY_ATTR: Dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}

ERROR_KINDS: Tuple[str, ...] = ("auth", "rate_limit", "network", "timeout", "schema", "unknown")

# Surfaced verbatim by GET /llm/status as `telemetry_note` — the
# run_status.HEARTBEAT_ADVISORY_NOTE analogue for the expected-null case.
LLM_STATUS_ADVISORY_NOTE = (
    "Verdicts are recorded from REAL LLM calls only — this platform never "
    "probes a provider to test a key. A null last-call record means no LLM "
    "call has been made with the current key, which is the EXPECTED state "
    "when LLM_COMMENTARY_ENABLED is False (its default) — it does NOT mean "
    "the key is broken. A verdict recorded against a different key is discarded "
    "on rotation and reported as source='key_rotated'. Only an 'auth' verdict "
    "ever indicates a rejected key; a rate-limit/network/timeout is a transient "
    "condition, not a key problem."
)

# Serialises read-modify-write across the orchestrator's ADVISORY_MAX_CONCURRENCY
# worker threads (default 8). Cross-PROCESS races (GUI + daemon) remain, but
# os.replace keeps the file valid — worst case one lost record, self-healing on
# the next call. llm/cache.py lives with the identical cross-process race.
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Classification — pure, no SDK import, never raises
# ---------------------------------------------------------------------------

# anthropic and openai are both Stainless-generated and share these class
# names. We are always inside a KNOWN provider's except block, so there is no
# cross-SDK ambiguity.
_AUTH_TYPES = frozenset({"AuthenticationError", "PermissionDeniedError"})
_RATE_LIMIT_TYPES = frozenset({"RateLimitError"})
_TIMEOUT_TYPES = frozenset(
    {"APITimeoutError", "TimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout"}
)
_NETWORK_TYPES = frozenset(
    {"APIConnectionError", "ConnectionError", "ConnectError", "RemoteProtocolError"}
)
_SCHEMA_TYPES = frozenset({"ValidationError"})

# google.genai has NO auth-specific exception class: a bad key surfaces as
# ClientError(code=400, status="INVALID_ARGUMENT", message="API key not
# valid..."). ONLY that exact, documented Google error reason upgrades a 400 to
# auth; every OTHER 400 stays "unknown" rather than guessing at the operator's
# expense. Verified against google-genai (ClientError.code==400,
# str(exc) contains "API key not valid").
_API_KEY_INVALID_RE = re.compile(r"API_KEY_INVALID|API key not valid", re.IGNORECASE)


def _http_status_of(exc: Any) -> Optional[int]:
    """Best-effort HTTP status off an SDK exception. Never raises.

    Reads ``.status_code`` (anthropic/openai) then ``.code`` (google.genai).
    ``bool`` is an ``int`` subclass, so it is skipped explicitly; google.genai's
    ``.status`` is a STRING ("INVALID_ARGUMENT") and is correctly ignored by the
    ``isinstance(int)`` guard.
    """
    for attr in ("status_code", "code"):
        try:
            v = getattr(exc, attr, None)
        except Exception:  # noqa: BLE001 - a property that raises must not crash us
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    return None


def classify_exception(exc: Any) -> Tuple[str, Optional[int]]:
    """Map an SDK exception to ``(error_kind, http_status)``. Never raises.

    ``error_kind`` is one of :data:`ERROR_KINDS`. Classification order: HTTP
    status first (the most reliable cross-SDK signal), then the type-name map,
    then the google.genai bad-key special case. Anything unrecognised →
    ``"unknown"`` — NEVER a guessed ``"auth"``.
    """
    try:
        name = type(exc).__name__
        status = _http_status_of(exc)
        if status in (401, 403):
            return "auth", status
        if status == 429:
            return "rate_limit", status
        if name in _AUTH_TYPES:
            return "auth", status
        if name in _RATE_LIMIT_TYPES:
            return "rate_limit", status
        if name in _TIMEOUT_TYPES:
            return "timeout", status
        if name in _NETWORK_TYPES:
            return "network", status
        if name in _SCHEMA_TYPES:
            return "schema", status
        # google.genai bad-key path — read str(exc) IN MEMORY to classify; it is
        # never persisted (only the verdict is).
        if status == 400 and name in ("ClientError", "APIError"):
            if _API_KEY_INVALID_RE.search(str(exc) or ""):
                return "auth", status
        return "unknown", status
    except Exception:  # noqa: BLE001 - classification must never raise (e.g. exc=None)
        return "unknown", None


# ---------------------------------------------------------------------------
# Fingerprint + file I/O — the fingerprint is MODULE-PRIVATE and never returned
# ---------------------------------------------------------------------------


def _status_path() -> Path:
    """Resolved live from settings.OUTPUT_DIR so tests can patch it.

    A function (not a module constant) — mirrors llm/cache.py::_cache_path();
    execution/kill_switch.py's frozen module-level path is a known test wart.
    """
    return Path(settings.OUTPUT_DIR) / LLM_STATUS_FILENAME


def _current_fingerprint(provider: str) -> Optional[str]:
    """Truncated one-way digest of the CURRENT key for ``provider``, or None.

    Not "a secret": an API key is 128+ bits of uniform random material, so a
    12-hex-char (48-bit) SHA-256 prefix is not even a unique identifier for it,
    let alone reversible — at most a confirmation oracle for someone who already
    holds the key. It is nonetheless kept MODULE-PRIVATE (stripped by
    read_status/read_all) so it cannot reach an API response by construction.
    """
    attr = _PROVIDER_KEY_ATTR.get(provider)
    if not attr:
        return None
    try:
        raw = getattr(settings, attr, None) or ""
        raw = str(raw).strip()
        if not raw:
            return None
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 - never raise
        return None


def _read_file() -> Dict[str, Any]:
    """Parse the status file into ``{"version", "providers": {...}}``. Never raises."""
    path = _status_path()
    if not path.exists():
        return {"version": _STATUS_VERSION, "providers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != _STATUS_VERSION:
            return {"version": _STATUS_VERSION, "providers": {}}
        providers = data.get("providers")
        if not isinstance(providers, dict):
            return {"version": _STATUS_VERSION, "providers": {}}
        return {"version": _STATUS_VERSION, "providers": providers}
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("status_store._read_file: could not read %s: %s", path, exc)
        return {"version": _STATUS_VERSION, "providers": {}}


def _atomic_write(data: Dict[str, Any]) -> None:
    """Atomic write via temp-file + os.replace (mirrors llm/cache.py::_write_all)."""
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.debug("status_store._atomic_write: parent mkdir failed: %s", exc)
        return
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".llm_status.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("status_store._atomic_write: write failed: %s", exc)


def _write_record(
    provider: str,
    *,
    ok: bool,
    error_kind: Optional[str],
    exception_type: Optional[str],
    http_status: Optional[int],
) -> None:
    """Persist one provider's verdict. Always writes (advances checked_at even
    when the outcome is unchanged, so the age bound reflects the LAST OBSERVED
    time). Never raises."""
    if provider not in _PROVIDER_KEY_ATTR:
        return
    try:
        with _LOCK:
            data = _read_file()
            providers = data.get("providers")
            if not isinstance(providers, dict):
                providers = {}
            providers[provider] = {
                "provider": provider,
                "ok": ok,
                "error_kind": error_kind,
                "exception_type": exception_type,
                "http_status": http_status,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "key_fingerprint": _current_fingerprint(provider),
            }
            _atomic_write({"version": _STATUS_VERSION, "providers": providers})
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("status_store._write_record: failed for %s: %s", provider, exc)


# ---------------------------------------------------------------------------
# Public write API — called from llm/providers.py's except blocks
# ---------------------------------------------------------------------------


def record_success(provider: str) -> None:
    """Record that the last real call to ``provider`` was ACCEPTED (key is good).

    Called the instant the SDK call returns without raising — before any
    parsing — so every downstream ``return None`` (missing block, refusal,
    ``parsed=None``) still clears a stale auth verdict. Never raises.
    """
    _write_record(provider, ok=True, error_kind=None, exception_type=None, http_status=None)


def record_failure(
    provider: str,
    exc: Any = None,
    *,
    error_kind: Optional[str] = None,
) -> None:
    """Record that the last real call to ``provider`` FAILED. Never raises.

    ``error_kind`` overrides classification for sites where the answer is
    already known (the ``ValidationError`` parse handlers pass
    ``error_kind="schema"``). Otherwise the kind is derived from ``exc`` via
    :func:`classify_exception`. Only the verdict is persisted — never the raw
    exception message (which could echo a key).
    """
    http_status: Optional[int] = None
    if exc is not None:
        _kind, http_status = classify_exception(exc)
        if error_kind is None:
            error_kind = _kind
    if error_kind is None:
        error_kind = "unknown"
    exception_type = type(exc).__name__ if exc is not None else None
    # A "schema" verdict carries no meaningful HTTP status (it happened AFTER a
    # 200); keep it None rather than a stray value.
    if error_kind == "schema":
        http_status = None
    _write_record(
        provider,
        ok=False,
        error_kind=error_kind,
        exception_type=exception_type,
        http_status=http_status,
    )


# ---------------------------------------------------------------------------
# Public read API — fingerprint stripped; source names its own provenance
# ---------------------------------------------------------------------------


def _empty_status(provider: str, source: str) -> Dict[str, Any]:
    return {
        "provider": provider,
        "ok": None,
        "error_kind": None,
        "exception_type": None,
        "http_status": None,
        "checked_at": None,
        "age_seconds": None,
        "source": source,
    }


def read_status(provider: str) -> Dict[str, Any]:
    """Return the current last-call verdict for ``provider``. Never raises.

    Always the full shape. ``source`` names the provenance:

    * ``"none"``        — no call has ever been recorded for this provider.
    * ``"key_rotated"`` — a verdict exists but for a DIFFERENT key (the current
      key's fingerprint doesn't match). Every field is nulled — the record isn't
      about the current key at all.
    * ``"expired"``     — a TRANSIENT verdict older than
      ``LLM_STATUS_MAX_AGE_HOURS``. Fields are RETAINED (it's still about your
      key, just old); consumers render it muted, not as a current claim.
    * ``"last_call"``   — a current, claimable verdict.

    The ``key_fingerprint`` is NEVER included — it is stripped here so it cannot
    cross the module boundary.
    """
    try:
        if provider not in _PROVIDER_KEY_ATTR:
            return _empty_status(provider, "none")
        rec = _read_file().get("providers", {}).get(provider)
        if not isinstance(rec, dict):
            return _empty_status(provider, "none")

        stored_fp = rec.get("key_fingerprint")
        current_fp = _current_fingerprint(provider)
        if stored_fp != current_fp:
            # Verdict is about a different key (or the key is now unset).
            return _empty_status(provider, "key_rotated")

        checked_at = rec.get("checked_at")
        age_seconds: Optional[float] = None
        if checked_at:
            try:
                ts = datetime.fromisoformat(checked_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
            except Exception:  # noqa: BLE001 - unparseable timestamp → no age
                age_seconds = None

        ok = rec.get("ok")
        error_kind = rec.get("error_kind")
        # Key-identity verdicts (ok / auth) are bounded by the fingerprint, not
        # the clock. Only TRANSIENT verdicts expire.
        key_bound = (ok is True) or (error_kind == "auth")
        source = "last_call"
        if not key_bound and age_seconds is not None:
            try:
                max_age = float(getattr(settings, "LLM_STATUS_MAX_AGE_HOURS", 24.0)) * 3600.0
            except Exception:  # noqa: BLE001
                max_age = 24.0 * 3600.0
            if max_age > 0 and age_seconds > max_age:
                source = "expired"

        return {
            "provider": provider,
            "ok": ok,
            "error_kind": error_kind,
            "exception_type": rec.get("exception_type"),
            "http_status": rec.get("http_status"),
            "checked_at": checked_at,
            "age_seconds": age_seconds,
            "source": source,
        }
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("status_store.read_status: failed for %s: %s", provider, exc)
        return _empty_status(provider, "none")


def read_all() -> Dict[str, Dict[str, Any]]:
    """Return the last-call verdict for every provider (claude/gemini/openai).

    Never raises; always the full three-key shape. The fingerprint is stripped
    from every record.
    """
    return {p: read_status(p) for p in PROVIDERS}


def clear() -> None:
    """Remove the status file. Test-only; never raises."""
    try:
        _status_path().unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.debug("status_store.clear: %s", exc)
