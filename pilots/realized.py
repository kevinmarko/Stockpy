"""pilots/realized.py — realized broker P&L for the PWA (READ-ONLY, cache-only).
==============================================================================

The Pilots-layer reader that surfaces the account's *realized* performance —
win rate, profit factor, realized P&L and holding stats reconstructed by PURE
FIFO lot-matching of the Robinhood filled-order history — for the mobile
Portfolio screen (``GET /portfolio/realized``).

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only / cache-only** — this runs on a web request path, so it must NEVER
  trigger a live Robinhood TOTP login. It reuses the existing, tested
  ``data.robinhood_orders`` FIFO reconstruction but forces the **cache-only**
  path: an injected empty ``orders_fetcher`` (so no network fetch is attempted)
  plus a no-op ``symbol_resolver`` (so ``robin_stocks`` is never even imported)
  and ``cache_max_age_hours=inf`` (so a warm ``cache/robinhood_orders.json`` of
  any age is served). A cold cache honestly yields an empty (NaN → ``null``)
  summary rather than logging in.
* **Honesty (CONSTRAINT #4)** — the summary's NaN-shaped fields (win rate,
  profit factor, averages when there are no trades) are mapped to ``null``, NEVER
  a fabricated ``0.0``. ``total_realized_pnl`` / ``gross_*`` are genuine sums so
  they stay ``0.0`` over zero trades.
* **Never raises (CONSTRAINT #6)** — every failure degrades to the empty view.

It imports only ``data.robinhood_orders`` (already an allowed light module on the
``api/pilots_api.py`` import path — the AST guard forbids only the six heavy
calculation engines) + ``pilots.scoring`` for the shared ``_coerce_float``
null-shaping helper.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pilots.scoring import _coerce_float

logger = logging.getLogger(__name__)

__all__ = ["realized_performance_view"]

# Cap the closed-trades list returned to the PWA — the summary is the headline;
# the list is a recent-trades feed, not the full ledger.
_MAX_TRADES = 100


def _summary_to_json(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Null-shape a ``realized_pnl_summary`` dict for honest JSON serialization.

    Every NaN float becomes ``None`` (CONSTRAINT #4) so the PWA renders "—"
    rather than a fabricated ``0.0`` — except the genuine sums
    (``total_realized_pnl`` / ``gross_profit`` / ``gross_loss``), which are real
    zeros over an empty trade set, and ``n_trades`` (an int count).
    """
    genuine_zero_keys = {"total_realized_pnl", "gross_profit", "gross_loss"}
    out: Dict[str, Any] = {}
    for key, value in summary.items():
        if key == "n_trades":
            out[key] = int(value) if value is not None else 0
        elif key in genuine_zero_keys:
            coerced = _coerce_float(value)
            out[key] = coerced if coerced is not None else 0.0
        else:
            out[key] = _coerce_float(value)  # NaN / non-finite -> None
    return out


def _trade_to_json(trade: Any, coerce=_coerce_float) -> Dict[str, Any]:
    """Serialize one ``ClosedTrade`` to the PWA row shape (ISO timestamps)."""
    return {
        "symbol": str(getattr(trade, "symbol", "") or "").upper(),
        "quantity": coerce(getattr(trade, "quantity", None)),
        "entry_ts": _iso(getattr(trade, "entry_ts", None)),
        "exit_ts": _iso(getattr(trade, "exit_ts", None)),
        "entry_price": coerce(getattr(trade, "entry_price", None)),
        "exit_price": coerce(getattr(trade, "exit_price", None)),
        "realized_pnl": coerce(getattr(trade, "realized_pnl", None)),
        "return_pct": coerce(getattr(trade, "return_pct", None)),
        "holding_days": coerce(getattr(trade, "holding_days", None)),
    }


def _iso(dt: Any) -> Optional[str]:
    """Best-effort ISO-8601 for a datetime; ``None`` on anything else."""
    try:
        return dt.isoformat() if dt is not None else None
    except Exception:  # noqa: BLE001
        return None


def _empty_view() -> Dict[str, Any]:
    """The honest cold-start view — an empty NaN-shaped summary, no trades."""
    try:
        from data.robinhood_orders import realized_pnl_summary

        summary = _summary_to_json(realized_pnl_summary([]))
    except Exception as exc:  # noqa: BLE001 — never raise
        logger.debug("realized_pnl_summary([]) failed: %s", exc)
        summary = {"n_trades": 0, "total_realized_pnl": 0.0}
    return {"summary": summary, "trades": [], "n_fills": 0, "available": False}


def realized_performance_view(max_trades: int = _MAX_TRADES) -> Dict[str, Any]:
    """Return ``{summary, trades, n_fills, available}`` from the warm order cache.

    ``available`` is ``True`` only when at least one filled order was found in the
    cache (so the PWA can distinguish "no data cached yet" from "you have no
    closed trades"). Newest closed trades first, capped at ``max_trades``.
    Never triggers a Robinhood login; never raises (CONSTRAINT #6).
    """
    try:
        from data.robinhood_orders import (
            fetch_filled_orders,
            reconstruct_closed_trades,
            realized_pnl_summary,
        )

        # Cache-only: empty fetcher + no-op resolver + infinite freshness so a
        # warm cache of ANY age is served and NO network/login is attempted.
        fills = fetch_filled_orders(
            orders_fetcher=lambda: [],
            symbol_resolver=lambda url: None,
            cache_max_age_hours=float("inf"),
        )
    except Exception as exc:  # noqa: BLE001 — dead-letter: degrade to empty view
        logger.debug("realized_performance_view fetch failed: %s", exc)
        return _empty_view()

    try:
        trades = reconstruct_closed_trades(fills)
        summary = _summary_to_json(realized_pnl_summary(trades))
        # Newest first for the feed; cap the list.
        trades_sorted = sorted(
            trades, key=lambda t: getattr(t, "exit_ts", None) or 0, reverse=True
        )
        rows: List[Dict[str, Any]] = [
            _trade_to_json(t) for t in trades_sorted[: max(0, int(max_trades))]
        ]
        return {
            "summary": summary,
            "trades": rows,
            "n_fills": len(fills),
            "available": bool(fills),
        }
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("realized_performance_view reconstruct failed: %s", exc)
        return _empty_view()
