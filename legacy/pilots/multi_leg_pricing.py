"""
InvestYo Quant Platform - Multi-Leg Options Pricing & Greeks Engine
===================================================================
Provides analytical Black-Scholes pricing, composite Greeks, payoff curves,
max profit / max loss calculations, and break-even points for multi-leg
option structures (Vertical Spreads, Iron Condors, Straddles, Strangles,
Calendar Spreads, Butterflies, and Custom Leg Combinations).

Invariants:
- AST Safety: stdlib, math, numpy, scipy.stats, pandas, plus
  ``pilots.options_risk`` (the canonical Black-Scholes pricer, F4 dedup --
  see ``calculate_black_scholes_leg_greeks``'s own docstring below; no
  heavy engines).
- Numerical Guards: 0DTE intrinsic fallback (T <= 1e-12), volatility clipping (sigma <= 1e-12).
- Zero Lookahead: Calculations are pure instantaneous analytical pricing functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import numpy as np

from pilots.options_risk import calculate_black_scholes_greeks
from settings import settings

TRADING_DAYS_PER_YEAR = 252.0


@dataclass
class OptionLegSpec:
    """Specification of an individual option leg in a multi-leg structure."""

    strike: float
    option_type: Literal["call", "put", "CALL", "PUT"]
    action: Literal["buy", "sell", "BUY", "SELL"]
    ratio: int = 1
    expiration: Optional[str] = None  # YYYY-MM-DD format
    premium: Optional[float] = None  # Market price per share (e.g. 2.50)
    iv: Optional[float] = None  # Implied volatility (e.g. 0.25 for 25%)

    def normalized_type(self) -> str:
        return self.option_type.lower().strip()

    def normalized_action(self) -> str:
        return self.action.lower().strip()

    def signed_ratio(self) -> int:
        """Returns positive ratio for long/buy, negative ratio for short/sell."""
        return self.ratio if self.normalized_action() == "buy" else -self.ratio


def calculate_black_scholes_leg_greeks(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: Optional[float] = None,
) -> Dict[str, float]:
    """Calculates Black-Scholes analytical price and per-share Greeks for a
    single leg.

    Delegates to ``pilots.options_risk.calculate_black_scholes_greeks`` (F4,
    docs/module_efficiency_redundancy_audit.md) -- this was a near-verbatim
    copy of that canonical implementation, byte-for-byte identical on every
    formula (d1/d2/price/delta/theta/gamma/vega and all degenerate-input
    guards), confirmed via a seeded numeric-equivalence grid before this
    migration (tests/test_multi_leg_pricing.py). Returns a superset of this
    function's original 5-key contract (``price``, ``delta``, ``gamma``,
    ``theta_daily``, ``vega_1pct`` all still present with identical values)
    plus the canonical function's extra fields (``theta_annual``, ``vega``,
    ``vega_raw``, ``rho``, ``rho_1pct``, ``rho_raw``, ``intrinsic``,
    ``extrinsic``) -- a caller reading only the original 5 keys is
    unaffected. One behavioral improvement, strictly additive: the canonical
    function normalizes ``option_type`` case/whitespace
    (``str(option_type or "call").lower().strip()``) before comparing to
    ``"call"``/``"put"``, where this copy compared the raw string directly --
    an uppercase ``"CALL"`` previously fell through to the put branch here.
    """
    return calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=option_type,
        r=r,
    )


def parse_dte_to_years(expiration_str: Optional[str], as_of_date: Optional[Union[str, date, datetime]] = None) -> float:
    """Parses an expiration date string (YYYY-MM-DD) into time to expiry in years."""
    if not expiration_str:
        return 30.0 / TRADING_DAYS_PER_YEAR  # Default 30 DTE if omitted

    try:
        exp_d = date.fromisoformat(expiration_str.strip())
        if as_of_date is None:
            cur_d = date.today()
        elif isinstance(as_of_date, (datetime, date)):
            cur_d = as_of_date if isinstance(as_of_date, date) else as_of_date.date()
        else:
            cur_d = date.fromisoformat(str(as_of_date).strip()[:10])

        days = (exp_d - cur_d).days
        if days <= 0:
            return 0.0
        return float(days) / 365.0
    except Exception:
        return 30.0 / TRADING_DAYS_PER_YEAR


def validate_multi_leg_structure(
    structure_type: str,
    legs: List[OptionLegSpec],
) -> Tuple[bool, List[str]]:
    """Validates structural correctness of standard multi-leg option strategies.

    Returns:
        (is_valid, validation_errors_list)
    """
    errors: List[str] = []
    if not legs or len(legs) == 0:
        return False, ["At least one leg is required in multi-leg specification."]

    for i, leg in enumerate(legs):
        if leg.strike <= 0:
            errors.append(f"Leg {i+1}: Strike price must be strictly positive (got {leg.strike}).")
        if leg.ratio <= 0:
            errors.append(f"Leg {i+1}: Contract ratio must be >= 1 (got {leg.ratio}).")
        if leg.normalized_type() not in ("call", "put"):
            errors.append(f"Leg {i+1}: Invalid option type '{leg.option_type}' (must be CALL or PUT).")
        if leg.normalized_action() not in ("buy", "sell"):
            errors.append(f"Leg {i+1}: Invalid action '{leg.action}' (must be BUY or SELL).")

    st_upper = structure_type.upper().replace("-", "_").replace(" ", "_")

    if st_upper in ("IRON_CONDOR", "CONDOR"):
        if len(legs) != 4:
            errors.append(f"Iron Condor requires exactly 4 legs (got {len(legs)}).")
        else:
            calls = [l for l in legs if l.normalized_type() == "call"]
            puts = [l for l in legs if l.normalized_type() == "put"]
            if len(calls) != 2 or len(puts) != 2:
                errors.append("Iron Condor must consist of exactly 2 put legs and 2 call legs.")
            else:
                sorted_puts = sorted(puts, key=lambda x: x.strike)
                sorted_calls = sorted(calls, key=lambda x: x.strike)
                if sorted_puts[-1].strike >= sorted_calls[0].strike:
                    errors.append(
                        f"Iron Condor strikes must have Put strikes < Call strikes "
                        f"(Highest Put: {sorted_puts[-1].strike} >= Lowest Call: {sorted_calls[0].strike})."
                    )

    elif st_upper in ("VERTICAL_SPREAD", "BULL_CALL_SPREAD", "BEAR_PUT_SPREAD", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
        if len(legs) != 2:
            errors.append(f"Vertical Spread requires exactly 2 legs (got {len(legs)}).")
        else:
            if legs[0].normalized_type() != legs[1].normalized_type():
                errors.append(f"Vertical spread legs must be the same option type (got {legs[0].option_type} and {legs[1].option_type}).")
            if legs[0].strike == legs[1].strike:
                errors.append(f"Vertical spread legs must have different strike prices (both are {legs[0].strike}).")
            if legs[0].normalized_action() == legs[1].normalized_action():
                errors.append("Vertical spread must have 1 BUY leg and 1 SELL leg.")

    elif st_upper in ("STRADDLE", "LONG_STRADDLE", "SHORT_STRADDLE"):
        if len(legs) != 2:
            errors.append(f"Straddle requires exactly 2 legs (got {len(legs)}).")
        else:
            types = {l.normalized_type() for l in legs}
            if types != {"call", "put"}:
                errors.append("Straddle must contain exactly 1 Call and 1 Put.")
            if legs[0].strike != legs[1].strike:
                errors.append(f"Straddle requires identical strikes across Call and Put (got {legs[0].strike} and {legs[1].strike}).")

    elif st_upper in ("STRANGLE", "LONG_STRANGLE", "SHORT_STRANGLE"):
        if len(legs) != 2:
            errors.append(f"Strangle requires exactly 2 legs (got {len(legs)}).")
        else:
            call_leg = next((l for l in legs if l.normalized_type() == "call"), None)
            put_leg = next((l for l in legs if l.normalized_type() == "put"), None)
            if not call_leg or not put_leg:
                errors.append("Strangle must contain 1 Call and 1 Put.")
            elif put_leg.strike >= call_leg.strike:
                errors.append(f"Strangle requires Put strike < Call strike (got Put={put_leg.strike}, Call={call_leg.strike}).")

    return len(errors) == 0, errors


def price_multi_leg_structure(
    spot: float,
    legs: List[OptionLegSpec],
    default_iv: float = 0.30,
    r: Optional[float] = None,
    grid_points: int = 100,
) -> Dict[str, Any]:
    """Calculates theoretical prices, composite net Greeks, net entry cost/credit,
    max profit, max loss, risk/reward, break-evens, and expiration payoff curve.
    """
    if spot <= 0:
        raise ValueError(f"Underlying spot price must be positive (got {spot}).")

    leg_results: List[Dict[str, Any]] = []
    net_premium_per_share = 0.0
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    all_strikes = [l.strike for l in legs]
    min_strike = min(all_strikes) if all_strikes else spot
    max_strike = max(all_strikes) if all_strikes else spot

    for leg in legs:
        t_years = parse_dte_to_years(leg.expiration)
        sigma = leg.iv if (leg.iv is not None and leg.iv > 0) else default_iv
        greeks = calculate_black_scholes_leg_greeks(
            spot=spot,
            strike=leg.strike,
            t_years=t_years,
            sigma=sigma,
            option_type=leg.normalized_type(),
            r=r,
        )

        theo_price = greeks["price"]
        actual_price = leg.premium if (leg.premium is not None and leg.premium >= 0) else theo_price
        multiplier = leg.signed_ratio()

        # Net premium cashflow: selling receives credit (+), buying costs debit (-)
        leg_cashflow = -multiplier * actual_price
        net_premium_per_share += leg_cashflow

        net_delta += multiplier * greeks["delta"]
        net_gamma += multiplier * greeks["gamma"]
        net_theta += multiplier * greeks["theta_daily"]
        net_vega += multiplier * greeks["vega_1pct"]

        leg_results.append({
            "strike": leg.strike,
            "option_type": leg.normalized_type(),
            "action": leg.normalized_action(),
            "ratio": leg.ratio,
            "expiration": leg.expiration,
            "theoretical_price": round(theo_price, 4),
            "entry_price": round(actual_price, 4),
            "delta": round(greeks["delta"], 4),
            "gamma": round(greeks["gamma"], 6),
            "theta_daily": round(greeks["theta_daily"], 4),
            "vega_1pct": round(greeks["vega_1pct"], 4),
        })

    # Payoff curve across spot range
    grid_min = max(0.01, min(0.7 * spot, min_strike * 0.85))
    grid_max = max(1.3 * spot, max_strike * 1.15)
    spot_grid = np.linspace(grid_min, grid_max, grid_points)

    payoff_curve: List[Dict[str, float]] = []
    payoffs_100: List[float] = []

    for S in spot_grid:
        total_payoff_per_contract = 0.0
        for leg in legs:
            K = leg.strike
            p = leg.premium if (leg.premium is not None and leg.premium >= 0) else leg_results[legs.index(leg)]["theoretical_price"]
            if leg.normalized_type() == "call":
                intrinsic = max(0.0, S - K)
            else:
                intrinsic = max(0.0, K - S)

            if leg.normalized_action() == "buy":
                total_payoff_per_contract += (intrinsic - p) * 100.0 * leg.ratio
            else:
                total_payoff_per_contract += (-intrinsic + p) * 100.0 * leg.ratio

        payoffs_100.append(total_payoff_per_contract)
        payoff_curve.append({
            "spot": round(float(S), 2),
            "payoff": round(float(total_payoff_per_contract), 2),
        })

    min_payoff = float(np.min(payoffs_100))
    max_payoff = float(np.max(payoffs_100))

    # Break-even crossings (root finding along payoff grid)
    breakevens: List[float] = []
    for i in range(len(payoffs_100) - 1):
        y1 = payoffs_100[i]
        y2 = payoffs_100[i + 1]
        if (y1 <= 0.0 and y2 >= 0.0) or (y1 >= 0.0 and y2 <= 0.0):
            if abs(y2 - y1) > 1e-9:
                x1 = spot_grid[i]
                x2 = spot_grid[i + 1]
                root_x = x1 - y1 * (x2 - x1) / (y2 - y1)
                breakevens.append(round(float(root_x), 2))

    is_credit = net_premium_per_share > 0.0
    net_cost_or_credit_100 = round(abs(net_premium_per_share) * 100.0, 2)

    return {
        "spot_price": round(spot, 2),
        "structure_type": "Multi-Leg Option",
        "net_order_action": "CREDIT" if is_credit else "DEBIT",
        "net_premium_per_share": round(net_premium_per_share, 4),
        "net_cashflow_per_contract": round(net_premium_per_share * 100.0, 2),
        "composite_greeks": {
            "net_delta_shares": round(net_delta * 100.0, 3),
            "net_delta_per_share": round(net_delta, 4),
            "net_gamma": round(net_gamma * 100.0, 5),
            "net_theta_daily": round(net_theta * 100.0, 3),
            "net_vega_1pct": round(net_vega * 100.0, 3),
        },
        "max_profit": round(max_payoff, 2) if max_payoff < 1e8 else None,
        "max_loss": round(abs(min_payoff), 2) if min_payoff > -1e8 else None,
        "is_defined_risk": min_payoff > -1e8,
        "breakeven_points": breakevens,
        "legs": leg_results,
        "payoff_curve": payoff_curve,
    }
