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
        # Option execution (single-leg and multi-leg strategies)
        legs_list = legs or []

        if len(legs_list) > 1:
            # Multi-leg strategy execution
            parsed_legs = []
            signed_prices = []
            strikes = []
            types = set()
            expirations_set = set()

            for idx, leg in enumerate(legs_list):
                contract_data = leg.get("contract", {})
                strike = float(contract_data.get("strike", 0.0))
                strikes.append(strike)
                opt_type = str(leg.get("type", contract_data.get("type", "call"))).upper()
                types.add(opt_type)
                action = str(leg.get("action", side)).lower()

                leg_exp = leg.get("expiration") or contract_data.get("expiration") or expiration
                if not leg_exp:
                    return {
                        "ok": False,
                        "order_id": client_order_id,
                        "message": f"Option expiration is required for leg {idx+1}; order rejected.",
                    }
                expirations_set.add(leg_exp)
                leg_symbol = f"{symbol} {leg_exp} ${strike:.2f} {opt_type}"

                ask = float(contract_data.get("ask", 0.0) or 0.0)
                bid = float(contract_data.get("bid", 0.0) or 0.0)
                last = float(contract_data.get("lastPrice", 0.0) or 0.0)
                mark = last if last > 0 else (ask + bid) / 2 if (ask + bid) > 0 else (ask or bid or 0.0)

                leg_price = ask if (action == "buy" and ask > 0) else (bid if (action == "sell" and bid > 0) else mark)
                if leg_price <= 0:
                    return {
                        "ok": False,
                        "order_id": client_order_id,
                        "message": f"No live quote available for {leg_symbol}; order rejected rather than filled at a fabricated price.",
                    }

                signed_price = leg_price if action == "buy" else -leg_price
                signed_prices.append(signed_price)

                parsed_legs.append({
                    "symbol": leg_symbol,
                    "side": action,
                    "qty": 1.0,  # scaled by contracts below
                    "fill_price": leg_price * 100.0,
                    "strike": strike,
                    "type": opt_type,
                    "expiration": leg_exp,
                    "unit_price": leg_price,
                })

            net_market_price = sum(signed_prices)
            is_net_debit = net_market_price >= 0
            abs_net_price = abs(net_market_price)

            # Limit order marketability check
            if order_type == "limit" and limit_price is not None and limit_price > 0:
                if is_net_debit and net_market_price > limit_price:
                    return {
                        "ok": False,
                        "order_id": client_order_id,
                        "message": (
                            f"Limit price ${limit_price:.2f} not marketable at current net debit "
                            f"${net_market_price:.2f} (Paper broker instant fills marketable orders only)."
                        ),
                    }
                elif not is_net_debit and abs_net_price < limit_price:
                    return {
                        "ok": False,
                        "order_id": client_order_id,
                        "message": (
                            f"Limit price ${limit_price:.2f} not marketable at current net credit "
                            f"${abs_net_price:.2f} (Paper broker instant fills marketable orders only)."
                        ),
                    }
                effective_net_price = limit_price if is_net_debit else -limit_price
            else:
                effective_net_price = net_market_price

            # Detect strategy name and strike width for sizing
            strike_width = None
            if len(strikes) >= 2:
                strike_width = abs(max(strikes) - min(strikes))

            if len(legs_list) == 2:
                if len(expirations_set) > 1:
                    strategy_name = "Calendar Spread"
                elif len(types) == 1:
                    strategy_name = "Vertical Spread"
                else:
                    strategy_name = "Straddle/Strangle"
            elif len(legs_list) == 4:
                strategy_name = "Iron Condor"
            else:
                strategy_name = f"{len(legs_list)}-Leg Strategy"

            from pilots.order_sizing import calculate_multi_leg_option_sizing
            if dollar_amount and dollar_amount > 0:
                contracts = calculate_multi_leg_option_sizing(
                    dollar_amount, effective_net_price, strike_width=strike_width, multiplier=100
                )
                if contracts < 1:
                    return {
                        "ok": False,
                        "order_id": client_order_id,
                        "message": f"Dollar amount ${dollar_amount:.2f} is insufficient for 1 strategy contract.",
                    }
            else:
                contracts = int(quantity or 1)

            if contracts <= 0:
                return {"ok": False, "order_id": client_order_id, "message": "Quantity must be at least 1 contract."}

            commission = 0.65 * contracts * len(legs_list)

            # Update quantities in parsed_legs
            for pl in parsed_legs:
                pl["qty"] = float(contracts)

            if effective_net_price >= 0:
                # Net Debit: cash paid out
                total_cost = (contracts * effective_net_price * 100.0) + commission
                net_cash_impact = -total_cost
                collateral_required = total_cost
                summary_type = f"Debit ${abs(effective_net_price):.2f}/sh"
            else:
                # Net Credit: cash received, margin/collateral reserved
                total_proceeds = (contracts * abs_net_price * 100.0) - commission
                net_cash_impact = total_proceeds
                max_risk_per_sh = (strike_width - abs_net_price) if (strike_width and strike_width > abs_net_price) else abs_net_price
                collateral_required = max_risk_per_sh * 100.0 * contracts
                summary_type = f"Credit ${abs_net_price:.2f}/sh"

            success = store.apply_multi_leg_fill(
                client_order_id=client_order_id,
                symbol=symbol,
                strategy_name=strategy_name,
                contracts=contracts,
                legs=parsed_legs,
                net_cash_impact=net_cash_impact,
                commission_and_fees=commission,
                collateral_required=collateral_required,
            )

            if not success:
                return {
                    "ok": False,
                    "order_id": client_order_id,
                    "message": f"Order rejected: Insufficient funds or collateral for {strategy_name} on {symbol}.",
                }

            return {
                "ok": True,
                "order_id": client_order_id,
                "message": (
                    f"Paper {strategy_name} filled: {contracts} contract(s) on {symbol} at {summary_type} "
                    f"(Net Cash Impact: ${net_cash_impact:.2f}, Commission: ${commission:.2f})."
                ),
            }

        else:
            # Single-leg option execution
            primary_leg = legs_list[0] if legs_list else None

            if primary_leg:
                contract_data = primary_leg.get("contract", {})
                strike = float(contract_data.get("strike", 0.0))
                opt_type = str(primary_leg.get("type", contract_data.get("type", "call"))).upper()
                action = str(primary_leg.get("action", side)).lower()
                leg_exp = primary_leg.get("expiration") or contract_data.get("expiration") or expiration
                if not leg_exp:
                    return {
                        "ok": False,
                        "order_id": client_order_id,
                        "message": "Option expiration is required to identify the contract; order rejected.",
                    }
                order_symbol = f"{symbol} {leg_exp} ${strike:.2f} {opt_type}"

                ask = float(contract_data.get("ask", 0.0) or 0.0)
                bid = float(contract_data.get("bid", 0.0) or 0.0)
                last = float(contract_data.get("lastPrice", 0.0) or 0.0)
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
                        "message": f"Dollar amount ${dollar_amount:.2f} is insufficient for 1 contract (Cost per contract: ${leg_price * 100:.2f}).",
                    }
            else:
                contracts = int(quantity or 1)

            if contracts <= 0:
                return {"ok": False, "order_id": client_order_id, "message": "Quantity must be at least 1 contract."}

            commission = 0.65 * contracts
            fill_price_unit = leg_price * 100.0
            total_cost = (contracts * fill_price_unit) + commission if action == "buy" else (contracts * fill_price_unit) - commission

            success = store.apply_fill(
                client_order_id=client_order_id,
                symbol=order_symbol,
                side=action,
                qty=float(contracts),
                fill_price=fill_price_unit,
                commission_and_fees=commission,
                allow_short=True,
            )

            if not success:
                return {
                    "ok": False,
                    "order_id": client_order_id,
                    "message": f"Order rejected: Insufficient funds or inventory for {action.upper()} {contracts} contract(s) of {order_symbol}.",
                }

            return {
                "ok": True,
                "order_id": client_order_id,
                "message": f"Paper option order filled: {action.upper()} {contracts} contract(s) of {order_symbol} at ${leg_price:.2f}/sh (Total: ${total_cost:.2f}).",
            }

