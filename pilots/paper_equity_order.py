"""
pilots/paper_equity_order.py
============================
Manual equity (stock) paper order executor, used by the Paper Broker screen's
Quick Trade ticket via ``POST /pilots/paper-broker/order``.

This used to be the ``asset_type == "stock"`` branch of
``pilots/paper_broker_options_order.py``. It lives on its own so equity paper
trading has no dependency on the options desk, which is being archived.
The options executor keeps its own copy of the stock branch until that
module is archived; keep the fill/commission math identical in both.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from data.paper_account_store import PaperAccountStore
from pilots.order_sizing import calculate_stock_sizing
from pilots.price_provider import get_current_price

logger = logging.getLogger(__name__)

# Commission model shared with the webapp ticket's preview:
# $0.005 per share, $1.00 minimum.
_COMMISSION_PER_SHARE = 0.005
_COMMISSION_MIN = 1.0


def estimate_commission(qty: float) -> float:
    return max(_COMMISSION_MIN, round(qty * _COMMISSION_PER_SHARE, 2))


def execute_equity_order(
    symbol: str,
    *,
    side: str = "buy",
    quantity: Optional[float] = None,
    dollar_amount: Optional[float] = None,
    order_type: str = "market",
    limit_price: Optional[float] = None,
    is_live: bool = False,
    strategy_id: Optional[str] = None,
    pilot_id: Optional[str] = None,
    provenance: Optional[str] = None,
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fill one equity paper order against ``PaperAccountStore``.

    Never raises. Returns ``{"ok", "order_id", "message"}``.

    Pricing: an explicit positive ``limit_price`` is used as the fill price
    (unchanged pre-split behavior); otherwise the live quote from
    ``pilots.price_provider.get_current_price``. A missing quote rejects the
    order rather than filling at a fabricated price (CONSTRAINT #4).

    ``strategy_id`` defaults to ``"Manual Trade"`` -- the real caller is a human
    at the Quick Trade ticket. ``strategy_id``/``pilot_id``/``provenance`` are
    overrides for an automated caller so its fills aren't misclassified as
    manual (see docs/known_issues/paper_trade_strategy_id_vocabulary.md).
    """
    order_id = client_order_id or f"eq_ord_{uuid.uuid4().hex[:12]}"

    if is_live:
        return {
            "ok": False,
            "order_id": None,
            "message": "Live order execution is disabled in Advisory-Only mode. Please use paper mode.",
        }

    symbol = (symbol or "").strip().upper()
    side = (side or "buy").lower().strip()
    if not symbol:
        return {"ok": False, "order_id": order_id, "message": "Symbol is required."}
    if side not in ("buy", "sell"):
        return {"ok": False, "order_id": order_id, "message": f"Unsupported side {side!r}; use 'buy' or 'sell'."}

    try:
        store = PaperAccountStore()
    except Exception:
        logger.exception("Failed to initialize PaperAccountStore")
        return {"ok": False, "order_id": order_id, "message": "Paper account storage is unavailable. Please try again shortly."}

    fill_price = float(limit_price) if (limit_price and limit_price > 0) else get_current_price(symbol)
    if not fill_price or fill_price <= 0:
        return {
            "ok": False,
            "order_id": order_id,
            "message": f"No live quote available for {symbol}; order rejected rather than filled at a fabricated price.",
        }

    if dollar_amount and dollar_amount > 0:
        qty = calculate_stock_sizing(dollar_amount, fill_price, allow_fractional=True)
    else:
        qty = float(quantity or 1.0)

    if qty <= 0:
        return {"ok": False, "order_id": order_id, "message": "Calculated share quantity must be greater than zero."}

    commission = estimate_commission(qty)
    total_cost = (qty * fill_price) + commission if side == "buy" else (qty * fill_price) - commission

    success = store.apply_fill(
        client_order_id=order_id,
        symbol=symbol,
        side=side,
        qty=qty,
        fill_price=fill_price,
        commission_and_fees=commission,
        strategy_id=strategy_id or "Manual Trade",
        pilot_id=pilot_id,
        provenance=provenance,
    )

    if not success:
        return {
            "ok": False,
            "order_id": order_id,
            "message": f"Order rejected: Insufficient funds or inventory for {side.upper()} {qty:.2f} {symbol}.",
        }

    return {
        "ok": True,
        "order_id": order_id,
        "message": f"Paper stock order filled: {side.upper()} {qty:.2f} shares of {symbol} at ${fill_price:.2f} (Total: ${total_cost:.2f}).",
    }
