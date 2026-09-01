"""pilots/portfolio.py — pure ``AccountSnapshot`` -> PWA ``Portfolio`` reshape.

Extracted out of ``api/pilots_api.py`` (2026-09, code-review fix) into this
dependency-light module so a caller that only needs the reshape logic — no
FastAPI route, no HTTP — never has to import the whole ``api/pilots_api.py``
module to get it. That module constructs a full FastAPI app and transitively
imports a large, heavy module graph (``gui.*``, ``llm.*``, ``ml.*``,
``execution.*``, ``agents.rag_orchestrator``, ...); importing it just for this
one pure dict-reshape forced every non-API caller into its own local/lazy
import workaround (``desktop/orchestrator_daemon.py`` already did this for
``api.pilots_api.app`` itself; ``scripts/export_notebooklm.py`` did the same
for this function specifically). Moving the logic here — stdlib + ``typing``
only — lets every caller import it directly and cheaply instead.

Dependency-light (stdlib + ``typing`` only) — pinned by
``tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light``.
"""
from __future__ import annotations

from typing import Any, Dict, List

__all__ = ["serialize_portfolio"]


def serialize_portfolio(snap: Any) -> Dict[str, Any]:
    """Reshape an ``AccountSnapshot`` into the PWA ``Portfolio`` contract
    (webapp/src/api/types.ts).

    ``AccountSnapshot.to_dict()`` emits ``positions`` as a *dict* keyed by symbol
    with ``quantity``/``average_cost`` field names and carries no
    ``position_count``/``total_unrealized_pl``/``source`` — none of which match
    the frontend's ``Portfolio``/``PortfolioPositionView``. This serializer maps
    them across without touching ``to_dict()`` itself (whose shape is load-bearing
    for the JSON-cache ``from_dict`` round-trip). Every value is read from the real
    snapshot — nothing is fabricated (CONSTRAINT #4); ``source`` is honestly
    ``"db"`` because callers of this function read DB-first via ``HistoricalStore``.
    """
    data = snap.to_dict()
    raw_positions = data.get("positions") or {}
    positions: List[Dict[str, Any]] = []
    total_unrealized_pl = 0.0
    for pos in raw_positions.values():
        upl = pos.get("unrealized_pl")
        if isinstance(upl, (int, float)) and upl == upl:  # skip None / NaN
            total_unrealized_pl += float(upl)
        positions.append(
            {
                "symbol": pos.get("symbol"),
                "qty": pos.get("quantity"),
                "avg_cost": pos.get("average_cost"),
                "current_price": pos.get("current_price"),
                "market_value": pos.get("market_value"),
                "unrealized_pl": pos.get("unrealized_pl"),
                "unrealized_pl_pct": pos.get("unrealized_pl_pct"),
                "name": pos.get("name"),
            }
        )
    return {
        "total_equity": data.get("total_equity"),
        "buying_power": data.get("buying_power"),
        "total_unrealized_pl": total_unrealized_pl,
        "total_dividends": data.get("total_dividends"),
        "position_count": len(positions),
        "positions": positions,
        "fetched_at": data.get("fetched_at"),
        "source": "db",
        "is_stale": snap.is_stale(),
        "age_hours": snap.age_hours(),
    }
