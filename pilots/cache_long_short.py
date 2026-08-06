"""pilots/cache_long_short.py — dependency-light read helper for the Cache
Long/Short advisory strategy's Pilots API endpoints.
=============================================================================

Imports only ``settings`` and ``data.cache_long_short_store`` (a plain
SQLAlchemy store, no heavy engine). NEVER imports
``engine.cache_long_short_engine`` -- that module pulls in
``processing_engine``/``pairs_ondemand``, and ``api/pilots_api.py`` is
AST-guarded against importing a heavy engine even transitively-in-intent
(see the ``pilots-endpoint`` skill). All real computation (beta, proxy
correlation, TLH scanning, wash-sale checks) happens in
``main_orchestrator.py``'s settings-gated background worker or in
``api/data_api.py``'s interactive ``POST /data/cache-long-short/simulate``
-- this module only ever reads what those already persisted.

Honesty (CONSTRAINT #4): a disabled flag or empty store degrades to an
honest empty/disabled shape, never a fabricated number. Never raises
(CONSTRAINT #6).
"""
from __future__ import annotations

from typing import Any, Dict, List

from settings import settings
from data.cache_long_short_store import CacheLongShortStore

__all__ = ["get_dashboard", "get_pending_approvals"]


def get_dashboard() -> Dict[str, Any]:
    """Aggregated Cache Long/Short state for the Tax Dashboard screen."""
    if not settings.CACHE_LONG_SHORT_ENABLED:
        return {"status": "disabled"}

    store = CacheLongShortStore()
    return {
        "status": "enabled",
        "tax_bank": store.tax_bank(),
        "exposure": store.exposure_summary(),
    }


def get_pending_approvals() -> List[Dict[str, Any]]:
    """Open tax lots the background TLH scanner has flagged and that haven't
    been approved yet. Real, persisted opportunities only -- never every
    open lot (that would misrepresent lots with no loss at all as pending
    trades)."""
    if not settings.CACHE_LONG_SHORT_ENABLED:
        return []

    store = CacheLongShortStore()
    lots = store.get_pending_tlh_lots()
    return [
        {
            "lot_id": lot.lot_id,
            "position_id": lot.position_id,
            "cost_basis": lot.cost_basis_per_share,
            "unrealized_loss_pct": lot.unrealized_loss_pct,
        }
        for lot in lots
    ]
