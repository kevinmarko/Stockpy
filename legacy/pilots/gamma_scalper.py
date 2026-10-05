"""
pilots/gamma_scalper.py — Intraday Gamma Scalping & Dynamic Delta Neutralization Simulator.
==========================================================================================

Simulates dynamic discrete equity hedging for option positions along an intraday spot price
path. When net delta drifts beyond a specified threshold (|net_delta| >= delta_threshold),
the engine simulates rebalancing equity trades to restore delta-neutrality.

Calculates:
- Total Scalping Realized P&L: Cumulative trading P&L from hedging equity trades.
- Total Transaction Costs: Total commissions/fees paid on stock executions.
- Option Position Mark-to-Market P&L: Value change of the underlying options.
- Theoretical Gamma Rent: 0.5 * sum(Gamma * (dS)^2) — empirical volatility payoff.
- Theta Time Decay: sum(Theta * dt) — time decay cost paid for holding gamma.
- Net Edge: Scalping Realized P&L - Theta Decay Cost (profitability over time decay).

Design Invariants:
* **AST-Safe (CONSTRAINT #1 & #3)** — Pure compute module. Never imports processing_engine.
* **Honesty (CONSTRAINT #4)** — Accurate Black-Scholes Greeks, exact stock cash flows.
* **Never Raises (CONSTRAINT #6)** — Degrades gracefully with default/empty price paths on degenerate inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy.stats import norm

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "simulate_gamma_scalping",
    "to_gamma_scalp_response",
    "generate_gbm_price_path",
    "generate_synthetic_price_path",
    "GammaScalpResult",
    "GammaScalpTrade",
    "GammaScalpHedgeSnapshot",
]

_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)

_DEGENERATE_THRESHOLD = 1e-12
_DEFAULT_MULTIPLIER = 100.0
TRADING_DAYS_PER_YEAR = 252.0


def _parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Parses standardized option leg symbol e.g. 'AAPL 2026-09-18 $150.00 CALL'."""
    if not isinstance(symbol, str):
        return None
    m = _OPTION_SYM_RE.match(symbol.strip())
    if not m:
        return None
    return {
        "ticker": m.group("ticker").upper(),
        "expiration": m.group("exp"),
        "strike": float(m.group("strike")),
        "option_type": m.group("type").upper(),
    }


def _bs_price_and_greeks(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "CALL",
    r: float = 0.045,
) -> Dict[str, float]:
    """
    Computes per-share Black-Scholes price and Greeks with degenerate guards (delegates to canonical pilots.options_risk).
    Returns: {price, delta, gamma, theta_annual, theta_daily, theta, vega_1pct}.
    """
    from pilots.options_risk import calculate_black_scholes_greeks

    res = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=option_type,
        r=r,
    )
    return {
        "price": float(res["price"]),
        "delta": float(res["delta"]),
        "gamma": float(res["gamma"]),
        "theta_annual": float(res["theta_annual"]),
        "theta_daily": float(res["theta_daily"]),
        "theta": float(res["theta_daily"]),
        "vega_1pct": float(res["vega_1pct"]),
    }


@dataclass
class GammaScalpTrade:
    """Record of an individual delta-rebalancing equity trade."""
    step: int
    spot_price: float
    shares: float
    side: str  # "buy" or "sell"
    fee: float
    net_delta_before: float
    net_delta_after: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "spot_price": round(self.spot_price, 4),
            "shares": round(self.shares, 4),
            "side": self.side,
            "trade_type": f"REBALANCE_{self.side.upper()}",
            "fee": round(self.fee, 4),
            "transaction_cost": round(self.fee, 4),
            "net_delta_before": round(self.net_delta_before, 4),
            "net_delta_after": round(self.net_delta_after, 4),
            "reason": self.reason,
        }


@dataclass
class GammaScalpHedgeSnapshot:
    """Snapshot of portfolio Greeks and hedge state at a single time step."""
    step: int
    spot_price: float
    time_remaining_years: float
    option_price: float
    option_delta_shares: float
    option_gamma_shares: float
    option_theta_annual: float
    stock_shares: float
    net_delta_shares: float
    net_delta_normalized: float
    gamma_rent_step: float
    theta_decay_step: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "spot_price": round(self.spot_price, 4),
            "time_remaining_years": round(self.time_remaining_years, 6),
            "option_price": round(self.option_price, 4),
            "option_delta_shares": round(self.option_delta_shares, 4),
            "option_gamma_shares": round(self.option_gamma_shares, 4),
            "option_theta_annual": round(self.option_theta_annual, 4),
            "stock_shares": round(self.stock_shares, 4),
            "net_delta_shares": round(self.net_delta_shares, 4),
            "net_delta_normalized": round(self.net_delta_normalized, 4),
            "gamma_rent_step": round(self.gamma_rent_step, 4),
            "theta_decay_step": round(self.theta_decay_step, 4),
        }


class GammaScalpResult(dict):
    """
    Structured dictionary containing simulation results and metrics.
    Supports both dict indexing (result['total_scalping_realized_pnl']) and attribute access.
    """
    def __init__(self, data: Dict[str, Any]):
        super().__init__(data)
        self.__dict__.update(data)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'GammaScalpResult' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value
        self.__dict__[name] = value


def _normalize_legs(
    option_position: Any,
    default_sigma: float = 0.25,
    default_r: float = 0.045,
    default_multiplier: float = 100.0,
    default_dte: float = 30.0,
) -> List[Dict[str, Any]]:
    """
    Normalizes single or multi-leg option position inputs into standardized leg dictionaries.
    """
    if option_position is None:
        return [{
            "symbol": "SPY STRADDLE",
            "qty": 1.0,
            "strike": 100.0,
            "option_type": "STRADDLE",
            "sigma": default_sigma,
            "r": default_r,
            "multiplier": default_multiplier,
            "t_years_start": default_dte / 365.0,
        }]

    raw_legs: List[Any] = []
    if isinstance(option_position, list):
        raw_legs = option_position
    elif isinstance(option_position, tuple):
        raw_legs = list(option_position)
    else:
        raw_legs = [option_position]

    legs: List[Dict[str, Any]] = []

    for item in raw_legs:
        if item is None:
            continue

        sym = ""
        qty = 1.0
        strike = 100.0
        opt_type = "CALL"
        sigma = default_sigma
        r = default_r
        multiplier = default_multiplier
        t_years = default_dte / 365.0

        if isinstance(item, dict):
            sym = str(item.get("symbol") or "")
            qty = float(item.get("qty", item.get("contracts", 1.0)) or 1.0)
            strike = float(item.get("strike", item.get("spot_price", 0.0)) or 0.0)
            strategy = str(item.get("strategy") or "").upper()
            raw_opt_type = str(item.get("option_type", item.get("type", ""))).upper()
            if "STRADDLE" in strategy or raw_opt_type == "STRADDLE":
                opt_type = "STRADDLE"
            elif "PUT" in strategy or raw_opt_type == "PUT":
                opt_type = "PUT"
            else:
                opt_type = "CALL"

            sigma = float(item.get("sigma", item.get("implied_vol", item.get("iv", default_sigma))) or default_sigma)
            r = float(item.get("r", default_r) or default_r)
            multiplier = float(item.get("multiplier", default_multiplier) or default_multiplier)
            if "t_years" in item and item["t_years"] is not None:
                t_years = float(item["t_years"])
            elif "dte" in item and item["dte"] is not None:
                t_years = max(0.0, float(item["dte"]) / 365.0)
        elif hasattr(item, "symbol"):  # PaperPosition or similar
            sym = str(getattr(item, "symbol", ""))
            qty = float(getattr(item, "qty", 1.0) or 1.0)
            strike = float(getattr(item, "strike", 0.0) or 0.0)
            opt_type = str(getattr(item, "option_type", "CALL")).upper()
            sigma = float(getattr(item, "sigma", getattr(item, "iv", default_sigma)) or default_sigma)
            r = float(getattr(item, "r", default_r) or default_r)
            multiplier = float(getattr(item, "multiplier", default_multiplier) or default_multiplier)
            if hasattr(item, "t_years") and getattr(item, "t_years") is not None:
                t_years = float(getattr(item, "t_years"))
            elif hasattr(item, "dte") and getattr(item, "dte") is not None:
                t_years = max(0.0, float(getattr(item, "dte")) / 365.0)

        # Parse symbol if provided
        parsed = _parse_option_symbol(sym)
        if parsed:
            strike = parsed["strike"]
            opt_type = parsed["option_type"]
            exp_str = parsed["expiration"]
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                dte_calc = max(0.0, (exp_date - now_utc).total_seconds() / 86400.0)
                t_years = dte_calc / 365.0
            except Exception:
                pass

        if strike <= 0:
            strike = 100.0

        legs.append({
            "symbol": sym,
            "qty": qty,
            "strike": strike,
            "option_type": opt_type,
            "sigma": max(1e-6, sigma),
            "r": r,
            "multiplier": multiplier,
            "t_years_start": max(0.0, t_years),
        })

    return legs


def simulate_gamma_scalping(
    option_position: Any = None,
    price_path: Optional[Sequence[float]] = None,
    delta_threshold: float = 0.15,
    fee_per_share: float = 0.005,
    *,
    position: Optional[Dict[str, Any]] = None,
    dt: Optional[float] = None,
    dt_days: Optional[float] = None,
    total_time_years: Optional[float] = None,
    transaction_cost_per_share: Optional[float] = None,
    initial_hedge: bool = True,
    sigma: Optional[float] = None,
    r: Optional[float] = None,
    multiplier: Optional[float] = None,
) -> GammaScalpResult:
    """
    Simulates dynamic delta hedging and gamma scalping along an intraday spot price path.

    Parameters:
    -----------
    option_position / position:
        PaperPosition, dict, or list of legs representing option contracts.
    price_path:
        Sequence of underlying spot prices along the simulation trajectory.
    delta_threshold:
        Threshold trigger for dynamic rebalancing (default 0.15 delta drift).
    fee_per_share / transaction_cost_per_share:
        Commission / transaction cost per share traded (default $0.005/share).
    dt / dt_days:
        Optional time step duration per interval in years or days.
    total_time_years:
        Optional total elapsed time across the entire price path in years.
    initial_hedge:
        Whether to establish an initial delta-neutral hedge at t=0 (default True).
    sigma:
        Optional implied volatility override.
    r:
        Optional risk-free interest rate override.
    multiplier:
        Optional contract multiplier override (default 100).

    Returns:
    --------
    GammaScalpResult (dict-like with attribute access) containing:
        - total_scalping_realized_pnl (stock_pnl)
        - total_transaction_costs
        - scalping_net_pnl
        - option_mtm_pnl (option_pnl)
        - total_portfolio_pnl (total_pnl)
        - theoretical_gamma_rent
        - theta_time_decay
        - net_edge
        - attribution
        - rebalance_count
        - trades
        - hedge_history
        - path_history
    """
    # Merge parameter aliases
    pos_input = option_position if option_position is not None else position
    fee_val = transaction_cost_per_share if transaction_cost_per_share is not None else fee_per_share
    fee_per_share = max(0.0, float(fee_val if fee_val is not None else 0.005))
    delta_threshold = max(0.001, float(delta_threshold if delta_threshold is not None else 0.15))
    r_val = float(r if r is not None else getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))
    default_sig = float(sigma) if sigma is not None else 0.25
    default_mult = float(multiplier) if multiplier is not None else _DEFAULT_MULTIPLIER

    legs = _normalize_legs(
        pos_input,
        default_sigma=default_sig,
        default_r=r_val,
        default_multiplier=default_mult,
    )

    # Empty price path handling
    if price_path is None:
        prices = generate_synthetic_price_path(
            initial_spot=legs[0]["strike"] if legs else 100.0,
            annual_vol=legs[0]["sigma"] if legs else default_sig,
            n_steps=50,
            dt_days=dt_days or 0.1,
        )
    else:
        prices = [float(p) for p in price_path if p is not None and not np.isnan(p)]

    if len(prices) == 0:
        empty_res: Dict[str, Any] = {
            "ok": True,
            "success": True,
            "status": "empty_path",
            "total_scalping_realized_pnl": 0.0,
            "stock_pnl": 0.0,
            "total_transaction_costs": 0.0,
            "scalping_net_pnl": 0.0,
            "option_mtm_pnl": 0.0,
            "option_pnl": 0.0,
            "total_portfolio_pnl": 0.0,
            "total_pnl": 0.0,
            "theoretical_gamma_rent": 0.0,
            "theta_time_decay": 0.0,
            "theta_decay_cost": 0.0,
            "net_edge": 0.0,
            "rebalance_count": 0,
            "initial_spot": 0.0,
            "final_spot": 0.0,
            "realized_volatility": 0.0,
            "implied_volatility": default_sig,
            "volatility_spread": 0.0,
            "trades": [],
            "hedge_history": [],
            "path_history": [],
            "attribution": {
                "gamma_rent": 0.0,
                "theta_decay": 0.0,
                "transaction_costs": 0.0,
                "net_edge": 0.0,
            },
            "Total Scalping Realized P&L": 0.0,
            "Total Transaction Costs": 0.0,
            "Option Position Mark-to-Market P&L": 0.0,
            "Theoretical Gamma Rent": 0.0,
            "Theta Time Decay": 0.0,
            "Net Edge": 0.0,
        }
        return GammaScalpResult(empty_res)

    n_steps = len(prices)

    # Time step calculation
    if dt_days is not None and dt_days > 0:
        dt_years = dt_days / TRADING_DAYS_PER_YEAR
    elif dt is not None and dt > 0:
        dt_years = float(dt)
    elif total_time_years is not None and total_time_years > 0:
        dt_years = (total_time_years / (n_steps - 1)) if n_steps > 1 else 0.0
    else:
        # Default: 1 trading day across path
        dt_years = (1.0 / TRADING_DAYS_PER_YEAR) / (n_steps - 1) if n_steps > 1 else 0.0

    total_effective_multiplier = sum(abs(l["qty"]) * l["multiplier"] for l in legs) or _DEFAULT_MULTIPLIER

    if delta_threshold < 1.0:
        threshold_shares = delta_threshold * total_effective_multiplier
        threshold_norm = delta_threshold
    else:
        threshold_shares = delta_threshold
        threshold_norm = delta_threshold / total_effective_multiplier

    def _evaluate_legs_greeks(spot: float, step_idx: int) -> Dict[str, float]:
        t_elapsed = step_idx * dt_years
        tot_val = 0.0
        tot_delta_shares = 0.0
        tot_gamma_shares = 0.0
        tot_theta_annual = 0.0
        tot_theta_daily = 0.0

        for leg in legs:
            t_rem = max(0.0, leg["t_years_start"] - t_elapsed)
            opt_t = leg["option_type"]
            eff_qty = leg["qty"] * leg["multiplier"]

            if opt_t == "STRADDLE":
                c = _bs_price_and_greeks(spot, leg["strike"], t_rem, leg["sigma"], "CALL", leg["r"])
                p = _bs_price_and_greeks(spot, leg["strike"], t_rem, leg["sigma"], "PUT", leg["r"])
                tot_val += eff_qty * (c["price"] + p["price"])
                tot_delta_shares += eff_qty * (c["delta"] + p["delta"])
                tot_gamma_shares += eff_qty * (c["gamma"] + p["gamma"])
                tot_theta_annual += eff_qty * (c["theta_annual"] + p["theta_annual"])
                tot_theta_daily += eff_qty * (c["theta_daily"] + p["theta_daily"])
            else:
                bs = _bs_price_and_greeks(spot, leg["strike"], t_rem, leg["sigma"], opt_t, leg["r"])
                tot_val += eff_qty * bs["price"]
                tot_delta_shares += eff_qty * bs["delta"]
                tot_gamma_shares += eff_qty * bs["gamma"]
                tot_theta_annual += eff_qty * bs["theta_annual"]
                tot_theta_daily += eff_qty * bs["theta_daily"]

        return {
            "total_value": tot_val,
            "delta_shares": tot_delta_shares,
            "gamma_shares": tot_gamma_shares,
            "theta_annual": tot_theta_annual,
            "theta_daily": tot_theta_daily,
        }

    # Simulation tracking
    stock_shares = 0.0
    total_cash_flow = 0.0
    total_transaction_costs = 0.0
    total_gamma_rent = 0.0
    total_theta_pnl = 0.0

    trades: List[GammaScalpTrade] = []
    hedge_history: List[GammaScalpHedgeSnapshot] = []
    path_history: List[Dict[str, Any]] = []

    s0 = prices[0]
    book0 = _evaluate_legs_greeks(s0, 0)
    initial_option_value = book0["total_value"]

    if initial_hedge and len(legs) > 0:
        initial_hedge_shares = -book0["delta_shares"]
        if abs(initial_hedge_shares) > 1e-6:
            trade_fee = abs(initial_hedge_shares) * fee_per_share
            total_transaction_costs += trade_fee
            total_cash_flow -= initial_hedge_shares * s0
            stock_shares = initial_hedge_shares

            trades.append(
                GammaScalpTrade(
                    step=0,
                    spot_price=s0,
                    shares=initial_hedge_shares,
                    side="buy" if initial_hedge_shares > 0 else "sell",
                    fee=trade_fee,
                    net_delta_before=book0["delta_shares"],
                    net_delta_after=0.0,
                    reason="Initial delta hedge to neutral",
                )
            )

    net_delta_0_shares = book0["delta_shares"] + stock_shares
    net_delta_0_norm = net_delta_0_shares / total_effective_multiplier

    hedge_history.append(
        GammaScalpHedgeSnapshot(
            step=0,
            spot_price=s0,
            time_remaining_years=legs[0]["t_years_start"] if legs else 0.0,
            option_price=book0["total_value"],
            option_delta_shares=book0["delta_shares"],
            option_gamma_shares=book0["gamma_shares"],
            option_theta_annual=book0["theta_annual"],
            stock_shares=stock_shares,
            net_delta_shares=net_delta_0_shares,
            net_delta_normalized=net_delta_0_norm,
            gamma_rent_step=0.0,
            theta_decay_step=0.0,
        )
    )

    path_history.append({
        "step": 0,
        "spot_price": round(s0, 4),
        "option_value": round(book0["total_value"], 4),
        "stock_shares": round(stock_shares, 4),
        "stock_pnl": 0.0,
        "option_pnl": 0.0,
        "total_pnl": 0.0,
        "gamma_rent": 0.0,
        "theta_decay": 0.0,
        "net_delta": round(net_delta_0_shares, 4),
    })

    prev_gamma = book0["gamma_shares"]
    prev_theta = book0["theta_annual"]

    for k in range(1, n_steps):
        s_k = prices[k]
        s_prev = prices[k - 1]
        ds = s_k - s_prev

        gamma_rent_k = 0.5 * prev_gamma * (ds ** 2)
        theta_decay_k = prev_theta * dt_years

        total_gamma_rent += gamma_rent_k
        total_theta_pnl += theta_decay_k

        book_k = _evaluate_legs_greeks(s_k, k)
        net_delta_k_shares = book_k["delta_shares"] + stock_shares
        net_delta_k_norm = net_delta_k_shares / total_effective_multiplier

        rebalanced = False
        if abs(net_delta_k_shares) >= (threshold_shares - 1e-9) and k < n_steps:
            rebalance_shares = -net_delta_k_shares
            trade_fee = abs(rebalance_shares) * fee_per_share
            total_transaction_costs += trade_fee
            total_cash_flow -= rebalance_shares * s_k
            stock_shares += rebalance_shares
            rebalanced = True

            trades.append(
                GammaScalpTrade(
                    step=k,
                    spot_price=s_k,
                    shares=rebalance_shares,
                    side="buy" if rebalance_shares > 0 else "sell",
                    fee=trade_fee,
                    net_delta_before=net_delta_k_shares,
                    net_delta_after=0.0,
                    reason=f"Rebalance delta drift ({net_delta_k_shares:+.2f} shares)",
                )
            )

        post_net_delta_shares = 0.0 if rebalanced else net_delta_k_shares
        post_net_delta_norm = 0.0 if rebalanced else net_delta_k_norm

        hedge_history.append(
            GammaScalpHedgeSnapshot(
                step=k,
                spot_price=s_k,
                time_remaining_years=max(0.0, (legs[0]["t_years_start"] if legs else 0.0) - k * dt_years),
                option_price=book_k["total_value"],
                option_delta_shares=book_k["delta_shares"],
                option_gamma_shares=book_k["gamma_shares"],
                option_theta_annual=book_k["theta_annual"],
                stock_shares=stock_shares,
                net_delta_shares=post_net_delta_shares,
                net_delta_normalized=post_net_delta_norm,
                gamma_rent_step=gamma_rent_k,
                theta_decay_step=theta_decay_k,
            )
        )

        curr_stock_mtm = total_cash_flow + (stock_shares * s_k)
        curr_option_pnl = book_k["total_value"] - initial_option_value
        path_history.append({
            "step": k,
            "spot_price": round(s_k, 4),
            "option_value": round(book_k["total_value"], 4),
            "stock_shares": round(stock_shares, 4),
            "stock_pnl": round(curr_stock_mtm, 4),
            "option_pnl": round(curr_option_pnl, 4),
            "total_pnl": round(curr_stock_mtm + curr_option_pnl, 4),
            "gamma_rent": round(total_gamma_rent, 4),
            "theta_decay": round(total_theta_pnl, 4),
            "net_delta": round(post_net_delta_shares, 4),
        })

        prev_gamma = book_k["gamma_shares"]
        prev_theta = book_k["theta_annual"]

    final_spot = prices[-1]
    final_book = _evaluate_legs_greeks(final_spot, n_steps - 1)
    final_option_value = final_book["total_value"]

    option_mtm_pnl = final_option_value - initial_option_value
    scalping_realized_pnl = total_cash_flow + (stock_shares * final_spot)
    scalping_net_pnl = scalping_realized_pnl - total_transaction_costs
    total_portfolio_pnl = option_mtm_pnl + scalping_net_pnl

    theta_decay_cost = -total_theta_pnl if total_theta_pnl <= 0 else 0.0
    net_edge = scalping_realized_pnl - theta_decay_cost

    realized_vol = 0.0
    if n_steps > 1:
        log_rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, n_steps) if prices[i - 1] > 0 and prices[i] > 0]
        if len(log_rets) > 1:
            step_std = float(np.std(log_rets, ddof=1))
            total_dur = (n_steps - 1) * dt_years
            steps_per_year = float(n_steps - 1) / total_dur if total_dur > 0 else TRADING_DAYS_PER_YEAR
            realized_vol = float(step_std * math.sqrt(steps_per_year))

    implied_vol = legs[0]["sigma"] if legs else default_sig
    vol_spread = realized_vol - implied_vol
    symbol = legs[0]["symbol"] if legs and legs[0].get("symbol") else "SPY"
    strat_type = legs[0].get("option_type", "STRADDLE") if legs else "STRADDLE"
    strategy = f"Long {strat_type.title()}"

    result_dict: Dict[str, Any] = {
        "ok": True,
        "success": True,
        "status": "success",
        "symbol": symbol,
        "strategy": strategy,
        "total_scalping_realized_pnl": round(scalping_realized_pnl, 4),
        "stock_pnl": round(scalping_realized_pnl, 4),
        "total_transaction_costs": round(total_transaction_costs, 4),
        "scalping_net_pnl": round(scalping_net_pnl, 4),
        "option_mtm_pnl": round(option_mtm_pnl, 4),
        "option_pnl": round(option_mtm_pnl, 4),
        "total_portfolio_pnl": round(total_portfolio_pnl, 4),
        "total_pnl": round(total_portfolio_pnl, 4),
        "theoretical_gamma_rent": round(total_gamma_rent, 4),
        "theta_time_decay": round(total_theta_pnl, 4),
        "theta_decay_cost": round(theta_decay_cost, 4),
        "net_edge": round(net_edge, 4),
        "attribution": {
            "gamma_rent": round(total_gamma_rent, 4),
            "theta_decay": round(total_theta_pnl, 4),
            "transaction_costs": round(total_transaction_costs, 4),
            "net_edge": round(net_edge, 4),
            "gamma_theta_ratio": round(abs(total_gamma_rent / total_theta_pnl), 3) if abs(total_theta_pnl) > 1e-6 else None,
        },
        "rebalance_count": len([t for t in trades if "Rebalance" in t.reason or "REBALANCE" in getattr(t, "side", "").upper()]),
        "total_trades_count": len(trades),
        "initial_spot": round(s0, 4),
        "final_spot": round(final_spot, 4),
        "initial_option_value": round(initial_option_value, 4),
        "final_option_value": round(final_option_value, 4),
        "final_stock_shares": round(stock_shares, 4),
        "realized_volatility": round(realized_vol, 4),
        "implied_volatility": round(implied_vol, 4),
        "volatility_spread": round(vol_spread, 4),
        "delta_threshold": delta_threshold,
        "fee_per_share": fee_per_share,
        "trades": [t.to_dict() for t in trades],
        "hedge_history": [h.to_dict() for h in hedge_history],
        "path_history": path_history,
        "Total Scalping Realized P&L": round(scalping_realized_pnl, 4),
        "Total Transaction Costs": round(total_transaction_costs, 4),
        "Option Position Mark-to-Market P&L": round(option_mtm_pnl, 4),
        "Theoretical Gamma Rent": round(total_gamma_rent, 4),
        "Theta Time Decay": round(total_theta_pnl, 4),
        "Net Edge": round(net_edge, 4),
    }

    return GammaScalpResult(result_dict)


def to_gamma_scalp_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reshapes simulate_gamma_scalping()'s internal result dict (theoretical_gamma_rent /
    theta_time_decay / total_transaction_costs / path_history / trades-without-cumulative-
    P&L -- the shape every existing test in tests/test_gamma_scalper.py asserts on) into
    the GammaScalpResponse contract webapp/src/api/types.ts and
    webapp/src/components/options/GammaScalperView.tsx already agree on (gamma_rent_total /
    theta_burn_total / transaction_costs / pnl_path / trades with per-trade cumulative P&L).

    webapp/src/components/options/GammaScalperView.tsx reads `result.pnl_path.length`
    unconditionally on mount (the panel auto-runs a simulation via useEffect) -- the live
    endpoint was handing back a response with no `pnl_path` key at all (only `path_history`,
    a differently-shaped array), so `result.pnl_path` was `undefined` and `.length` crashed
    with "Cannot read properties of undefined (reading 'length')" every time this panel
    opened, before the operator could interact with anything.

    Per-trade `cash_flow` is recomputed from the SAME formula the simulation loop itself
    uses (`-shares * spot_price` -- see the `total_cash_flow -=` line above), and
    `stock_position`/`total_pnl`/`gamma_rent_cumulative`/`theta_decay_cumulative` are joined
    from `path_history`/`hedge_history` by the shared `step` index -- real, already-computed
    values, never fabricated (CONSTRAINT #4). `timestamp` has no wall-clock equivalent in
    this abstract step-indexed simulation, so an honest `"t+<step>"` label is used rather
    than a synthetic ISO datetime that would misrepresent it as real time.

    Kept as a separate step from simulate_gamma_scalping() itself (applied at the API
    handler for POST /pilots/options/gamma-scalp/simulate instead) so every existing
    caller/test of the pure simulation function is unaffected.
    """
    path_history = raw.get("path_history") or []
    hedge_history = raw.get("hedge_history") or []
    trades_raw = raw.get("trades") or []

    path_by_step = {p.get("step"): p for p in path_history if p.get("step") is not None}
    hedge_by_step = {h.get("step"): h for h in hedge_history if h.get("step") is not None}
    initial_snapshot = hedge_history[0] if hedge_history else {}

    def _to_trade(t: Dict[str, Any]) -> Dict[str, Any]:
        step = t.get("step")
        path_pt = path_by_step.get(step) or {}
        hedge_pt = hedge_by_step.get(step) or {}
        shares = t.get("shares", 0.0) or 0.0
        spot_price = t.get("spot_price", 0.0) or 0.0
        side_raw = str(t.get("side", "")).upper()
        side = side_raw if side_raw in ("BUY", "SELL") else "HOLD"
        return {
            "step": step,
            "timestamp": f"t+{step}",
            "spot_price": spot_price,
            "pre_delta": t.get("net_delta_before", 0.0),
            "post_delta": t.get("net_delta_after", 0.0),
            "shares_traded": shares,
            "side": side,
            "trade_price": spot_price,
            "cash_flow": round(-shares * spot_price, 4),
            "stock_position": hedge_pt.get("stock_shares", path_pt.get("stock_shares", 0.0)),
            "option_mtm": path_pt.get("option_value", 0.0),
            "total_pnl": path_pt.get("total_pnl", 0.0),
            "gamma_rent_cumulative": path_pt.get("gamma_rent", 0.0),
            "theta_decay_cumulative": path_pt.get("theta_decay", 0.0),
        }

    pnl_path = [
        {
            "step": p.get("step"),
            "spot": p.get("spot_price"),
            "total_pnl": p.get("total_pnl"),
            "gamma_rent": p.get("gamma_rent"),
            "theta_decay": p.get("theta_decay"),
            "option_mtm": p.get("option_value"),
            "stock_pnl": p.get("stock_pnl"),
        }
        for p in path_history
    ]

    price_path = [p["spot_price"] for p in path_history if p.get("spot_price") is not None]

    return {
        "symbol": raw.get("symbol", ""),
        "spot_price": raw.get("initial_spot", 0.0),
        "initial_delta": initial_snapshot.get("option_delta_shares", 0.0),
        "initial_gamma": initial_snapshot.get("option_gamma_shares", 0.0),
        "initial_theta": initial_snapshot.get("option_theta_annual", 0.0),
        "total_trades": raw.get("total_trades_count", len(trades_raw)),
        "rebalance_count": raw.get("rebalance_count", len(trades_raw)),
        "delta_threshold": raw.get("delta_threshold", 0.0),
        "total_pnl": raw.get("total_pnl", 0.0),
        # `theta_decay_cost` (not the raw `theta_time_decay` P&L, which can be negative) --
        # the frontend renders this as a fixed "-$..." magnitude, matching the codebase's
        # own theta_decay_cost = -theta_time_decay-when-negative-else-0 convention above.
        "gamma_rent_total": raw.get("theoretical_gamma_rent", 0.0),
        "theta_burn_total": raw.get("theta_decay_cost", 0.0),
        "stock_pnl": raw.get("stock_pnl", 0.0),
        "option_pnl": raw.get("option_pnl", 0.0),
        "transaction_costs": raw.get("total_transaction_costs", 0.0),
        "net_edge": raw.get("net_edge", 0.0),
        "trades": [_to_trade(t) for t in trades_raw],
        "price_path": price_path,
        "pnl_path": pnl_path,
    }


def generate_gbm_price_path(
    s0: float = 100.0,
    mu: float = 0.0,
    sigma: float = 0.30,
    total_time_years: float = 1.0 / 252.0,
    n_steps: int = 100,
    seed: Optional[int] = None,
) -> List[float]:
    """
    Generates a Geometric Brownian Motion spot price path for simulation and testing.
    """
    if n_steps <= 1:
        return [float(s0)]

    if seed is not None:
        np.random.seed(seed)

    dt = total_time_years / (n_steps - 1)
    drift = (mu - 0.5 * sigma ** 2) * dt
    vol = sigma * math.sqrt(dt)

    random_shocks = np.random.normal(0, 1, n_steps - 1)
    prices = [float(s0)]
    curr_s = float(s0)

    for shock in random_shocks:
        curr_s *= math.exp(drift + vol * shock)
        prices.append(float(curr_s))

    return prices


def generate_synthetic_price_path(
    initial_spot: float = 100.0,
    annual_vol: float = 0.25,
    n_steps: int = 50,
    dt_days: float = 0.1,
    seed: Optional[int] = 42,
) -> List[float]:
    """
    Generates synthetic price path with daily dt step.
    """
    return generate_gbm_price_path(
        s0=initial_spot,
        mu=0.0,
        sigma=annual_vol,
        total_time_years=(n_steps * dt_days) / TRADING_DAYS_PER_YEAR,
        n_steps=n_steps,
        seed=seed,
    )
