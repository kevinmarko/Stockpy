"""
pilots/paper_broker_options_order.py
====================================
Paper broker execution module for options and underlying stock orders.
Integrates with `data.paper_account_store.PaperAccountStore`,
`pilots.price_provider`, and `pilots.order_sizing`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from data.paper_account_store import PaperAccountStore
from pilots.order_sizing import calculate_option_sizing, calculate_stock_sizing
from pilots.price_provider import get_current_price

logger = logging.getLogger(__name__)


def execute_paper_order(
    symbol: str,
    *,
    asset_type: str = "option",
    side: str = "buy",
    quantity: Optional[float] = None,
    dollar_amount: Optional[float] = None,
    order_type: str = "market",
    limit_price: Optional[float] = None,
    expiration: Optional[str] = None,
    legs: Optional[List[Dict[str, Any]]] = None,
    is_live: bool = False,
) -> Dict[str, Any]:
    """
    Executes a paper order for stock or option contracts, updating PaperAccountStore.
    """
    if is_live:
        return {
            "ok": False,
            "order_id": None,
            "message": "Live order execution is disabled in Advisory-Only mode. Please use paper mode.",
        }

    symbol = symbol.strip().upper()
    client_order_id = f"opt_ord_{uuid.uuid4().hex[:12]}"
    asset_type = (asset_type or "option").lower().strip()
    side = (side or "buy").lower().strip()

    try:
        store = PaperAccountStore()
    except Exception:
        logger.exception("Failed to initialize PaperAccountStore")
        return {"ok": False, "order_id": client_order_id, "message": "Paper account storage is unavailable. Please try again shortly."}

    if asset_type == "stock":
        fill_price = float(limit_price) if (limit_price and limit_price > 0) else get_current_price(symbol)
        if fill_price <= 0:
            return {
                "ok": False,
                "order_id": client_order_id,
                "message": f"No live quote available for {symbol}; order rejected rather than filled at a fabricated price.",
            }

        if dollar_amount and dollar_amount > 0:
            qty = calculate_stock_sizing(dollar_amount, fill_price, allow_fractional=True)
        else:
            qty = float(quantity or 1.0)

        if qty <= 0:
            return {"ok": False, "order_id": client_order_id, "message": "Calculated share quantity must be greater than zero."}

        # Commission: $0.005 / share, min $1.00
        commission = max(1.0, round(qty * 0.005, 2))
        total_cost = (qty * fill_price) + commission if side == "buy" else (qty * fill_price) - commission

        success = store.apply_fill(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            fill_price=fill_price,
            commission_and_fees=commission,
        )

        if not success:
            return {
                "ok": False,
                "order_id": client_order_id,
                "message": f"Order rejected: Insufficient funds or inventory for {side.upper()} {qty:.2f} {symbol}."
            }

        return {
            "ok": True,
            "order_id": client_order_id,
            "message": f"Paper stock order filled: {side.upper()} {qty:.2f} shares of {symbol} at ${fill_price:.2f} (Total: ${total_cost:.2f})."
        }

    else:
        # Option execution
        legs_list = legs or []

        # This paper broker cannot honestly price a multi-leg strategy from a
        # single symbol's quote (matching execution/fmp_paper_broker.py's
        # documented V1 behavior) -- reject rather than silently fill only
        # the first leg while charging commission for all of them.
        if len(legs_list) > 1:
            return {
                "ok": False,
                "order_id": client_order_id,
                "message": (
                    "Multi-leg option orders are not yet supported by the paper broker "
                    "(a single-symbol quote cannot honestly price a spread/condor); "
                    "please submit each leg individually."
                ),
            }

        primary_leg = legs_list[0] if legs_list else None

        if primary_leg:
            contract_data = primary_leg.get("contract", {})
            strike = contract_data.get("strike", 0.0)
            opt_type = primary_leg.get("type", "call").upper()
            action = primary_leg.get("action", side).lower()
            if not expiration:
                return {
                    "ok": False,
                    "order_id": client_order_id,
                    "message": "Option expiration is required to identify the contract; order rejected.",
                }
            order_symbol = f"{symbol} {expiration} ${strike:.2f} {opt_type}"

            ask = contract_data.get("ask", 0.0)
            bid = contract_data.get("bid", 0.0)
            last = contract_data.get("lastPrice", 0.0)
            mark = last if last > 0 else (ask + bid) / 2 if (ask + bid) > 0 else (ask or bid or 0.0)
            leg_price = limit_price if (order_type == "limit" and limit_price and limit_price > 0) else (ask if action == "buy" else (bid if bid > 0 else mark))
            if leg_price <= 0:
                return {
                    "ok": False,
                    "order_id": client_order_id,
                    "message": f"No live quote available for {order_symbol}; order rejected rather than filled at a fabricated price.",
                }
        else:
            order_symbol = f"{symbol}-OPTION"
            if order_type == "limit" and limit_price and limit_price > 0:
                leg_price = limit_price
            else:
                return {
                    "ok": False,
                    "order_id": client_order_id,
                    "message": "No option contract or limit price provided; cannot honestly price this order.",
                }
            action = side

        if dollar_amount and dollar_amount > 0:
            contracts = calculate_option_sizing(dollar_amount, leg_price, multiplier=100)
            if contracts < 1:
                return {
                    "ok": False,
                    "order_id": client_order_id,
                    "message": f"Dollar amount ${dollar_amount:.2f} is insufficient for 1 contract (Cost per contract: ${leg_price * 100:.2f})."
                }
        else:
            contracts = int(quantity or 1)

        if contracts <= 0:
            return {"ok": False, "order_id": client_order_id, "message": "Quantity must be at least 1 contract."}

        # Commission: $0.65 per contract per leg
        commission = 0.65 * contracts * max(1, len(legs_list))
        fill_price_unit = leg_price * 100.0
        total_cost = (contracts * fill_price_unit) + commission if action == "buy" else (contracts * fill_price_unit) - commission

        success = store.apply_fill(
            client_order_id=client_order_id,
            symbol=order_symbol,
            side=action,
            qty=float(contracts),
            fill_price=fill_price_unit,
            commission_and_fees=commission,
        )

        if not success:
            return {
                "ok": False,
                "order_id": client_order_id,
                "message": f"Order rejected: Insufficient funds or inventory for {action.upper()} {contracts} contract(s) of {order_symbol}."
            }

        return {
            "ok": True,
            "order_id": client_order_id,
            "message": f"Paper option order filled: {action.upper()} {contracts} contract(s) of {order_symbol} at ${leg_price:.2f}/sh (Total: ${total_cost:.2f})."
        }
