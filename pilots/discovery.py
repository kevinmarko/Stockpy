"""pilots/discovery.py — read scan-discovered candidates for the Agentic Trading tab.
========================================================================================

Backs ``GET /agentic/discovery``. Reads ``output/scan_candidates.json``, an
artifact this repo's pipeline NEVER writes: the webapp/API cannot reach the
Robinhood MCP (only a live Claude Code session can — see
``execution/queue_builder.py``'s module contract, the same reason
``GET /execution-queue`` never contacts the MCP). Candidates are populated by
the companion ``.claude/skills/agentic-discovery/SKILL.md`` skill, which runs
the operator's configured scans (:class:`pilots.scan_config_store.ScanConfigStore`)
through the Robinhood MCP's ``run_scan``, cross-references each hit against
``engine.advisory.evaluate()`` via the investyo MCP, and writes this file.

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only / persisted-state only** — imports only ``settings`` +
  :mod:`pilots.scan_config_store` (itself stdlib + ``settings`` only) + stdlib.
* **Honesty (CONSTRAINT #4)** — a missing artifact yields an empty candidate
  list with an honest ``reason``; a candidate with no advisory cross-reference
  carries ``action: None`` / ``conviction: None``, never a fabricated value.
* **Never raises (CONSTRAINT #6)** — a missing/corrupt file degrades to empty.
* **Buy-candidates only** — this section exists to surface *new* symbols the
  operator might want to buy, not to review positions they already hold.
  ``engine.advisory.evaluate()`` (the cross-reference the agentic-discovery
  skill records into the file) can legitimately return ``SELL``/``HOLD`` for a
  symbol that isn't held at all — that's the same general-purpose BUY/SELL/HOLD
  read used for reviewing existing positions elsewhere in the platform, and
  "SELL" is meaningless (nothing to exit) for a symbol the operator doesn't
  own. So a raw ``SELL``/``HOLD`` action recorded in the file is filtered out
  here before it reaches the webapp; only ``BUY`` and the honest "not yet
  scored" (``None``) gap are ever returned. This matches the intended contract
  already encoded in the webapp's mock fixture
  (``webapp/src/api/mock.ts``'s ``MOCK_DISCOVERY_CANDIDATES``).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

from pilots.scan_config_store import ScanConfigStore

logger = logging.getLogger(__name__)

__all__ = ["discovery"]

_FILENAME = "scan_candidates.json"
_NO_ARTIFACT_REASON = (
    "No scan candidates yet — run the agentic-discovery skill "
    "(.claude/skills/agentic-discovery/) to populate this from a configured scan."
)
_NO_ENABLED_SCANS_REASON = (
    "No scan candidates yet, and no scan configs are enabled. Add a scan config "
    "in the Agentic Trading tab, then run the agentic-discovery skill."
)


def _default_path() -> Path:
    return settings.OUTPUT_DIR / _FILENAME


def _load_candidates_file(path: Optional[str] = None) -> Optional[dict]:
    p = Path(path) if path else _default_path()
    try:
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("discovery._load_candidates_file: could not read %s: %s", p, exc)
        return None


def _sanitize_candidate(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    symbol = str(raw.get("symbol") or "").upper().strip()
    if not symbol:
        return None
    conviction = raw.get("conviction")
    try:
        conviction = float(conviction) if conviction is not None else None
    except (TypeError, ValueError):
        conviction = None
    action = raw.get("action")
    action = action.upper().strip() or None if isinstance(action, str) else None
    return {
        "symbol": symbol,
        "scan_name": raw.get("scan_name"),
        "scan_reason": raw.get("scan_reason"),
        "action": action,  # BUY/SELL/HOLD or None — never fabricated
        "conviction": conviction,
        "discovered_at": raw.get("discovered_at"),
    }


def discovery(
    limit: Optional[int] = None,
    candidates_path: Optional[str] = None,
    scan_config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ``{generated_at, candidates, scan_configs, reason}``.

    ``limit`` defaults to ``settings.AGENTIC_MAX_CANDIDATES``. ``scan_configs``
    always reflects the live :class:`ScanConfigStore` contents (even when no
    candidates have been discovered yet) so the operator can see/edit what's
    configured before the first scan runs.
    """
    cap = limit if limit is not None else settings.AGENTIC_MAX_CANDIDATES
    scan_configs = ScanConfigStore(path=scan_config_path).list_all()

    obj = _load_candidates_file(candidates_path)
    if obj is None:
        reason = _NO_ARTIFACT_REASON if scan_configs else _NO_ENABLED_SCANS_REASON
        return {
            "generated_at": None,
            "candidates": [],
            "scan_configs": scan_configs,
            "reason": reason,
        }

    raw_candidates = obj.get("candidates")
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    candidates = [c for c in (_sanitize_candidate(r) for r in raw_candidates) if c is not None]
    had_candidates = bool(candidates)
    # Buy-candidates only (see module docstring) — a SELL/HOLD cross-reference
    # is real data (kept in the file for audit) but isn't a buy candidate, so
    # it's dropped here rather than shown as if the operator should act on it.
    candidates = [c for c in candidates if c["action"] in ("BUY", None)]
    candidates = candidates[: max(0, int(cap))]

    if candidates:
        reason = None
    elif had_candidates:
        reason = "Last scan run found candidates, but none scored a BUY signal from the advisory engine."
    else:
        reason = "Last scan run found no candidates."

    return {
        "generated_at": obj.get("generated_at"),
        "candidates": candidates,
        "scan_configs": scan_configs,
        "reason": reason,
    }
