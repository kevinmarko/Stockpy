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

Scaffold note
-------------
This module currently ships with the real interface (this docstring, the
exception, the dedup-ledger helpers) but ``list_sources``/``dispatch_session``
raise ``NotImplementedError`` as a scaffold — see the parallel implementation
task that replaces these bodies with the real HTTP calls. Every other
consumer of this module (the MCP tools, the CLI script, their tests) is
written against these exact signatures and does not need the real bodies to
exist to be correct.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

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


def _ledger_path() -> Path:
    """Lazy settings read (see module docstring) — never module-level."""
    from settings import settings

    return settings.OUTPUT_DIR / _LEDGER_FILENAME


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
    raise NotImplementedError(
        "scaffold — data/jules_client.py:list_sources is not yet implemented"
    )


def dispatch_session(
    prompt: str,
    source: str,
    branch: str,
    title: str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """``POST /sessions`` — start a Jules session against ``source`` on
    ``branch`` with ``prompt``, in the hardcoded ``AUTO_CREATE_PR`` automation
    mode (see module docstring). Validates ``source`` against a fresh
    :func:`list_sources` call first and raises :class:`JulesUnavailable` if it
    is not in the connected-sources list — a wrong ``source`` means
    dispatching an autonomous coding agent at the WRONG external repo, so
    this must never pass through blind.

    Refuses (raises :class:`JulesUnavailable`) if an identical dispatch
    (same UTC day, same source/branch/title/prompt) was already recorded in
    the ledger today, unless ``force=True``. On success, appends a record to
    the ledger before returning.

    Returns the raw parsed JSON response from ``POST /sessions``.
    """
    raise NotImplementedError(
        "scaffold — data/jules_client.py:dispatch_session is not yet implemented"
    )
