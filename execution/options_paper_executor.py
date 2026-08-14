"""Automated Strategy Options Paper Trading Executor.

Identifies gate-passing options strategy directives (Put Credit Spreads, Iron Condors,
Bull Call Spreads, etc.) from technical_options_engine and automatically executes them
into the paper broker with atomic fills, contract sizing, and position deduplication.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from data.paper_account_store import PaperAccountStore, PaperOrder, PaperPosition
from db_config import session_scope
from execution.options_queue_builder import (
    CONFIG as OQB_CONFIG,
    _directive_for_symbol,
    _leg_dicts,
    _resolve_symbols,
    passes_premium_gate,
)
from pilots.options_risk import parse_option_symbol
from pilots.order_sizing import calculate_multi_leg_option_sizing
from settings import settings

logger = logging.getLogger(__name__)


def _calculate_default_expiration(target_dte: int = 30) -> str:
    """Calculates target expiration date string (YYYY-MM-DD) target_dte days in future on a Friday."""
    target = datetime.now(timezone.utc).date() + timedelta(days=target_dte)
    # Adjust to closest Friday (weekday 4)
    weekday = target.weekday()
    days_to_friday = (4 - weekday) % 7
    if days_to_friday > 3:
        days_to_friday -= 7
    friday = target + timedelta(days=days_to_friday)
    return friday.strftime("%Y-%m-%d")


def _price_option_contract(
    spot: float,
    strike: float,
    dte: int,
    opt_type: str,
    r: Optional[float] = None,
    sigma: float = 0.30,
) -> float:
    """Calculates Black-Scholes unit price for an option contract ($/contract, multiplier=100)."""
    if r is None:
        r = float(getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))

    if dte <= 0:
        if str(opt_type).upper() == "CALL":
            intrinsic = max(0.0, spot - strike)
        else:
            intrinsic = max(0.0, strike - spot)
        return max(0.01, round(intrinsic, 4)) * 100.0

    t_years = max(1, dte) / 365.0
    from scipy.stats import norm

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)

    if str(opt_type).upper() == "CALL":
        bs_price = spot * norm.cdf(d1) - strike * math.exp(-r * t_years) * norm.cdf(d2)
    else:
        bs_price = strike * math.exp(-r * t_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)

    return max(0.01, round(bs_price, 4)) * 100.0


class OptionsPaperExecutor:
    """Executes quantitative strategy option directives directly into the paper broker."""

    def __init__(self, store: Optional[PaperAccountStore] = None):
        self.store = store or PaperAccountStore()

    def get_actionable_directives(
        self,
        run_result: Any = None,
        symbols: Optional[List[str]] = None,
        market: Any = None,
        macro_dto: Optional[Any] = None,
        vrp: Optional[float] = None,
        target_dte: int = 30,
    ) -> List[Dict[str, Any]]:
        """Scans universe for gate-passing, actionable option strategy directives."""
        if symbols is None:
            if run_result is not None:
                symbols = _resolve_symbols(run_result)
            else:
                symbols = []

        if not symbols:
            # Fallback to watchlist or default tickers
            raw = getattr(settings, "WATCHLIST", "") or ""
            symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]

        if market is None:
            try:
                from data.market_data import get_provider
                market = get_provider()
            except Exception as exc:
                logger.warning("OptionsPaperExecutor: failed to get market provider: %s", exc)
                market = None

        actionable = []
        for sym in symbols:
            try:
                directive = _directive_for_symbol(
                    sym,
                    market=market,
                    macro_dto=macro_dto,
                    vrp=vrp,
                    target_dte=target_dte,
                )
                if not directive:
                    continue

                strategy = str(directive.get("Strategy", "Cash"))
                action = str(directive.get("Action", "Wait"))
                if strategy.lower() == "cash" or action.lower() == "wait":
                    continue

                passed, reasons = passes_premium_gate(
                    directive,
                    macro_dto=macro_dto,
                    vrp=vrp,
                    config=OQB_CONFIG,
                )
                if not passed or not directive.get("Integrity_OK", False):
                    continue

                legs = _leg_dicts(directive, target_dte)
                if not legs:
                    continue

                actionable.append({
                    "symbol": sym,
                    "strategy": strategy,
                    "action": action,
                    "directive": directive,
                    "legs": legs,
                    "net_premium": directive.get("Net_Premium"),
                    "ivr": directive.get("True_IVR") if math.isfinite(directive.get("True_IVR", float("nan"))) else directive.get("IVR_Proxy"),
                    "trend_bias": directive.get("Trend_Bias", "Neutral"),
                    "target_dte": target_dte,
                })
            except Exception as exc:
                logger.warning("OptionsPaperExecutor: directive scan failed for %s: %s", sym, exc)

        return actionable

    def execute_strategy_directives(
        self,
        directives: Optional[List[Dict[str, Any]]] = None,
        dry_run: bool = False,
        max_notional_per_order: Optional[float] = None,
        macro_dto: Optional[Any] = None,
        vrp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Executes a list of actionable strategy directives into PaperAccountStore.

        ``macro_dto``/``vrp`` are forwarded to ``get_actionable_directives()`` when
        ``directives`` is not already supplied, so the VIX/CREDIT-EVENT/VRP
        premium-selling regime gate (see ``execution/options_queue_builder.py``'s
        ``passes_premium_gate``) is actually evaluated rather than silently
        skipped because both were left at their ``None`` default.
        """
        if max_notional_per_order is None:
            max_notional_per_order = getattr(settings, "MAX_OPTION_NOTIONAL_PER_TRADE", 2500.0)

        max_concurrent = getattr(settings, "MAX_CONCURRENT_OPTION_POSITIONS", 10)

        account = self.store.get_account()
        open_positions = self.store.get_open_positions()

        # Count current option positions and collect symbols
        held_option_symbols = set()
        total_option_positions = 0
        for pos in open_positions:
            is_opt = " " in pos.symbol and "$" in pos.symbol
            if is_opt:
                total_option_positions += 1
                base_sym = pos.symbol.split(" ")[0].upper()
                held_option_symbols.add(base_sym)

        executed = []
        skipped = []
        failed = []

        if directives is None:
            directives = self.get_actionable_directives(macro_dto=macro_dto, vrp=vrp)

        for item in directives:
            sym = str(item.get("symbol", "")).upper().strip()
            if not sym:
                continue

            strategy = item.get("strategy", "Multi-Leg Option")
            action = item.get("action", "Open")
            net_premium = item.get("net_premium")
            legs_raw = item.get("legs", [])
            target_dte = item.get("target_dte", 30)

            # 1. Check max concurrent position limit
            if total_option_positions >= max_concurrent:
                skipped.append({
                    "symbol": sym,
                    "reason": f"Max concurrent option positions limit reached ({max_concurrent})"
                })
                continue

            # 2. Position deduplication guard: avoid duplicate spreads on same ticker
            if sym in held_option_symbols:
                skipped.append({
                    "symbol": sym,
                    "reason": f"Position in {sym} already exists in paper account"
                })
                continue

            # 3. Resolve expiration date
            expiration = item.get("expiration") or _calculate_default_expiration(target_dte)

            # 4. Calculate strike width & pricing for sizing
            strikes = []
            parsed_legs = []
            signed_prices = []

            for idx, leg in enumerate(legs_raw):
                strike = float(leg.get("strike", 0.0) or leg.get("Strike", 0.0))
                if strike > 0:
                    strikes.append(strike)

                leg_type = str(leg.get("type", leg.get("Type", "CALL"))).upper()
                leg_side = str(leg.get("side", leg.get("Side", "BUY"))).lower()
                leg_ratio = float(leg.get("ratio_qty", leg.get("Ratio", 1.0)))

                # Formatted leg symbol
                leg_symbol = f"{sym} {expiration} ${strike:.2f} {leg_type}"

                # Leg price
                price = float(leg.get("price", leg.get("Price", 0.0)) or 0.0)
                if price <= 0 and net_premium and len(legs_raw) > 0:
                    price = abs(net_premium) / len(legs_raw)
                if price <= 0:
                    price = 0.50  # Fallback minimum option price

                signed_price = (price * leg_ratio) if leg_side == "buy" else (-price * leg_ratio)
                signed_prices.append(signed_price)

                parsed_legs.append({
                    "symbol": leg_symbol,
                    "side": leg_side,
                    "ratio_qty": leg_ratio,
                    "fill_price": price * 100.0,
                    "raw_price": price,
                })

            strike_width = None
            if len(strikes) >= 2:
                strikes_sorted = sorted(strikes)
                strike_width = abs(strikes_sorted[-1] - strikes_sorted[0])

            calc_net_price = sum(signed_prices)
            is_debit = calc_net_price >= 0
            net_price_per_share = abs(calc_net_price)

            # 5. Contract Sizing
            signed_net_price = calc_net_price
            sizing = calculate_multi_leg_option_sizing(
                dollar_amount=max_notional_per_order,
                net_price_per_share=signed_net_price,
                strike_width=strike_width,
                multiplier=100,
            )
            contracts = max(1, sizing)

            # 5b. Stage 4 ML Meta-Labeler Gating & Sizing
            if getattr(settings, "OPTIONS_META_LABELER_ENABLED", True):
                try:
                    from ml.options_meta_labeler import global_options_meta_labeler
                    ml_score = global_options_meta_labeler.score_option_directive(item)
                    if not ml_score.get("approved", True):
                        skipped.append({
                            "symbol": sym,
                            "reason": f"Stage 4 ML Meta-Labeler rejected directive (P(Win)={ml_score.get('prob_win', 0):.2f} < threshold)",
                            "ml_score": ml_score,
                        })
                        continue
                    mult = float(ml_score.get("sizing_multiplier", 1.0))
                    # No `max(1, ...)` floor here: re-flooring to 1 after a
                    # low-confidence multiplier (e.g. 0.30x) would silently
                    # re-inflate a derated trade back to full size whenever
                    # the base sizing was already 1 contract -- the common
                    # case -- defeating the confidence-based derating
                    # entirely. A directive that derates below 1 contract is
                    # skipped instead of forced back up to 1.
                    ml_contracts = int(round(contracts * mult))
                    if ml_contracts < 1:
                        skipped.append({
                            "symbol": sym,
                            "reason": f"Stage 4 ML Meta-Labeler sizing multiplier ({mult:.2f}x) derated position below 1 contract (P(Win)={ml_score.get('prob_win', 0):.2f})",
                            "ml_score": ml_score,
                        })
                        continue
                    contracts = ml_contracts
                except Exception as exc:
                    logger.debug("ML Meta-labeler evaluation skipped: %s", exc)

            # 6. Commission & Cash Impact

            commission = 0.65 * contracts * len(parsed_legs)
            if is_debit:
                net_cash_impact = -((contracts * net_price_per_share * 100.0) + commission)
                collateral = abs(net_cash_impact)
            else:
                net_cash_impact = (contracts * net_price_per_share * 100.0) - commission
                collateral = (strike_width * 100.0 * contracts) if strike_width else (contracts * net_price_per_share * 100.0)

            # 7. Check buying power
            if is_debit and abs(net_cash_impact) > account.buying_power:
                skipped.append({
                    "symbol": sym,
                    "reason": f"Insufficient buying power (Required: ${abs(net_cash_impact):.2f}, Available: ${account.buying_power:.2f})"
                })
                continue

            if dry_run:
                executed.append({
                    "symbol": sym,
                    "strategy": strategy,
                    "contracts": contracts,
                    "net_price": net_price_per_share,
                    "net_cash_impact": net_cash_impact,
                    "dry_run": True,
                })
                total_option_positions += 1
                held_option_symbols.add(sym)
                continue

            # 8. Execute atomic fill
            client_order_id = f"AUTO-OPT-{sym}-{int(datetime.now(timezone.utc).timestamp())}"
            try:
                fill_legs = [{
                    "symbol": l["symbol"],
                    "side": l["side"],
                    "qty": contracts * l["ratio_qty"],
                    "fill_price": l["fill_price"],
                } for l in parsed_legs]

                success = self.store.apply_multi_leg_fill(
                    client_order_id=client_order_id,
                    symbol=sym,
                    strategy_name=strategy,
                    contracts=contracts,
                    legs=fill_legs,
                    net_cash_impact=net_cash_impact,
                    commission_and_fees=commission,
                    collateral_required=collateral,
                )

                if success:
                    executed.append({
                        "order_id": client_order_id,
                        "symbol": sym,
                        "strategy": strategy,
                        "contracts": contracts,
                        "net_price": net_price_per_share,
                        "net_cash_impact": net_cash_impact,
                        "legs": [l["symbol"] for l in parsed_legs],
                    })
                    total_option_positions += 1
                    held_option_symbols.add(sym)
                    # Refresh account snapshot
                    account = self.store.get_account()
                else:
                    failed.append({
                        "symbol": sym,
                        "reason": "apply_multi_leg_fill returned False (insufficient funds or database lock)"
                    })
            except Exception as exc:
                logger.error("OptionsPaperExecutor: execution failed for %s: %s", sym, exc)
                failed.append({
                    "symbol": sym,
                    "reason": str(exc)
                })

        return {
            "executed_count": len(executed),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "executed": executed,
            "skipped": skipped,
            "failed": failed,
        }

    def evaluate_position_exits(
        self,
        spot_map: Optional[Dict[str, float]] = None,
        profit_target_pct: Optional[float] = None,
        stop_loss_multiple: Optional[float] = None,
        manage_dte_threshold: Optional[int] = None,
        current_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        Evaluates open option positions against profit target (e.g. 50%), stop loss (e.g. 200%),
        and 21-DTE gamma management thresholds.

        Aggregates positions per underlying and expiration (spreads/multi-leg/single-leg),
        calculates unrealized P&L against max initial credit or debit, and generates
        closing multi-leg order requests when an exit condition is triggered.
        """
        if profit_target_pct is None:
            profit_target_pct = float(getattr(settings, "OPTIONS_PROFIT_TARGET_PCT", 0.50))
        if stop_loss_multiple is None:
            stop_loss_multiple = float(getattr(settings, "OPTIONS_STOP_LOSS_MULTIPLE", 2.0))
        if manage_dte_threshold is None:
            manage_dte_threshold = int(getattr(settings, "OPTIONS_MANAGE_DTE_THRESHOLD", 21))

        from pilots.options_risk import parse_option_symbol

        positions = self.store.get_open_positions()
        today = current_date or datetime.now(timezone.utc).date()

        # Group positions by (ticker, expiration)
        grouped_positions: Dict[tuple[str, str], List[Any]] = {}
        for pos in positions:
            opt_info = parse_option_symbol(pos.symbol)
            if not opt_info:
                continue
            ticker = opt_info["ticker"].upper()
            exp_str = opt_info["expiration"]
            key = (ticker, exp_str)
            if key not in grouped_positions:
                grouped_positions[key] = []
            grouped_positions[key].append((pos, opt_info))

        candidates = []

        for (ticker, exp_str), group in grouped_positions.items():
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = max(0, (exp_date - today).days)
            except Exception:
                dte = 30

            # Spot price lookup
            spot = spot_map.get(ticker.upper()) if spot_map else None

            # Calculate mark price, P&L, entry cash for each leg in group
            total_entry_debit = 0.0
            total_entry_credit = 0.0
            total_unrealized_pl = 0.0
            closing_legs = []

            for pos, opt_info in group:
                strike = float(opt_info["strike"])
                opt_type = opt_info["option_type"].lower()
                qty = float(pos.qty)
                abs_qty = abs(qty)
                entry_price = float(pos.avg_entry_price)

                # Option pricing
                if spot is not None and spot > 0:
                    mark_price = _price_option_contract(spot, strike, dte, opt_type)
                else:
                    if pos.market_value is not None and abs_qty > 0:
                        mark_price = abs(float(pos.market_value)) / abs_qty
                    else:
                        mark_price = entry_price

                if qty > 0:
                    # Long leg: entry was debit, close by selling
                    leg_pl = (mark_price - entry_price) * abs_qty
                    total_entry_debit += abs_qty * entry_price
                    closing_side = "sell"
                else:
                    # Short leg: entry was credit, close by buying
                    leg_pl = (entry_price - mark_price) * abs_qty
                    total_entry_credit += abs_qty * entry_price
                    closing_side = "buy"

                total_unrealized_pl += leg_pl
                closing_legs.append({
                    "symbol": pos.symbol,
                    "side": closing_side,
                    "qty": abs_qty,
                    "fill_price": mark_price,
                })

            net_entry_credit = total_entry_credit - total_entry_debit
            is_credit = net_entry_credit > 0

            # Profit % and Loss Multiple calculation
            if is_credit:
                max_profit = net_entry_credit
                profit_pct = total_unrealized_pl / max_profit if max_profit > 0 else 0.0
                loss_multiple = (-total_unrealized_pl / max_profit) if (total_unrealized_pl < 0 and max_profit > 0) else 0.0
            else:
                initial_debit = abs(net_entry_credit) if net_entry_credit != 0 else total_entry_debit
                profit_pct = total_unrealized_pl / initial_debit if initial_debit > 0 else 0.0
                loss_multiple = (-total_unrealized_pl / initial_debit) if (total_unrealized_pl < 0 and initial_debit > 0) else 0.0

            # Evaluate triggers
            trigger = None
            reason_detail = None

            if profit_pct >= profit_target_pct:
                trigger = "PROFIT_TARGET"
                reason_detail = f"Profit target reached: {profit_pct:.1%} >= {profit_target_pct:.1%}"
            elif total_unrealized_pl < 0 and loss_multiple >= stop_loss_multiple:
                trigger = "STOP_LOSS"
                reason_detail = f"Stop loss triggered: {loss_multiple:.1f}x max risk >= {stop_loss_multiple:.1f}x"
            elif dte <= manage_dte_threshold:
                trigger = "DTE_MANAGEMENT"
                reason_detail = f"DTE threshold reached: {dte}d <= {manage_dte_threshold}d"

            if trigger:
                contracts = max(int(l["qty"]) for l in closing_legs) if closing_legs else 1
                sell_proceeds = sum(l["qty"] * l["fill_price"] for l in closing_legs if l["side"] == "sell")
                buy_costs = sum(l["qty"] * l["fill_price"] for l in closing_legs if l["side"] == "buy")
                commission = 0.65 * len(closing_legs) * contracts
                net_cash_impact = (sell_proceeds - buy_costs) - commission

                candidates.append({
                    "symbol": ticker,
                    "expiration": exp_str,
                    "position_symbol": closing_legs[0]["symbol"] if len(closing_legs) == 1 else f"{ticker} {exp_str} ({len(closing_legs)} legs)",
                    "strategy": f"Close {ticker} {exp_str}",
                    "action": "CLOSE",
                    "trigger_reason": trigger,
                    "reason_detail": reason_detail,
                    "dte": dte,
                    "profit_pct": round(profit_pct, 4),
                    "loss_multiple": round(loss_multiple, 4),
                    "unrealized_pl": round(total_unrealized_pl, 2),
                    "net_entry": round(net_entry_credit, 2),
                    "is_credit": is_credit,
                    "contracts": contracts,
                    "closing_side": closing_legs[0]["side"] if len(closing_legs) == 1 else "multi",
                    "qty": float(contracts),
                    "legs": closing_legs,
                    "net_cash_impact": round(net_cash_impact, 2),
                    "commission": round(commission, 2),
                })

        return candidates

    def execute_auto_exits(
        self,
        exit_candidates: Optional[List[Dict[str, Any]]] = None,
        spot_map: Optional[Dict[str, float]] = None,
        dry_run: bool = False,
        force: bool = False,
        current_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Executes closing orders for triggered exit candidates.
        Applies multi-leg fills to PaperAccountStore.
        If force=False and dry_run=False and settings.OPTIONS_AUTO_EXIT_ENABLED is False,
        skips execution and returns pending exit candidates.
        """
        if exit_candidates is None:
            exit_candidates = self.evaluate_position_exits(spot_map=spot_map, current_date=current_date)

        auto_exit_enabled = force or dry_run or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False)
        if not auto_exit_enabled:
            return {
                "enabled": False,
                "evaluated_count": len(exit_candidates),
                "executed_count": 0,
                "failed_count": 0,
                "executed": [],
                "failed": [],
                "pending_exits": exit_candidates,
            }

        executed = []
        failed = []

        for idx, candidate in enumerate(exit_candidates):
            sym = candidate["symbol"]
            strategy = candidate.get("strategy", f"Close {sym}")
            contracts = candidate.get("contracts", 1)
            legs = candidate.get("legs", [])
            net_cash_impact = candidate.get("net_cash_impact", 0.0)
            commission = candidate.get("commission", 0.0)
            reason = candidate.get("trigger_reason")
            client_order_id = f"AUTO-EXIT-{sym}-{int(datetime.now(timezone.utc).timestamp())}-{idx+1}"

            if dry_run:
                executed.append({
                    "order_id": client_order_id,
                    "symbol": sym,
                    "reason": reason,
                    "contracts": contracts,
                    "net_cash_impact": net_cash_impact,
                    "dry_run": True,
                    "legs": [l["symbol"] for l in legs],
                })
                continue

            try:
                success = self.store.apply_multi_leg_fill(
                    client_order_id=client_order_id,
                    symbol=sym,
                    strategy_name=strategy,
                    contracts=contracts,
                    legs=legs,
                    net_cash_impact=net_cash_impact,
                    commission_and_fees=commission,
                )

                if success:
                    executed.append({
                        "order_id": client_order_id,
                        "symbol": sym,
                        "reason": reason,
                        "reason_detail": candidate.get("reason_detail"),
                        "contracts": contracts,
                        "net_cash_impact": net_cash_impact,
                        "unrealized_pl": candidate.get("unrealized_pl"),
                        "legs": [l["symbol"] for l in legs],
                    })
                else:
                    failed.append({
                        "symbol": sym,
                        "reason": "apply_multi_leg_fill returned False",
                    })
            except Exception as exc:
                logger.error("OptionsPaperExecutor: exit execution failed for %s: %s", sym, exc)
                failed.append({
                    "symbol": sym,
                    "reason": str(exc),
                })

        return {
            "enabled": True,
            "evaluated_count": len(exit_candidates),
            "executed_count": len(executed),
            "failed_count": len(failed),
            "executed": executed,
            "failed": failed,
        }

    def execute_earnings_crush_trade(
        self,
        candidate: Dict[str, Any],
        contracts: int = 1,
    ) -> Dict[str, Any]:
        """
        Executes a multi-leg Earnings Crush options strategy (Iron Condor, Short Straddle,
        Short Strangle) into PaperAccountStore with strategy_name="Earnings Crush".

        Candidate format:
        {
            "symbol" / "ticker": "NVDA",
            "strategy": "Iron Condor" / "Short Straddle" / "Earnings Crush",
            "expiration": "2026-08-21",
            "legs": [
                {"symbol": "NVDA 2026-08-21 $120.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 250.0},
                ...
            ]
            or individual strikes / wing definitions:
            "spot": 120.0,
            "short_put": 110.0, "long_put": 105.0,
            "short_call": 130.0, "long_call": 135.0,
            "net_credit": 3.50,
            "earnings_date": "2026-08-20",
        }
        """
        sym = str(candidate.get("symbol") or candidate.get("ticker", "")).upper().strip()
        if not sym:
            return {"success": False, "reason": "Missing symbol in earnings crush candidate"}

        strategy = str(candidate.get("strategy") or "Earnings Crush")
        earnings_date = candidate.get("earnings_date")
        target_dte = candidate.get("target_dte", 7)
        expiration = candidate.get("expiration") or candidate.get("exp_date") or _calculate_default_expiration(target_dte)

        # Parse / build legs
        raw_legs = candidate.get("legs", [])
        parsed_legs = []
        strikes = []
        signed_prices = []

        if raw_legs:
            for idx, leg in enumerate(raw_legs):
                leg_sym = leg.get("symbol")
                side = str(leg.get("side", leg.get("Side", "BUY"))).lower()
                ratio = float(leg.get("ratio_qty", leg.get("Ratio", leg.get("qty", 1.0))))
                
                strike = float(leg.get("strike", 0.0) or leg.get("Strike", 0.0))
                opt_type = str(leg.get("type", leg.get("Type", "CALL"))).upper()
                
                if leg_sym:
                    opt_info = parse_option_symbol(leg_sym)
                    if opt_info:
                        strike = opt_info["strike"]
                        opt_type = opt_info["option_type"].upper()
                else:
                    leg_sym = f"{sym} {expiration} ${strike:.2f} {opt_type}"

                if strike > 0:
                    strikes.append(strike)

                # Price in $/contract (fill_price) or $/share (raw_price)
                fill_price = float(leg.get("fill_price", 0.0) or 0.0)
                raw_price = float(leg.get("price", leg.get("Price", 0.0) or leg.get("raw_price", 0.0)) or 0.0)
                
                if fill_price <= 0 and raw_price > 0:
                    fill_price = raw_price * 100.0 if raw_price < 50.0 else raw_price
                elif fill_price > 0 and raw_price <= 0:
                    raw_price = fill_price / 100.0
                elif fill_price <= 0 and raw_price <= 0:
                    raw_price = 1.50
                    fill_price = 150.0

                signed_price = (raw_price * ratio) if side == "buy" else (-raw_price * ratio)
                signed_prices.append(signed_price)

                parsed_legs.append({
                    "symbol": leg_sym,
                    "side": side,
                    "qty": contracts * ratio,
                    "ratio_qty": ratio,
                    "fill_price": fill_price,
                    "raw_price": raw_price,
                })
        else:
            # Construct from strikes in candidate dict
            # Support Iron Condor (short_put, long_put, short_call, long_call)
            # or Straddle (put_strike, call_strike or atm_strike)
            spot = float(candidate.get("spot", candidate.get("spot_price", 100.0)))
            
            if "short_put" in candidate or "short_put_strike" in candidate:
                sp = float(candidate.get("short_put", candidate.get("short_put_strike", 0.0)))
                lp = float(candidate.get("long_put", candidate.get("long_put_strike", 0.0)))
                sc = float(candidate.get("short_call", candidate.get("short_call_strike", 0.0)))
                lc = float(candidate.get("long_call", candidate.get("long_call_strike", 0.0)))

                if sp > 0:
                    strikes.extend([sp, lp, sc, lc])
                    parsed_legs = [
                        {"symbol": f"{sym} {expiration} ${lp:.2f} PUT", "side": "buy", "qty": float(contracts), "ratio_qty": 1.0, "fill_price": _price_option_contract(spot, lp, target_dte, "put"), "raw_price": _price_option_contract(spot, lp, target_dte, "put") / 100.0},
                        {"symbol": f"{sym} {expiration} ${sp:.2f} PUT", "side": "sell", "qty": float(contracts), "ratio_qty": 1.0, "fill_price": _price_option_contract(spot, sp, target_dte, "put"), "raw_price": _price_option_contract(spot, sp, target_dte, "put") / 100.0},
                        {"symbol": f"{sym} {expiration} ${sc:.2f} CALL", "side": "sell", "qty": float(contracts), "ratio_qty": 1.0, "fill_price": _price_option_contract(spot, sc, target_dte, "call"), "raw_price": _price_option_contract(spot, sc, target_dte, "call") / 100.0},
                        {"symbol": f"{sym} {expiration} ${lc:.2f} CALL", "side": "buy", "qty": float(contracts), "ratio_qty": 1.0, "fill_price": _price_option_contract(spot, lc, target_dte, "call"), "raw_price": _price_option_contract(spot, lc, target_dte, "call") / 100.0},
                    ]
            elif "atm_strike" in candidate or "strike" in candidate:
                atm = float(candidate.get("atm_strike", candidate.get("strike", spot)))
                parsed_legs = [
                    {"symbol": f"{sym} {expiration} ${atm:.2f} PUT", "side": "sell", "qty": float(contracts), "ratio_qty": 1.0, "fill_price": _price_option_contract(spot, atm, target_dte, "put"), "raw_price": _price_option_contract(spot, atm, target_dte, "put") / 100.0},
                    {"symbol": f"{sym} {expiration} ${atm:.2f} CALL", "side": "sell", "qty": float(contracts), "ratio_qty": 1.0, "fill_price": _price_option_contract(spot, atm, target_dte, "call"), "raw_price": _price_option_contract(spot, atm, target_dte, "call") / 100.0},
                ]
                strikes.append(atm)

        if not parsed_legs:
            return {"success": False, "reason": f"No valid legs constructed for {sym} Earnings Crush"}

        # Calculate net cash impact & collateral
        commission = 0.65 * contracts * len(parsed_legs)
        net_credit_arg = candidate.get("net_credit", candidate.get("net_premium"))

        if net_credit_arg is not None:
            net_cash_impact = (float(net_credit_arg) * 100.0 * contracts) - commission
        else:
            # Sum leg fill prices
            sell_proceeds = sum(l["qty"] * l["fill_price"] for l in parsed_legs if l["side"] == "sell")
            buy_costs = sum(l["qty"] * l["fill_price"] for l in parsed_legs if l["side"] == "buy")
            net_cash_impact = (sell_proceeds - buy_costs) - commission

        # Collateral
        strike_width = None
        if len(strikes) >= 2:
            sorted_strikes = sorted(strikes)
            strike_width = abs(sorted_strikes[-1] - sorted_strikes[0])
        collateral = (strike_width * 100.0 * contracts) if strike_width else abs(net_cash_impact)

        client_order_id = f"EC-{sym}-{int(datetime.now(timezone.utc).timestamp())}"
        fill_legs = [{
            "symbol": l["symbol"],
            "side": l["side"],
            "qty": l["qty"],
            "fill_price": l["fill_price"],
        } for l in parsed_legs]

        success = self.store.apply_multi_leg_fill(
            client_order_id=client_order_id,
            symbol=sym,
            strategy_name="Earnings Crush",
            contracts=contracts,
            legs=fill_legs,
            net_cash_impact=net_cash_impact,
            commission_and_fees=commission,
            collateral_required=collateral,
        )

        return {
            "success": bool(success),
            "order_id": client_order_id,
            "symbol": sym,
            "strategy": "Earnings Crush",
            "contracts": contracts,
            "net_cash_impact": net_cash_impact,
            "commission": commission,
            "legs": fill_legs,
            "earnings_date": earnings_date,
            "reason": None if success else "apply_multi_leg_fill returned False",
        }

    def settle_post_earnings_trades(
        self,
        current_date: Optional[date] = None,
        spot_map: Optional[Dict[str, float]] = None,
        iv_crush_factor: float = 0.40,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Scans open positions for trades opened under the 'Earnings Crush' strategy where the
        earnings announcement has completed (as of current_date), and closes all constituent legs
        at market open to harvest pure IV crush.
        """
        today = current_date or datetime.now(timezone.utc).date()
        settled = []
        failed = []

        with session_scope(self.store.Session) as session:
            # Query parent orders placed with strategy_name="Earnings Crush" or client_order_id like 'EC-%'
            ec_orders = (
                session.query(PaperOrder)
                .filter(
                    (PaperOrder.symbol.like("%EARNINGS CRUSH%"))
                    | ((PaperOrder.client_order_id.like("EC-%")) & (~PaperOrder.client_order_id.like("%_L%")))
                )
                .filter(
                    (PaperOrder.status == "filled")
                    | (PaperOrder.status == "FILLED")
                )
                .all()
            )

            # Map order ID -> list of leg symbols and target ticker
            ec_order_legs: Dict[str, Dict[str, Any]] = {}
            for eco in ec_orders:
                # Find child leg orders
                child_legs = (
                    session.query(PaperOrder)
                    .filter(PaperOrder.client_order_id.like(f"{eco.client_order_id}_L%"))
                    .all()
                )
                leg_symbols = [cl.symbol.upper() for cl in child_legs]
                parts = eco.symbol.split()
                ticker = parts[-1].upper() if len(parts) > 1 else (leg_symbols[0].split()[0] if leg_symbols else "UNKNOWN")

                if not leg_symbols:
                    continue

                order_date = eco.timestamp.date()
                ec_order_legs[eco.client_order_id] = {
                    "order_id": eco.client_order_id,
                    "ticker": ticker,
                    "order_date": order_date,
                    "leg_symbols": leg_symbols,
                }

            # Query current open positions
            open_pos_rows = session.query(PaperPosition).filter(PaperPosition.qty != 0).all()
            open_pos_map = {
                p.symbol.upper(): {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                }
                for p in open_pos_rows
            }

            # Check each EC order
            active_ec_trades = []
            for coid, info in ec_order_legs.items():
                open_legs_for_order = []
                for lsym in info["leg_symbols"]:
                    if lsym in open_pos_map:
                        open_legs_for_order.append(open_pos_map[lsym])

                if open_legs_for_order:
                    # Earnings announcement is completed if:
                    # 1. force is True, OR
                    # 2. current_date was explicitly passed and current_date >= info["order_date"], OR
                    # 3. today > info["order_date"]
                    earnings_completed = force or (current_date is not None and current_date >= info["order_date"]) or (today > info["order_date"])
                    if earnings_completed:
                        active_ec_trades.append({
                            "parent_order_id": coid,
                            "ticker": info["ticker"],
                            "positions": open_legs_for_order,
                        })

        for trade in active_ec_trades:
            ticker = trade["ticker"]
            closing_legs = []
            spot = spot_map.get(ticker.upper()) if spot_map else None

            for pos in trade["positions"]:
                qty = float(pos["qty"])
                abs_qty = abs(qty)
                entry_price = float(pos["avg_entry_price"])
                opt_info = parse_option_symbol(pos["symbol"])

                # Option pricing post IV crush
                if spot is not None and spot > 0 and opt_info:
                    strike = float(opt_info["strike"])
                    opt_type = str(opt_info["option_type"]).lower()
                    exp_str = opt_info["expiration"]
                    try:
                        exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                        dte = max(0, (exp_d - today).days)
                    except Exception:
                        dte = 1
                    mark_price = _price_option_contract(spot, strike, dte, opt_type, sigma=0.20)
                else:
                    mark_price = entry_price * iv_crush_factor

                closing_side = "sell" if qty > 0 else "buy"
                closing_legs.append({
                    "symbol": pos["symbol"],
                    "side": closing_side,
                    "qty": abs_qty,
                    "fill_price": mark_price,
                })

            if not closing_legs:
                continue

            contracts = max(int(l["qty"]) for l in closing_legs)
            sell_proceeds = sum(l["qty"] * l["fill_price"] for l in closing_legs if l["side"] == "sell")
            buy_costs = sum(l["qty"] * l["fill_price"] for l in closing_legs if l["side"] == "buy")
            commission = 0.65 * len(closing_legs) * contracts
            net_cash_impact = (sell_proceeds - buy_costs) - commission

            close_order_id = f"CLOSE-EC-{ticker}-{int(datetime.now(timezone.utc).timestamp())}"
            try:
                success = self.store.apply_multi_leg_fill(
                    client_order_id=close_order_id,
                    symbol=ticker,
                    strategy_name="Close Earnings Crush",
                    contracts=contracts,
                    legs=closing_legs,
                    net_cash_impact=net_cash_impact,
                    commission_and_fees=commission,
                )

                if success:
                    settled.append({
                        "order_id": close_order_id,
                        "symbol": ticker,
                        "parent_order_id": trade["parent_order_id"],
                        "strategy": "Earnings Crush (Post-Earnings Close)",
                        "contracts": contracts,
                        "net_cash_impact": net_cash_impact,
                        "commission": commission,
                        "closing_legs": closing_legs,
                    })
                else:
                    failed.append({
                        "symbol": ticker,
                        "parent_order_id": trade["parent_order_id"],
                        "reason": "apply_multi_leg_fill returned False",
                    })
            except Exception as exc:
                logger.error("OptionsPaperExecutor: failed to settle EC trade for %s: %s", ticker, exc)
                failed.append({
                    "symbol": ticker,
                    "parent_order_id": trade["parent_order_id"],
                    "reason": str(exc),
                })

        return {
            "settled_count": len(settled),
            "failed_count": len(failed),
            "settled": settled,
            "failed": failed,
        }

    def execute_dispersion_trade(
        self,
        basket: Any,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Executes a calibrated DispersionBasket into PaperAccountStore."""
        from pilots.dispersion_trading import execute_dispersion_trade
        return execute_dispersion_trade(basket=basket, store=self.store, dry_run=dry_run)

