"""pilots/options_sor.py — Multi-Leg Options Smart Order Router (SOR) & Legging Optimization Engine.
=====================================================================================================

Implements quantitative multi-leg options routing analytics and synthetic legging optimization:

1. **Complex Order Book (COB) vs. Synthetic Legging**:
   - **Direct COB Net Route (`COB_NET_PACKAGE`)**: Submits all legs simultaneously as a net package
     order to the exchange complex order book. Guarantees atomic execution (zero hung-leg risk).
   - **Synthetic Legging-In (`LEG_PASSIVE_FIRST`)**: Fills the passive (wide-spread / illiquid) leg
     first at the bid/ask or near touch to capture spread edge, then immediately crosses the market
     on the active (tight-spread / liquid) leg.
   - **Split Direct Routing (`SPLIT_DIRECT`)**: Dispatches direct limit orders across distinct venue
     books with adaptive pegging.

2. **Routing Optimization & Decision Policy Matrix**:
   - Compares Net Mid Price, Net Natural Price, and Net Passive Price.
   - Models expected fill probability in COB based on aggregate relative spread width and leg count.
   - Computes Expected Spread Savings ($) vs. Expected Adverse Selection / Legging Hazard ($).
   - Selects optimal policy: `COB_NET_PACKAGE`, `LEG_PASSIVE_FIRST`, or `SPLIT_DIRECT`.

3. **Legging Hazard & Adverse Selection Simulator**:
   - Monte Carlo simulation of price drift during inter-leg execution latency (Delta t).
   - Computes probability of hung leg, expected adverse selection cost, and net edge distribution.

AST & Architecture Invariants:
- Standalone / AST-safe: imports only standard library (`dataclasses`, `math`, `re`, `typing`, `logging`),
  `numpy`, and `scipy.stats`.
- Pure quantitative math — NEVER imports heavy forbidden engines (processing_engine,
  strategy_engine, forecasting_engine, macro_engine, technical_options_engine, etc.).
- Robust / Non-raising (CONSTRAINT #6) — handles empty/missing quotes honestly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from scipy.stats import norm

from settings import settings

logger = logging.getLogger(__name__)

# Execution Policies
POLICY_COB_NET_PACKAGE = "COB_NET_PACKAGE"
POLICY_LEG_PASSIVE_FIRST = "LEG_PASSIVE_FIRST"
POLICY_SPLIT_DIRECT = "SPLIT_DIRECT"

__all__ = [
    "POLICY_COB_NET_PACKAGE",
    "POLICY_LEG_PASSIVE_FIRST",
    "POLICY_SPLIT_DIRECT",
    "LeggingSimulationResult",
    "RoutingAnalysisResult",
    "analyze_routing_options",
    "simulate_legging_execution",
    "parse_leg_symbol",
    "calculate_leg_greeks",
]

# Standard option symbol format: AAPL 2026-09-18 $150.00 CALL
_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)

_DEGENERATE_EPSILON = 1e-12
TRADING_DAYS_PER_YEAR = 252.0
TRADING_SECONDS_PER_YEAR = 252.0 * 6.5 * 3600.0


@dataclass
class RoutingAnalysisResult:
    """Structured result from analyzing multi-leg order execution routing."""
    valid: bool
    symbol: str
    strategy_type: str
    spot_price: float
    legs_count: int
    order_size: int
    cob_pricing: Optional[Dict[str, Any]]
    synthetic_legging: Optional[Dict[str, Any]]
    recommended_policy: str
    policy_rationale: str
    policies_comparison: List[Dict[str, Any]]
    legs_breakdown: List[Dict[str, Any]]
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class LeggingSimulationResult:
    """Structured result from Monte Carlo inter-leg hazard & adverse selection simulation."""
    valid: bool
    num_simulations: int
    latency_seconds: float
    spot_price: float
    volatility: float
    probability_of_hung_leg: float  # [0.0, 1.0] (% of runs where second leg moved against trader)
    hung_leg_percentage: float      # [0.0, 100.0]
    avg_slippage_cost: float        # Average Slippage / Adverse Selection Cost ($)
    expected_net_edge_captured: float  # Expected Net Edge Captured ($)
    initial_spread_edge: float      # Gross initial spread edge ($)
    initial_net_mid: float          # Initial package net mid ($)
    initial_net_natural: float      # Initial package net natural ($)
    fill_price_mean: float          # Mean net spread fill price ($)
    fill_price_std: float           # Std dev of net spread fill prices
    fill_price_median: float        # Median net spread fill price ($)
    fill_price_p5: float            # 5th percentile net spread fill price
    fill_price_p25: float           # 25th percentile net spread fill price
    fill_price_p75: float           # 75th percentile net spread fill price
    fill_price_p95: float           # 95th percentile net spread fill price
    fill_price_min: float           # Minimum net spread fill price
    fill_price_max: float           # Maximum net spread fill price
    distribution: Dict[str, Any]    # Distribution of net spread fill prices
    recommended_policy: str         # COB_NET_PACKAGE, LEG_PASSIVE_FIRST, SPLIT_DIRECT
    reason: str
    passive_leg: str = ""
    active_leg: str = ""
    gross_spread_savings: float = 0.0
    expected_slippage: float = 0.0
    expected_net_savings: float = 0.0
    hung_leg_probability: float = 0.0
    savings_p5: float = 0.0
    savings_p50: float = 0.0
    savings_p95: float = 0.0
    var_95: float = 0.0
    is_legging_favorable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def parse_leg_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Parses standardized option leg symbol string into components."""
    if not isinstance(symbol, str):
        return None
    m = _OPTION_SYM_RE.match(symbol.strip())
    if not m:
        return None
    return {
        "ticker": m.group("ticker").upper(),
        "expiration": m.group("exp"),
        "strike": float(m.group("strike")),
        "option_type": m.group("type").lower(),
    }


def calculate_leg_greeks(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float = 0.25,
    option_type: str = "call",
    r: float = 0.045,
) -> Dict[str, float]:
    """Computes Black-Scholes Delta and Gamma for a single option leg (delegates to canonical pilots.options_risk)."""
    from pilots.options_risk import calculate_black_scholes_greeks

    greeks = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=option_type,
        r=r,
    )
    return {
        "delta": float(greeks.get("delta", 0.0)),
        "gamma": float(greeks.get("gamma", 0.0)),
        "price": float(greeks.get("price", 0.0)),
    }


def _normalize_leg_data(
    leg: Dict[str, Any],
    idx: int,
    spot_price: float,
    quotes_map: Optional[Dict[str, Any]] = None,
    volatility: float = 0.25,
) -> Dict[str, Any]:
    """Normalizes and validates raw leg payload into standardized quoting and Greek fields."""
    contract = leg.get("contract") or {}
    if not isinstance(contract, dict):
        contract = {}

    # Extract symbol / identifier
    symbol = str(
        leg.get("symbol")
        or leg.get("contractSymbol")
        or contract.get("contractSymbol")
        or ""
    ).strip()

    # Look up in quotes_map if available
    q_data = {}
    if quotes_map and isinstance(quotes_map, dict):
        if symbol and symbol in quotes_map:
            q_data = quotes_map[symbol] or {}
        elif str(idx) in quotes_map:
            q_data = quotes_map[str(idx)] or {}

    # Parse action / side (buy vs sell)
    action_raw = str(leg.get("action") or leg.get("side") or "buy").lower().strip()
    action = "sell" if action_raw in ("sell", "short", "ask") else "buy"

    # Strike, Option Type, Expiration
    parsed = parse_leg_symbol(symbol) if symbol else None
    strike = float(
        leg.get("strike")
        or contract.get("strike")
        or (parsed["strike"] if parsed else 0.0)
        or 0.0
    )
    opt_type = str(
        leg.get("type")
        or leg.get("option_type")
        or contract.get("type")
        or (parsed["option_type"] if parsed else "call")
        or "call"
    ).lower()

    exp_str = str(
        leg.get("expiration")
        or contract.get("expiration")
        or (parsed["expiration"] if parsed else "")
        or ""
    ).strip()

    ratio = float(leg.get("ratio") or leg.get("qty") or leg.get("quantity") or 1.0)
    if ratio <= 0:
        ratio = 1.0

    # Pricing & Liquidity Extraction
    bid = float(
        leg.get("bid")
        or q_data.get("bid")
        or contract.get("bid")
        or 0.0
    )
    ask = float(
        leg.get("ask")
        or q_data.get("ask")
        or contract.get("ask")
        or 0.0
    )
    last = float(
        leg.get("last_price")
        or leg.get("lastPrice")
        or q_data.get("lastPrice")
        or q_data.get("last")
        or contract.get("lastPrice")
        or 0.0
    )
    volume = leg.get("volume") or q_data.get("volume") or contract.get("volume")
    open_interest = (
        leg.get("openInterest")
        or leg.get("open_interest")
        or q_data.get("openInterest")
        or contract.get("openInterest")
    )
    iv = float(
        leg.get("impliedVolatility")
        or leg.get("iv")
        or q_data.get("impliedVolatility")
        or contract.get("impliedVolatility")
        or volatility
        or 0.25
    )

    # Clean crossed or missing quotes
    if bid < 0:
        bid = 0.0
    if ask < 0:
        ask = 0.0
    if ask < bid and ask > 0:
        bid, ask = ask, bid

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
    elif ask > 0:
        mid = ask
    elif bid > 0:
        mid = bid
    elif last > 0:
        mid = last
        bid = max(0.0, last * 0.98)
        ask = last * 1.02
    else:
        mid = 0.0

    spread = max(0.0, ask - bid)
    rel_spread = (spread / mid) if mid > 0 else (0.5 if spread > 0 else 0.0)

    # Natural & Passive prices
    if action == "buy":
        natural_price = ask if ask > 0 else mid
        passive_price = bid if bid > 0 else mid
    else:
        natural_price = bid if bid > 0 else mid
        passive_price = ask if ask > 0 else mid

    # Calculate DTE & Greeks
    dte = 30.0
    if exp_str:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            dte = max(0.0, (exp_date - now).total_seconds() / 86400.0)
        except Exception:
            dte = 30.0

    t_years = max(0.0, dte / 365.0)

    # Extract or compute delta
    greeks_contract = contract.get("greeks") or {}
    delta = leg.get("delta") or greeks_contract.get("delta")
    gamma = leg.get("gamma") or greeks_contract.get("gamma")

    if delta is None or gamma is None:
        bs = calculate_leg_greeks(
            spot=spot_price if spot_price > 0 else strike,
            strike=strike,
            t_years=t_years,
            sigma=iv if iv > 0 else volatility,
            option_type=opt_type,
        )
        if delta is None:
            delta = bs["delta"]
        if gamma is None:
            gamma = bs["gamma"]

    delta = float(delta)
    gamma = float(gamma)

    # Liquidity score [0.0, 1.0]
    spread_score = 1.0 / (1.0 + 8.0 * rel_spread)
    vol_num = float(volume) if volume is not None and str(volume).replace(".", "", 1).isdigit() else 0.0
    oi_num = float(open_interest) if open_interest is not None and str(open_interest).replace(".", "", 1).isdigit() else 0.0
    activity_bonus = min(0.2, (math.log10(max(1.0, vol_num + oi_num)) / 5.0) * 0.2)
    liquidity_score = float(np.clip(spread_score * 0.8 + activity_bonus, 0.05, 1.0))

    if not symbol:
        symbol = f"LEG_{idx+1} {exp_str} ${strike:.2f} {opt_type.upper()}".strip()

    return {
        "index": idx,
        "symbol": symbol,
        "action": action,
        "strike": round(strike, 2),
        "option_type": opt_type,
        "expiration": exp_str,
        "dte": round(dte, 1),
        "ratio": ratio,
        "bid": round(bid, 4),
        "ask": round(ask, 4),
        "mid": round(mid, 4),
        "spread": round(spread, 4),
        "relative_spread": round(rel_spread, 4),
        "natural_price": round(natural_price, 4),
        "passive_price": round(passive_price, 4),
        "volume": volume,
        "open_interest": open_interest,
        "iv": round(iv, 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "liquidity_score": round(liquidity_score, 4),
        "role": "neutral",
    }


def analyze_routing_options(
    legs: List[Dict[str, Any]],
    spot_price: float,
    quotes_map: Optional[Dict[str, Any]] = None,
    volatility: float = 0.25,
    order_size: int = 1,
) -> RoutingAnalysisResult:
    """Analyzes execution routing options for single or multi-leg option orders.

    Evaluates:
    - Complex Order Book (COB) Net Mid, Natural, Passive pricing & fill probability.
    - Synthetic Legging pricing (Passive-First vs Active-First spread capture).
    - Expected Legging Savings ($) vs Adverse Selection Hazard Risk ($).
    - Optimal policy recommendation: `COB_NET_PACKAGE`, `LEG_PASSIVE_FIRST`, or `SPLIT_DIRECT`.
    """
    if not legs or not isinstance(legs, list):
        return RoutingAnalysisResult(
            valid=False,
            reason="No option legs provided for routing analysis.",
            symbol="UNKNOWN",
            strategy_type="None",
            spot_price=float(spot_price or 0.0),
            legs_count=0,
            order_size=order_size,
            cob_pricing=None,
            synthetic_legging=None,
            recommended_policy=POLICY_COB_NET_PACKAGE,
            policy_rationale="No legs to route.",
            policies_comparison=[],
            legs_breakdown=[],
        )

    parsed_legs: List[Dict[str, Any]] = []
    for idx, leg_item in enumerate(legs):
        norm_leg = _normalize_leg_data(
            leg_item,
            idx=idx,
            spot_price=spot_price,
            quotes_map=quotes_map,
            volatility=volatility,
        )
        parsed_legs.append(norm_leg)

    legs_count = len(parsed_legs)

    # Detect Strategy Name
    types = {pl["option_type"] for pl in parsed_legs}
    expirations = {pl["expiration"] for pl in parsed_legs if pl["expiration"]}
    strikes = [pl["strike"] for pl in parsed_legs]

    if legs_count == 1:
        strategy_type = f"Single-Leg {parsed_legs[0]['option_type'].capitalize()}"
    elif legs_count == 2:
        if len(expirations) > 1:
            strategy_type = "Calendar Spread"
        elif len(types) == 1:
            strategy_type = "Vertical Spread"
        else:
            strategy_type = "Straddle/Strangle"
    elif legs_count == 4:
        strategy_type = "Iron Condor"
    else:
        strategy_type = f"{legs_count}-Leg Strategy"

    base_symbol = "MULTI"
    if parsed_legs and parsed_legs[0]["symbol"]:
        sym_parts = parsed_legs[0]["symbol"].split()
        if sym_parts:
            base_symbol = sym_parts[0]

    # COB Calculations
    cob_mid_sum = 0.0
    cob_nat_sum = 0.0
    cob_pass_sum = 0.0
    total_spread_sum = 0.0
    sum_mids = 0.0

    for pl in parsed_legs:
        sign = 1.0 if pl["action"] == "buy" else -1.0
        q = pl["ratio"]
        cob_mid_sum += sign * q * pl["mid"]
        cob_nat_sum += sign * q * pl["natural_price"]
        cob_pass_sum += sign * q * pl["passive_price"]
        total_spread_sum += q * pl["spread"]
        sum_mids += q * pl["mid"]

    cob_spread_width = abs(cob_nat_sum - cob_pass_sum)
    is_net_debit = cob_mid_sum >= 0.0
    avg_rel_spread = (total_spread_sum / sum_mids) if sum_mids > 0 else 0.10

    fill_prob_nat = 0.98
    fill_prob_mid = float(
        np.clip(
            0.85 * math.exp(-3.2 * avg_rel_spread) * (1.0 - 0.06 * max(0, legs_count - 1)),
            0.08,
            0.92,
        )
    )

    cob_pricing = {
        "net_mid": round(cob_mid_sum, 4),
        "net_natural": round(cob_nat_sum, 4),
        "net_passive": round(cob_pass_sum, 4),
        "spread_width": round(cob_spread_width, 4),
        "is_net_debit": is_net_debit,
        "fill_probability_mid": round(fill_prob_mid, 4),
        "fill_probability_natural": round(fill_prob_nat, 4),
    }

    # Synthetic Legging Analysis
    if legs_count >= 2:
        sorted_indices = sorted(
            range(legs_count),
            key=lambda i: (parsed_legs[i]["spread"], -parsed_legs[i]["liquidity_score"]),
            reverse=True,
        )
        passive_idx = sorted_indices[0]
        active_indices = sorted_indices[1:]
        active_idx = active_indices[0]

        parsed_legs[passive_idx]["role"] = "passive"
        for ai in active_indices:
            parsed_legs[ai]["role"] = "active"

        passive_leg = parsed_legs[passive_idx]
        active_leg = parsed_legs[active_idx]

        synth_pass_first_sum = 0.0
        for i, pl in enumerate(parsed_legs):
            sign = 1.0 if pl["action"] == "buy" else -1.0
            q = pl["ratio"]
            if i == passive_idx:
                synth_pass_first_sum += sign * q * pl["passive_price"]
            else:
                synth_pass_first_sum += sign * q * pl["natural_price"]

        expected_spread_savings = abs(cob_nat_sum - synth_pass_first_sum)

        tau_seconds = 2.5 * (1.0 + 5.0 * passive_leg["relative_spread"])
        tau_years = tau_seconds / (TRADING_DAYS_PER_YEAR * 6.5 * 3600.0)

        passive_sign = 1.0 if passive_leg["action"] == "buy" else -1.0
        unhedged_delta = abs(passive_sign * passive_leg["ratio"] * passive_leg["delta"])

        spot_val = spot_price if spot_price > 0 else (strikes[0] if strikes else 100.0)
        expected_spot_drift = spot_val * (volatility or 0.25) * math.sqrt(tau_years)

        adverse_hazard = unhedged_delta * expected_spot_drift
        net_edge = expected_spread_savings - adverse_hazard

        active_spread = active_leg["spread"]
        threshold_move = max(0.02, 0.5 * active_spread)
        if spot_val > 0 and (volatility or 0.25) > 0:
            sigma_opt = max(0.001, active_leg["delta"] * spot_val * (volatility or 0.25) * math.sqrt(tau_years))
            hung_prob = float(np.clip(2.0 * (1.0 - norm.cdf(threshold_move / sigma_opt)), 0.02, 0.85))
        else:
            hung_prob = 0.15

        synthetic_legging = {
            "passive_leg_index": passive_idx,
            "active_leg_index": active_idx,
            "passive_leg_symbol": passive_leg["symbol"],
            "active_leg_symbol": active_leg["symbol"],
            "net_price_passive_first": round(synth_pass_first_sum, 4),
            "expected_spread_savings": round(expected_spread_savings, 4),
            "estimated_adverse_hazard": round(adverse_hazard, 4),
            "net_edge": round(net_edge, 4),
            "hung_leg_probability": round(hung_prob, 4),
            "inter_leg_working_seconds": round(tau_seconds, 1),
        }
    else:
        passive_idx = 0
        active_idx = 0
        parsed_legs[0]["role"] = "direct"
        expected_spread_savings = 0.0
        adverse_hazard = 0.0
        net_edge = 0.0
        hung_prob = 0.0
        synthetic_legging = {
            "passive_leg_index": 0,
            "active_leg_index": 0,
            "passive_leg_symbol": parsed_legs[0]["symbol"],
            "active_leg_symbol": parsed_legs[0]["symbol"],
            "net_price_passive_first": round(cob_nat_sum, 4),
            "expected_spread_savings": 0.0,
            "estimated_adverse_hazard": 0.0,
            "net_edge": 0.0,
            "hung_leg_probability": 0.0,
            "inter_leg_working_seconds": 0.0,
        }

    # Routing Policy Selection
    if legs_count == 1:
        recommended_policy = POLICY_SPLIT_DIRECT
        policy_rationale = "Single-leg order routed direct to primary exchange book with adaptive midpoint pegging."
    elif legs_count > 2:
        recommended_policy = POLICY_COB_NET_PACKAGE
        policy_rationale = (
            f"{strategy_type} ({legs_count} legs) requires atomic execution via Complex Order Book (COB) "
            f"to eliminate compounding multi-leg legging hazard."
        )
    else:
        max_spread = max(parsed_legs[0]["spread"], parsed_legs[1]["spread"])
        min_spread = min(parsed_legs[0]["spread"], parsed_legs[1]["spread"])
        is_asymmetric = (max_spread >= 2.0 * max(0.01, min_spread)) and (max_spread >= 0.08)
        is_edge_positive = net_edge > 0.02
        is_hazard_acceptable = hung_prob < 0.35 and (volatility or 0.25) < 0.60

        if is_asymmetric and is_edge_positive and is_hazard_acceptable:
            recommended_policy = POLICY_LEG_PASSIVE_FIRST
            policy_rationale = (
                f"Spread asymmetry (${max_spread:.2f} vs ${min_spread:.2f}) provides "
                f"${expected_spread_savings * 100:.2f}/contract spread capture edge in LEG_PASSIVE_FIRST, "
                f"outweighing estimated adverse hazard of ${adverse_hazard * 100:.2f}."
            )
        elif order_size >= 10 and max_spread >= 0.15:
            recommended_policy = POLICY_SPLIT_DIRECT
            policy_rationale = (
                f"Large order size ({order_size} contracts) and wide spread (${cob_spread_width:.2f}) "
                f"benefits from SPLIT_DIRECT slicing across venue books."
            )
        else:
            recommended_policy = POLICY_COB_NET_PACKAGE
            policy_rationale = (
                f"Tight spread (${cob_spread_width:.2f}) and symmetric liquidity make COB_NET_PACKAGE optimal, "
                f"guaranteeing atomic execution at net mid/limit without hung-leg risk."
            )

    policies_comparison = [
        {
            "policy": POLICY_COB_NET_PACKAGE,
            "name": "COB Net Package",
            "estimated_net_price": round(cob_mid_sum if fill_prob_mid >= 0.5 else cob_nat_sum, 4),
            "execution_speed_seconds": 0.5,
            "hung_leg_risk": 0.0,
            "fill_probability": round(fill_prob_mid, 4),
            "recommended": recommended_policy == POLICY_COB_NET_PACKAGE,
            "description": "Simultaneous execution via Complex Order Book; guaranteed atomic fill, zero legging risk.",
        },
        {
            "policy": POLICY_LEG_PASSIVE_FIRST,
            "name": "Synthetic Leg Passive-First",
            "estimated_net_price": round(synthetic_legging["net_price_passive_first"], 4),
            "execution_speed_seconds": round(synthetic_legging["inter_leg_working_seconds"] + 0.5, 1),
            "hung_leg_risk": round(synthetic_legging["hung_leg_probability"], 4),
            "fill_probability": round(0.75 * (1.0 - synthetic_legging["hung_leg_probability"]), 4),
            "recommended": recommended_policy == POLICY_LEG_PASSIVE_FIRST,
            "description": "Post passive limit order on wider-spread leg, sweep active leg upon fill to capture spread edge.",
        },
        {
            "policy": POLICY_SPLIT_DIRECT,
            "name": "Split Direct Routing",
            "estimated_net_price": round(cob_mid_sum, 4),
            "execution_speed_seconds": 3.0,
            "hung_leg_risk": round(min(0.50, hung_prob * 1.3), 4),
            "fill_probability": round(max(0.20, fill_prob_mid * 0.9), 4),
            "recommended": recommended_policy == POLICY_SPLIT_DIRECT,
            "description": "Direct venue limit routing with adaptive midpoint pegging across all legs.",
        },
    ]

    return RoutingAnalysisResult(
        valid=True,
        symbol=base_symbol,
        strategy_type=strategy_type,
        spot_price=round(spot_price, 2) if spot_price > 0 else 0.0,
        legs_count=legs_count,
        order_size=order_size,
        cob_pricing=cob_pricing,
        synthetic_legging=synthetic_legging,
        recommended_policy=recommended_policy,
        policy_rationale=policy_rationale,
        policies_comparison=policies_comparison,
        legs_breakdown=parsed_legs,
    )


def simulate_legging_execution(
    legs: List[Dict[str, Any]],
    spot_price: float,
    volatility: float = 0.25,
    latency_seconds: Optional[float] = None,
    num_simulations: int = 1000,
    random_seed: Optional[int] = None,
    risk_free_rate: float = 0.045,
) -> LeggingSimulationResult:
    """Runs a Monte Carlo simulation of legging execution hazard and adverse selection.

    Simulates the price path of the underlying spot over inter-leg execution latency,
    evaluating active leg slippage, hung-leg probability, and net dollar edge distribution.

    Parameters
    ----------
    legs : List[Dict[str, Any]]
        List of option legs.
    spot_price : float
        Current underlying spot price.
    volatility : float
        Annualized implied/historical volatility (default 0.25).
    latency_seconds : Optional[float]
        Inter-leg execution delay in seconds. Defaults to
        `settings.OPTIONS_SOR_LEGGING_LATENCY_SECONDS` (2.0) when not provided.
    num_simulations : int
        Number of Monte Carlo paths (default 1000).
    random_seed : Optional[int]
        Random seed for deterministic test reproducibility.
    risk_free_rate : float
        Risk-free rate (default 0.045).

    Returns
    -------
    LeggingSimulationResult
        Structured dataclass containing probability of hung leg, expected slippage,
        net edge captured, empirical distribution, and policy recommendation.
    """
    num_simulations = max(10, int(num_simulations or 1000))
    spot_price = float(spot_price) if spot_price and spot_price > 0 else 100.0
    volatility = float(volatility) if volatility and volatility > 0 else 0.25
    default_latency_seconds = float(getattr(settings, "OPTIONS_SOR_LEGGING_LATENCY_SECONDS", 2.0))
    latency_seconds = max(
        0.0,
        float(latency_seconds if latency_seconds is not None else default_latency_seconds),
    )

    if not legs or len(legs) < 2:
        return LeggingSimulationResult(
            valid=False,
            num_simulations=num_simulations,
            latency_seconds=latency_seconds,
            spot_price=spot_price,
            volatility=volatility,
            probability_of_hung_leg=0.0,
            hung_leg_percentage=0.0,
            avg_slippage_cost=0.0,
            expected_net_edge_captured=0.0,
            initial_spread_edge=0.0,
            initial_net_mid=0.0,
            initial_net_natural=0.0,
            fill_price_mean=0.0,
            fill_price_std=0.0,
            fill_price_median=0.0,
            fill_price_p5=0.0,
            fill_price_p25=0.0,
            fill_price_p75=0.0,
            fill_price_p95=0.0,
            fill_price_min=0.0,
            fill_price_max=0.0,
            distribution={"percentiles": {}, "sample_prices": []},
            recommended_policy=POLICY_COB_NET_PACKAGE,
            reason="At least 2 legs and a positive spot price are required for legging simulation.",
            passive_leg="",
            active_leg="",
            gross_spread_savings=0.0,
            expected_slippage=0.0,
            expected_net_savings=0.0,
            hung_leg_probability=0.0,
            savings_p5=0.0,
            savings_p50=0.0,
            savings_p95=0.0,
            var_95=0.0,
            is_legging_favorable=False,
        )

    # Base routing analysis
    sor_analysis = analyze_routing_options(
        legs=legs,
        spot_price=spot_price,
        volatility=volatility,
    )

    if not sor_analysis.valid:
        return LeggingSimulationResult(
            valid=False,
            num_simulations=num_simulations,
            latency_seconds=latency_seconds,
            spot_price=spot_price,
            volatility=volatility,
            probability_of_hung_leg=0.0,
            hung_leg_percentage=0.0,
            avg_slippage_cost=0.0,
            expected_net_edge_captured=0.0,
            initial_spread_edge=0.0,
            initial_net_mid=0.0,
            initial_net_natural=0.0,
            fill_price_mean=0.0,
            fill_price_std=0.0,
            fill_price_median=0.0,
            fill_price_p5=0.0,
            fill_price_p25=0.0,
            fill_price_p75=0.0,
            fill_price_p95=0.0,
            fill_price_min=0.0,
            fill_price_max=0.0,
            distribution={"percentiles": {}, "sample_prices": []},
            recommended_policy=POLICY_COB_NET_PACKAGE,
            reason=sor_analysis.policy_rationale or "Invalid legs payload",
            passive_leg="",
            active_leg="",
            gross_spread_savings=0.0,
            expected_slippage=0.0,
            expected_net_savings=0.0,
            hung_leg_probability=0.0,
            savings_p5=0.0,
            savings_p50=0.0,
            savings_p95=0.0,
            var_95=0.0,
            is_legging_favorable=False,
        )

    parsed_legs = sor_analysis.legs_breakdown
    synth_meta = sor_analysis.synthetic_legging or {}
    cob_meta = sor_analysis.cob_pricing or {}

    passive_idx = synth_meta.get("passive_leg_index", 0)
    active_idx = synth_meta.get("active_leg_index", 1)

    passive_leg = parsed_legs[passive_idx]
    active_leg = parsed_legs[active_idx]
    base_savings = synth_meta.get("expected_spread_savings", 0.0)

    initial_net_natural = float(cob_meta.get("net_natural", 0.0))
    initial_net_mid = float(cob_meta.get("net_mid", 0.0))
    initial_spread_edge = abs(initial_net_natural - initial_net_mid)

    # Inter-leg latency Delta t in annualized trading years
    dt_years = (latency_seconds / TRADING_SECONDS_PER_YEAR) if latency_seconds > 0 else 0.0
    sigma = max(0.001, volatility)

    rng = np.random.default_rng(random_seed)

    if dt_years > 0:
        # GBM step: S(dt) = S(0) * exp((r - 0.5 * sigma^2) * dt + sigma * sqrt(dt) * Z)
        z = rng.standard_normal(num_simulations)
        drift = (risk_free_rate - 0.5 * (sigma ** 2)) * dt_years
        diffusion = sigma * math.sqrt(dt_years) * z
        simulated_spots = spot_price * np.exp(drift + diffusion)
        spot_jumps = simulated_spots - spot_price
    else:
        simulated_spots = np.full(num_simulations, spot_price, dtype=float)
        spot_jumps = np.zeros(num_simulations, dtype=float)

    # Active leg price change: dP = Delta * dS + 0.5 * Gamma * (dS)^2
    active_delta = active_leg["delta"]
    active_gamma = active_leg["gamma"]
    active_sign = 1.0 if active_leg["action"] == "buy" else -1.0

    # For a buyer of the active leg, price rise (dP > 0) is adverse (higher cost).
    # For a seller of the active leg, price drop (dP < 0) is adverse (lower proceeds).
    active_leg_dprice = (active_delta * spot_jumps) + (0.5 * active_gamma * (spot_jumps ** 2))
    adverse_slippage = active_sign * active_leg_dprice

    # Hung leg condition: adverse slippage exceeds tolerance threshold or moves against trader.
    # This flags the paths where chasing the active leg at its repriced level is no longer a
    # realistic/economic fill -- in reality the algo abandons the sweep and the trader is left
    # holding ONLY the passive leg: an unintended, NAKED single-leg position, not "both legs
    # filled, just at a worse price."
    spread_tol = max(0.01, 0.5 * active_leg["spread"])
    hung_mask = (adverse_slippage > spread_tol) if dt_years > 0 else np.zeros(num_simulations, dtype=bool)
    hung_leg_probability = float(np.mean(hung_mask))
    hung_leg_percentage = hung_leg_probability * 100.0

    # Naked-exposure cost: without this, every simulated path (hung or not) priced the active
    # leg as though it eventually filled at natural_price + dprice, silently treating a hung
    # leg as if both legs always fill together and understating legging risk. A genuinely hung
    # leg instead leaves the passive leg naked, and the realistic remedy is to cross its own
    # bid-ask spread again to flatten the unintended position (or accept open-ended directional
    # risk, which is at least as costly) -- so charge that real, additional round-trip unwind
    # cost on hung paths only, leaving the ordinary (filled) slippage distribution untouched.
    naked_unwind_cost = float(passive_leg["spread"]) * float(passive_leg["ratio"])
    adverse_slippage = np.where(hung_mask, adverse_slippage + naked_unwind_cost, adverse_slippage)

    # Total slippage cost across all runs (now honestly includes the naked-exposure unwind
    # cost incurred on hung paths, rather than silently ignoring that risk).
    avg_slippage_cost = float(np.mean(np.maximum(0.0, adverse_slippage)))

    # Net dollar savings distribution = Gross Spread Savings - Adverse Slippage (incl. the
    # naked-exposure unwind cost charged above on hung paths).
    simulated_savings = base_savings - adverse_slippage
    expected_net_edge_captured = float(np.mean(simulated_savings))

    # Net spread fill prices distribution
    # Passive leg fills at passive price, active leg fills at natural + dprice
    passive_sign = 1.0 if passive_leg["action"] == "buy" else -1.0
    passive_fill = passive_sign * passive_leg["ratio"] * passive_leg["passive_price"]

    simulated_net_fills = np.full(num_simulations, passive_fill, dtype=float)
    for idx, pl in enumerate(parsed_legs):
        if idx == passive_idx:
            continue
        sign = 1.0 if pl["action"] == "buy" else -1.0
        q = pl["ratio"]
        dprice = (pl["delta"] * spot_jumps) + (0.5 * pl["gamma"] * (spot_jumps ** 2))
        simulated_net_fills += sign * q * (pl["natural_price"] + dprice)

    fill_mean = float(np.mean(simulated_net_fills))
    fill_std = float(np.std(simulated_net_fills))
    fill_median = float(np.median(simulated_net_fills))
    fill_p5 = float(np.percentile(simulated_net_fills, 5))
    fill_p25 = float(np.percentile(simulated_net_fills, 25))
    fill_p75 = float(np.percentile(simulated_net_fills, 75))
    fill_p95 = float(np.percentile(simulated_net_fills, 95))
    fill_min = float(np.min(simulated_net_fills))
    fill_max = float(np.max(simulated_net_fills))

    savings_p5 = float(np.percentile(simulated_savings, 5))
    savings_p50 = float(np.percentile(simulated_savings, 50))
    savings_p95 = float(np.percentile(simulated_savings, 95))
    var_95 = float(max(0.0, -savings_p5))

    is_favorable = bool(expected_net_edge_captured > 0.02 and hung_leg_probability < 0.30)

    distribution_data = {
        "percentiles": {
            "p5": round(fill_p5, 4),
            "p25": round(fill_p25, 4),
            "p50": round(fill_median, 4),
            "p75": round(fill_p75, 4),
            "p95": round(fill_p95, 4),
            "min": round(fill_min, 4),
            "max": round(fill_max, 4),
            "mean": round(fill_mean, 4),
            "std": round(fill_std, 4),
        },
        "sample_prices": [round(x, 4) for x in simulated_net_fills[:50].tolist()],
        "savings_distribution": {
            "p5": round(savings_p5, 4),
            "p50": round(savings_p50, 4),
            "p95": round(savings_p95, 4),
            "mean": round(expected_net_edge_captured, 4),
            "var_95": round(var_95, 4),
        },
    }

    if expected_net_edge_captured <= 0.0 or hung_leg_probability > 0.40:
        recommended_policy = POLICY_COB_NET_PACKAGE
        reason = (
            f"High hung leg hazard ({hung_leg_percentage:.1f}%) and adverse selection slippage (${avg_slippage_cost:.2f}) "
            f"erode edge. Execute via atomic COB net package."
        )
    elif expected_net_edge_captured > 0.05 and hung_leg_probability <= 0.35 and initial_spread_edge >= 0.10:
        recommended_policy = POLICY_LEG_PASSIVE_FIRST
        reason = (
            f"Favorable risk-reward: Expected net edge +${expected_net_edge_captured:.2f}/sh with "
            f"controlled hung leg hazard ({hung_leg_percentage:.1f}%). Leg passive first."
        )
    else:
        recommended_policy = POLICY_SPLIT_DIRECT
        reason = (
            f"Moderate edge (+${expected_net_edge_captured:.2f}/sh) vs hazard ({hung_leg_percentage:.1f}%). "
            f"Recommend dynamic split limit routing."
        )

    return LeggingSimulationResult(
        valid=True,
        num_simulations=num_simulations,
        latency_seconds=round(latency_seconds, 2),
        spot_price=round(spot_price, 2),
        volatility=round(volatility, 4),
        probability_of_hung_leg=round(hung_leg_probability, 4),
        hung_leg_percentage=round(hung_leg_percentage, 2),
        avg_slippage_cost=round(avg_slippage_cost, 4),
        expected_net_edge_captured=round(expected_net_edge_captured, 4),
        initial_spread_edge=round(initial_spread_edge, 4),
        initial_net_mid=round(initial_net_mid, 4),
        initial_net_natural=round(initial_net_natural, 4),
        fill_price_mean=round(fill_mean, 4),
        fill_price_std=round(fill_std, 4),
        fill_price_median=round(fill_median, 4),
        fill_price_p5=round(fill_p5, 4),
        fill_price_p25=round(fill_p25, 4),
        fill_price_p75=round(fill_p75, 4),
        fill_price_p95=round(fill_p95, 4),
        fill_price_min=round(fill_min, 4),
        fill_price_max=round(fill_max, 4),
        distribution=distribution_data,
        recommended_policy=recommended_policy,
        reason=reason,
        passive_leg=passive_leg["symbol"],
        active_leg=active_leg["symbol"],
        gross_spread_savings=round(base_savings, 4),
        expected_slippage=round(avg_slippage_cost, 4),
        expected_net_savings=round(expected_net_edge_captured, 4),
        hung_leg_probability=round(hung_leg_probability, 4),
        savings_p5=round(savings_p5, 4),
        savings_p50=round(savings_p50, 4),
        savings_p95=round(savings_p95, 4),
        var_95=round(var_95, 4),
        is_legging_favorable=is_favorable,
    )
