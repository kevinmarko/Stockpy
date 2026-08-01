"""
skills/discovery_skill.py
=========================
Claude Code MCP read-only tools for market discovery and screening.
Provides create_scan, run_scan, and update_scan_filters.

``run_scan`` cross-references scan criteria against the live advisory
engine's per-symbol ``score``/``action`` output in
``output/state_snapshot.json`` — no hardcoded tickers. This is the same
file the Pilots API's ``/state`` endpoint and the GUI Observability tab
read (see ``api/state_api.py``), not a raw SQL table: this codebase has no
live, actively-written SQL table holding a per-cycle dashboard (the legacy
``DailySignals`` table defined in ``database_setup.py`` is not populated by
either orchestrator — the real per-cycle signal output only ever reaches
Google Sheets, the HTML report, and this JSON snapshot). RSI is NOT
filterable here — it is not one of the fields the advisory engine writes
into the snapshot (see ``reporting/state_snapshot.py``); a ``max_rsi``
criterion is accepted but logged as unsupported rather than silently
no-op'd or fabricated.

Security: an AST guard prevents any execution/order-submission module
from appearing in the discovery call stack.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("InvestyoDiscovery")
    _MCP_AVAILABLE = True
except ImportError:
    mcp = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False
    logger.warning("mcp not installed — discovery_skill MCP tools unavailable.")

# In-process scan registry
_SCANS: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# AST / call-stack execution guard
# ---------------------------------------------------------------------------

_FORBIDDEN_MODULES = {
    "alpaca_broker.py",
    "order_manager.py",
    "queue_builder.py",
    "compose.py",
}

def _ast_guard_no_submission() -> None:
    """Raise if any order-submission module is in the call stack."""
    for frame in inspect.stack():
        basename = os.path.basename(frame.filename)
        if basename in _FORBIDDEN_MODULES:
            raise RuntimeError(
                f"AST Guard violation: discovery_skill called from restricted "
                f"module '{basename}'. Order-submission code is forbidden here."
            )


# ---------------------------------------------------------------------------
# Scan storage helpers
# ---------------------------------------------------------------------------

def _db_advisory_scores(min_score: float = 0.0) -> List[Dict[str, Any]]:
    """Read the live advisory engine's per-symbol scores from
    ``output/state_snapshot.json`` for symbols with ``score >= min_score``.

    Returns a list of dicts with keys: symbol, score, recommendation (the
    advisory engine's actual BUY/SELL/HOLD action). Dead-letter safe:
    returns [] when the snapshot is absent, unreadable, or malformed —
    matching api/state_api.py's own read pattern for this same file.
    """
    try:
        path = settings.OUTPUT_DIR / "state_snapshot.json"
        if not path.exists():
            logger.warning(
                "discovery_skill: no state snapshot at '%s'. Run the pipeline first.", path
            )
            return []

        snapshot = json.loads(path.read_text(encoding="utf-8"))
        signals = snapshot.get("signals") or []
        return [
            {
                "symbol": str(sig["symbol"]),
                "score": float(sig.get("score", 0.0) or 0.0),
                "recommendation": sig.get("action") or None,
            }
            for sig in signals
            if sig.get("symbol") and float(sig.get("score", 0.0) or 0.0) >= min_score
        ]
    except Exception as exc:
        logger.error("_db_advisory_scores: failed to read state snapshot: %s", exc)
        return []


# ---------------------------------------------------------------------------
# MCP-exposed tools
# ---------------------------------------------------------------------------

def create_scan(scan_id: str, criteria: Dict[str, Any]) -> str:
    """Register a new market scan configuration."""
    _ast_guard_no_submission()
    _SCANS[scan_id] = criteria
    logger.info("Scan '%s' created: %s", scan_id, criteria)
    return f"Scan '{scan_id}' created with criteria: {json.dumps(criteria)}"


def update_scan_filters(scan_id: str, new_criteria: Dict[str, Any]) -> str:
    """Update the filter criteria for an existing scan."""
    _ast_guard_no_submission()
    if scan_id not in _SCANS:
        return f"Error: scan '{scan_id}' not found. Call create_scan first."
    _SCANS[scan_id].update(new_criteria)
    logger.info("Scan '%s' updated: %s", scan_id, _SCANS[scan_id])
    return f"Scan '{scan_id}' updated: {json.dumps(_SCANS[scan_id])}"


def run_scan(scan_id: str) -> str:
    """Run a scan and cross-reference with the live advisory engine scores.

    Writes output to ``output/scan_candidates.json``.
    """
    _ast_guard_no_submission()

    if scan_id not in _SCANS:
        return f"Error: scan '{scan_id}' not found. Call create_scan first."

    criteria = _SCANS[scan_id]

    # Pull live advisory scores from the state snapshot
    min_score = float(criteria.get("min_score", 0.0))
    candidates = _db_advisory_scores(min_score=min_score)

    # max_rsi is NOT applied — RSI isn't one of the fields the advisory
    # engine writes into state_snapshot.json (see module docstring). Log
    # rather than silently ignore, so a scan config author isn't misled
    # into thinking this criterion did anything.
    if criteria.get("max_rsi") is not None:
        logger.warning(
            "Scan '%s': 'max_rsi' criterion is not supported (no RSI field "
            "in the live state snapshot) — ignored.",
            scan_id,
        )

    recommendation_filter = criteria.get("recommendation")
    if recommendation_filter:
        candidates = [c for c in candidates if c.get("recommendation") == recommendation_filter]

    symbol_filter: Optional[List[str]] = criteria.get("symbols")
    if symbol_filter:
        allowed = {s.upper() for s in symbol_filter}
        candidates = [c for c in candidates if c["symbol"].upper() in allowed]

    # Attach the criteria used so results are self-documenting
    output = {
        "scan_id": scan_id,
        "criteria": criteria,
        "candidates": candidates,
    }

    out_dir = "output"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "scan_candidates.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("Scan '%s' complete: %d candidates → %s", scan_id, len(candidates), out_path)
    return (
        f"Scan '{scan_id}' complete. Found {len(candidates)} candidates. "
        f"Results written to {out_path}."
    )


# ---------------------------------------------------------------------------
# Register tools with FastMCP if available
# ---------------------------------------------------------------------------

if _MCP_AVAILABLE and mcp is not None:
    mcp.tool()(create_scan)
    mcp.tool()(update_scan_filters)
    mcp.tool()(run_scan)

    if __name__ == "__main__":
        mcp.run()
