"""
skills/discovery_skill.py
=========================
Claude Code MCP read-only tools for market discovery and screening.
Provides create_scan, run_scan, and update_scan_filters.

``run_scan`` cross-references scan criteria against the live advisory
engine's ``Score`` (Advisory Score) column, RSI, and fundamentals from
the SQLite ``quant_platform.db`` — no hardcoded tickers.

Security: an AST guard prevents any execution/order-submission module
from appearing in the discovery call stack.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Dict, List, Optional

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
    """Query the SQLite DB for tickers with Score >= min_score.

    Returns a list of dicts with keys: symbol, score, rsi, recommendation.
    Dead-letter safe: returns [] on any DB/import error.
    """
    try:
        import sqlite3
        db_path = os.environ.get("DATABASE_URL", "quant_platform.db")
        if db_path.startswith("sqlite:///"):
            db_path = db_path[len("sqlite:///"):]
        if not os.path.exists(db_path):
            logger.warning("discovery_skill: DB not found at '%s'. Run the pipeline first.", db_path)
            return []

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            """
            SELECT symbol, score, rsi, recommendation
            FROM   dashboard
            WHERE  score >= ?
            ORDER  BY score DESC
            """,
            (min_score,),
        ).fetchall()
        conn.close()
        return [
            {"symbol": r[0], "score": r[1], "rsi": r[2], "recommendation": r[3]}
            for r in rows
            if r[0]  # skip null symbols
        ]
    except Exception as exc:
        logger.error("_db_advisory_scores query failed: %s", exc)
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

    # Pull live advisory scores from the DB
    min_score = float(criteria.get("min_score", 0.0))
    candidates = _db_advisory_scores(min_score=min_score)

    # Apply optional criteria filters
    max_rsi = criteria.get("max_rsi")
    if max_rsi is not None:
        candidates = [c for c in candidates if c.get("rsi") is not None and c["rsi"] <= float(max_rsi)]

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
