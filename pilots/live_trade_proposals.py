"""pilots/live_trade_proposals.py — dependency-light read helper for the
live-trade human-approval gate's Pilots API endpoints.
=============================================================================

Imports only ``execution.live_trade_proposals_store`` (a plain persistence
store, no heavy engine) -- never a heavy engine, matching the same
AST-guard discipline documented in ``pilots/cache_long_short.py`` and
``pilots/paper_broker.py``.

Honesty (CONSTRAINT #6): never raises -- degrades to ``[]`` on any failure,
matching every other read helper in ``pilots/``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["get_pending_proposals"]


def _serialize(proposal: Any) -> Dict[str, Any]:
    """Plain-dict, JSON-safe serialization of a single ``LiveTradeProposal``
    row/object. Datetime fields are rendered via ``.isoformat()`` (matching
    ``pilots/paper_broker.py``'s/``pilots/cache_long_short.py``'s convention
    of never handing a raw ``datetime`` back through the Pilots API)."""

    def _iso(value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            return isoformat()
        return value

    return {
        "token": proposal.token,
        "symbol": proposal.symbol,
        "side": proposal.side,
        "qty": proposal.qty,
        "order_type": proposal.order_type,
        "limit_price": proposal.limit_price,
        "strategy_id": proposal.strategy_id,
        "proposed_at": _iso(getattr(proposal, "proposed_at", None)),
        "expires_at": _iso(getattr(proposal, "expires_at", None)),
        "status": proposal.status,
        "approved_at": _iso(getattr(proposal, "approved_at", None)),
        "approved_by": getattr(proposal, "approved_by", None),
        "broker_order_id": getattr(proposal, "broker_order_id", None),
        "error_message": getattr(proposal, "error_message", None),
    }


def get_pending_proposals(limit: int = 50) -> List[Dict[str, Any]]:
    """Pending live-trade proposals awaiting human approve/reject. Never
    raises (CONSTRAINT #6) -- degrades to an empty list on any failure."""
    try:
        from execution.live_trade_proposals_store import LiveTradeProposalStore

        store = LiveTradeProposalStore(readonly=True)
        return [_serialize(p) for p in store.get_pending(limit=limit)]
    except Exception:
        return []
