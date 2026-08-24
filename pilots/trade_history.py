"""pilots/trade_history.py — durable, paginated broker closed-trade history
for the PWA's dedicated Trade History screen.

Distinct from ``pilots/realized.py``, which is deliberately cache-only
(``cache/robinhood_orders.json``, capped at 100 trades) and feeds the
Portfolio screen's "Realized performance" SUMMARY panel. This module reads
the DURABLE store instead (``data.broker_fills_store.BrokerFillsStore``, fed
by the Robinhood login worker's orders ingest — see
``data/broker_fills_store.py`` and ``data/robinhood_login_worker.py``), so it
survives cache eviction and supports real pagination/filtering over the FULL
persisted history rather than a capped recent-trades feed.

Reuses ``pilots.realized``'s null-shaping helpers (``_summary_to_json`` /
``_trade_to_json``) rather than re-deriving the same NaN->null rules twice
(CONSTRAINT #7 — single source of truth).

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only** — never writes anything; constructs
  ``BrokerFillsStore(readonly=True)``.
* **Honesty (CONSTRAINT #4)** — ``summary`` is computed over the FULL
  filtered history, not just the returned page, so paging never changes the
  reported win rate / profit factor. NaN fields serialize to ``null``, never
  a fabricated ``0.0``.
* **Never raises (CONSTRAINT #6)** — every failure degrades to the empty view.

Dependency-light by design (stdlib + ``pilots`` + ``data`` only, both lazily
imported) so it stays inside ``tests/test_pilots_strategy_matrix.py``'s
auto-discovered dependency-light allowlist for ``pilots/*.py`` modules.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pilots.realized import _summary_to_json, _trade_to_json

logger = logging.getLogger(__name__)

__all__ = ["trade_history_view"]

# Hard upper bound on a single page -- protects the endpoint from an
# unbounded ?limit= query parameter.
_MAX_PAGE_SIZE = 500
_DEFAULT_PAGE_SIZE = 50


def _empty_view(limit: int, offset: int) -> Dict[str, Any]:
    """The honest cold-start / failure view -- empty page, NaN-shaped
    summary, ``available: False``."""
    try:
        from data.robinhood_orders import realized_pnl_summary

        summary = _summary_to_json(realized_pnl_summary([]))
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.debug("trade_history_view: empty-summary shaping failed: %s", exc)
        summary = {"n_trades": 0, "total_realized_pnl": 0.0}
    return {
        "trades": [],
        "summary": summary,
        "total": 0,
        "limit": limit,
        "offset": offset,
        "symbols": [],
        "available": False,
        "source": "durable_store",
        "last_ingested_at": None,
    }


def trade_history_view(
    *, limit: int = _DEFAULT_PAGE_SIZE, offset: int = 0, symbol: Optional[str] = None
) -> Dict[str, Any]:
    """Return ``{trades, summary, total, limit, offset, symbols, available,
    source, last_ingested_at}`` from the durable broker-fills store.

    ``summary`` is computed over the FULL filtered history (not just the
    returned page), so page 2 reports the same win rate/profit factor as
    page 1. ``trades`` is newest-exit-first, sliced to
    ``[offset, offset+limit)``. ``symbols`` is every distinct symbol with at
    least one persisted fill, for a filter control -- unaffected by the
    ``symbol`` filter itself. ``available`` is ``True`` only when the store
    has at least one persisted fill (distinguishing "no data ingested yet"
    from "you have no closed trades"). Never triggers a Robinhood login;
    never raises (CONSTRAINT #6).
    """
    limit = max(1, min(int(limit), _MAX_PAGE_SIZE))
    offset = max(0, int(offset))
    symbol = (symbol or "").strip().upper() or None

    try:
        from data.broker_fills_store import BrokerFillsStore

        store = BrokerFillsStore(readonly=True)
    except Exception as exc:  # noqa: BLE001 - dead-letter: degrade to empty view
        logger.debug("trade_history_view: store construction failed: %s", exc)
        return _empty_view(limit, offset)

    try:
        from data.robinhood_orders import realized_pnl_summary

        all_trades = store.closed_trades(symbol=symbol)  # newest-exit-first, unpaginated
        total = len(all_trades)
        page = all_trades[offset : offset + limit]

        summary = _summary_to_json(realized_pnl_summary(all_trades))
        rows: List[Dict[str, Any]] = [_trade_to_json(t) for t in page]

        n_fills = len(store.all_fills())
        last_ingested = store.last_ingested_at()

        return {
            "trades": rows,
            "summary": summary,
            "total": total,
            "limit": limit,
            "offset": offset,
            "symbols": store.distinct_symbols(),
            "available": n_fills > 0,
            "source": "durable_store",
            "last_ingested_at": last_ingested.isoformat() if last_ingested else None,
        }
    except Exception as exc:  # noqa: BLE001 - dead-letter: degrade to empty view
        logger.debug("trade_history_view: read failed: %s", exc)
        return _empty_view(limit, offset)
