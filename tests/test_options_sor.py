"""tests/test_options_sor.py — Tests for Multi-Leg Options Smart Order Router (SOR) & Legging Simulator.
=======================================================================================================

Verifies:
1. AST import safety: pilots/options_sor.py never imports forbidden engines.
2. Symbol parsing and Black-Scholes Greeks helper.
3. Complex Order Book (COB) Net Mid, Natural, Passive pricing and fill probabilities.
4. Synthetic Legging Passive-First vs Active-First spread capture.
5. Optimal Routing Policy Selection (COB_NET_PACKAGE, LEG_PASSIVE_FIRST, SPLIT_DIRECT).
6. Monte Carlo Legging Hazard and Adverse Selection simulation.
7. Structured LeggingSimulationResult properties, distribution stats, and reproducibility.
8. Robustness against degenerate, missing, or corrupt leg quotes (CONSTRAINT #6).
"""

import ast
import math
from pathlib import Path
import pytest

from pilots.options_sor import (
    POLICY_COB_NET_PACKAGE,
    POLICY_LEG_PASSIVE_FIRST,
    POLICY_SPLIT_DIRECT,
    LeggingSimulationResult,
    RoutingAnalysisResult,
    analyze_routing_options,
    calculate_leg_greeks,
    parse_leg_symbol,
    simulate_legging_execution,
)


# ---------------------------------------------------------------------------
# 1. AST Import Safety Guard
# ---------------------------------------------------------------------------


def test_options_sor_ast_import_safety():
    """Verifies that pilots/options_sor.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "options_sor.py"
    assert file_path.exists(), f"File {file_path} not found"

    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename="options_sor.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
        "data_engine",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert forbidden not in node.module, f"Forbidden import from found: {node.module}"


# ---------------------------------------------------------------------------
# 2. Leg Symbol Parsing & Greek Calculations
# ---------------------------------------------------------------------------


def test_parse_leg_symbol():
    res_call = parse_leg_symbol("AAPL 2026-09-18 $150.00 CALL")
    assert res_call is not None
    assert res_call["ticker"] == "AAPL"
    assert res_call["expiration"] == "2026-09-18"
    assert res_call["strike"] == 150.0
    assert res_call["option_type"] == "call"

    res_put = parse_leg_symbol("SPY 2026-10-16 $500.50 PUT")
    assert res_put is not None
    assert res_put["ticker"] == "SPY"
    assert res_put["strike"] == 500.50
    assert res_put["option_type"] == "put"

    res_invalid = parse_leg_symbol("INVALID_TICKER")
    assert res_invalid is None


def test_calculate_leg_greeks():
    spot = 150.0
    strike = 150.0
    t_years = 30.0 / 365.0
    sigma = 0.25

    g_call = calculate_leg_greeks(spot, strike, t_years, sigma, option_type="call")
    assert 0.45 < g_call["delta"] < 0.65
    assert g_call["gamma"] > 0
    assert g_call["price"] > 0

    g_put = calculate_leg_greeks(spot, strike, t_years, sigma, option_type="put")
    assert -0.55 < g_put["delta"] < -0.35
    assert g_put["gamma"] > 0

    # 0DTE / Degenerate guard
    g_0dte = calculate_leg_greeks(spot, strike, t_years=0.0, sigma=sigma, option_type="call")
    assert g_0dte["gamma"] == 0.0


# ---------------------------------------------------------------------------
# 3. Complex Order Book (COB) Pricing & Tight Spread Policy (COB_NET_PACKAGE)
# ---------------------------------------------------------------------------


def test_cob_net_package_tight_spreads():
    """Tight symmetric spreads on SPY vertical call spread should select COB_NET_PACKAGE."""
    legs = [
        {
            "symbol": "SPY 2026-09-18 $500.00 CALL",
            "action": "buy",
            "strike": 500.0,
            "type": "call",
            "expiration": "2026-09-18",
            "bid": 10.00,
            "ask": 10.02,
        },
        {
            "symbol": "SPY 2026-09-18 $505.00 CALL",
            "action": "sell",
            "strike": 505.0,
            "type": "call",
            "expiration": "2026-09-18",
            "bid": 6.98,
            "ask": 7.00,
        },
    ]

    spot = 502.0
    res = analyze_routing_options(legs, spot_price=spot)

    assert isinstance(res, RoutingAnalysisResult)
    assert res.valid is True
    assert res.strategy_type == "Vertical Spread"
    assert res.legs_count == 2

    cob = res.cob_pricing
    assert cob["net_mid"] == pytest.approx(10.01 - 6.99, abs=0.001)  # 3.02
    assert cob["net_natural"] == pytest.approx(10.02 - 6.98, abs=0.001)  # 3.04
    assert cob["net_passive"] == pytest.approx(10.00 - 7.00, abs=0.001)  # 3.00
    assert cob["spread_width"] == pytest.approx(0.04, abs=0.001)
    assert cob["is_net_debit"] is True
    assert cob["fill_probability_mid"] > 0.70

    # With tight $0.02 spreads on both legs, COB_NET_PACKAGE is optimal
    assert res.recommended_policy == POLICY_COB_NET_PACKAGE
    assert "COB_NET_PACKAGE" in res.policy_rationale

    # Policies comparison has 3 options
    assert len(res.policies_comparison) == 3
    assert res.policies_comparison[0]["policy"] == POLICY_COB_NET_PACKAGE
    assert res.policies_comparison[0]["recommended"] is True


# ---------------------------------------------------------------------------
# 4. Asymmetric Spread & Synthetic Legging (LEG_PASSIVE_FIRST)
# ---------------------------------------------------------------------------


def test_synthetic_legging_passive_first():
    """Asymmetric spreads (wide passive leg, liquid active leg) should recommend LEG_PASSIVE_FIRST."""
    legs = [
        {
            "symbol": "XYZ 2026-09-18 $100.00 PUT",
            "action": "sell",
            "strike": 100.0,
            "type": "put",
            "expiration": "2026-09-18",
            "bid": 4.50,
            "ask": 5.00,  # Wide spread ($0.50) -> Passive leg
        },
        {
            "symbol": "XYZ 2026-09-18 $95.00 PUT",
            "action": "buy",
            "strike": 95.0,
            "type": "put",
            "expiration": "2026-09-18",
            "bid": 2.00,
            "ask": 2.02,  # Tight spread ($0.02) -> Active leg
        },
    ]

    spot = 101.0
    res = analyze_routing_options(legs, spot_price=spot, volatility=0.20)

    assert isinstance(res, RoutingAnalysisResult)
    assert res.valid is True
    synth = res.synthetic_legging

    # Passive leg is index 0 ($100P with $0.50 spread)
    assert synth["passive_leg_index"] == 0
    assert synth["active_leg_index"] == 1

    # In synthetic legging:
    # Passive leg sold at Ask (5.00), Active leg bought at Ask (2.02) -> Net Credit 2.98
    # Natural COB price: Sell at Bid (4.50), Buy at Ask (2.02) -> Net Credit 2.48
    # Spread savings = $0.50 ($50 / contract)
    assert synth["expected_spread_savings"] == pytest.approx(0.50, abs=0.01)
    assert synth["net_edge"] > 0

    assert res.recommended_policy == POLICY_LEG_PASSIVE_FIRST
    assert "LEG_PASSIVE_FIRST" in res.policy_rationale


def _build_hung_leg_test_legs(active_option_type: str, active_delta: float) -> list:
    """Builds a 2-leg spread with a fixed wide-spread passive leg and a tight-spread
    active leg whose option_type/delta are parameterized -- used to prove hung-leg
    hazard is symmetric between an economically-equivalent PUT active leg (negative
    delta) and CALL active leg (positive delta) of the same magnitude."""
    return [
        {
            "symbol": "SYM 2026-09-18 $100.00 PUT",
            "action": "sell",
            "strike": 100.0,
            "type": "put",
            "expiration": "2026-09-18",
            "bid": 1.00,
            "ask": 3.00,  # Wide spread ($2.00) -> passive leg
            "delta": -0.30,
        },
        {
            "symbol": f"SYM 2026-09-18 $110.00 {active_option_type.upper()}",
            "action": "buy",
            "strike": 110.0,
            "type": active_option_type,
            "expiration": "2026-09-18",
            "bid": 2.00,
            "ask": 2.05,  # Tight spread ($0.05) -> active leg
            "delta": active_delta,
        },
    ]


def test_hung_leg_probability_symmetric_for_put_and_call_active_leg():
    """Regression test for the unsigned-delta bug: hung_leg_probability must be driven
    by the MAGNITUDE of the active leg's delta, not its sign. Before the fix, a PUT
    active leg (negative delta) collapsed sigma_opt to its 0.001 floor (since
    delta*spot*vol*sqrt(tau) went negative), silently flooring hung_leg_probability at
    its 0.02 clip regardless of real hazard, while an economically identical CALL
    active leg (same |delta|) reported the correct, much higher value."""
    spot = 101.0
    volatility = 0.80  # high vol to keep the correct (unclipped) value away from 0.02

    legs_call_active = _build_hung_leg_test_legs("call", 0.80)
    legs_put_active = _build_hung_leg_test_legs("put", -0.80)

    res_call = analyze_routing_options(legs_call_active, spot_price=spot, volatility=volatility)
    res_put = analyze_routing_options(legs_put_active, spot_price=spot, volatility=volatility)

    assert res_call.valid is True
    assert res_put.valid is True

    synth_call = res_call.synthetic_legging
    synth_put = res_put.synthetic_legging

    # Sanity: the tight-spread leg (index 1) is selected as the active leg in both cases.
    assert synth_call["active_leg_index"] == 1
    assert synth_put["active_leg_index"] == 1

    hung_call = synth_call["hung_leg_probability"]
    hung_put = synth_put["hung_leg_probability"]

    # The core fix: |delta|=0.80 for both -> hung_leg_probability must match regardless
    # of PUT (-0.80) vs CALL (+0.80) sign.
    assert hung_put == pytest.approx(hung_call, abs=1e-4)

    # Neither should be pinned at the degenerate 0.02 floor -- proves this is a genuine
    # computed hazard, not the sign bug silently collapsing sigma_opt to its floor.
    assert hung_call > 0.10
    assert hung_put > 0.10


# ---------------------------------------------------------------------------
# 5. Multi-Leg 4-Leg Strategy (Iron Condor -> COB_NET_PACKAGE)
# ---------------------------------------------------------------------------


def test_iron_condor_routes_to_cob_package():
    """4-leg Iron Condor should always route via COB_NET_PACKAGE to prevent multi-leg legging risk."""
    legs = [
        {"symbol": "SPY 2026-09-18 $480.00 PUT", "action": "buy", "bid": 1.00, "ask": 1.05},
        {"symbol": "SPY 2026-09-18 $490.00 PUT", "action": "sell", "bid": 2.20, "ask": 2.25},
        {"symbol": "SPY 2026-09-18 $515.00 CALL", "action": "sell", "bid": 2.10, "ask": 2.15},
        {"symbol": "SPY 2026-09-18 $525.00 CALL", "action": "buy", "bid": 0.90, "ask": 0.95},
    ]

    res = analyze_routing_options(legs, spot_price=500.0)
    assert res.valid is True
    assert res.strategy_type == "Iron Condor"
    assert res.legs_count == 4
    assert res.recommended_policy == POLICY_COB_NET_PACKAGE
    assert "Iron Condor" in res.policy_rationale


# ---------------------------------------------------------------------------
# 6. Single Leg & Large Order Sizing
# ---------------------------------------------------------------------------


def test_single_leg_and_large_order_split_direct():
    # Single leg
    single_leg = [{"symbol": "AAPL 2026-09-18 $150.00 CALL", "action": "buy", "bid": 5.00, "ask": 5.10}]
    res_single = analyze_routing_options(single_leg, spot_price=150.0)
    assert res_single.valid is True
    assert res_single.recommended_policy == POLICY_SPLIT_DIRECT

    # Large order size across wide spreads
    wide_legs = [
        {"symbol": "ILLIQ 2026-09-18 $100.00 CALL", "action": "buy", "bid": 5.00, "ask": 5.30},
        {"symbol": "ILLIQ 2026-09-18 $105.00 CALL", "action": "sell", "bid": 2.00, "ask": 2.30},
    ]
    res_large = analyze_routing_options(wide_legs, spot_price=100.0, order_size=20, volatility=0.65)
    assert res_large.valid is True
    assert res_large.recommended_policy in (POLICY_SPLIT_DIRECT, POLICY_COB_NET_PACKAGE)


# ---------------------------------------------------------------------------
# 7. Monte Carlo Legging Simulation & Structured Result
# ---------------------------------------------------------------------------


def test_simulate_legging_execution_call_spread():
    """Verifies statistical Monte Carlo simulation of legging execution hazard."""
    legs = [
        {
            "symbol": "NVDA 2026-09-18 $120.00 CALL",
            "action": "buy",
            "bid": 8.00,
            "ask": 8.40,  # Passive leg
        },
        {
            "symbol": "NVDA 2026-09-18 $125.00 CALL",
            "action": "sell",
            "bid": 5.10,
            "ask": 5.12,  # Active leg
        },
    ]

    spot = 122.0
    sim = simulate_legging_execution(
        legs=legs,
        spot_price=spot,
        volatility=0.35,
        latency_seconds=2.0,
        num_simulations=500,
        random_seed=42,
    )

    assert isinstance(sim, LeggingSimulationResult)
    assert sim.valid is True
    assert sim.num_simulations == 500
    assert sim.latency_seconds == 2.0
    assert sim.gross_spread_savings == pytest.approx(0.40, abs=0.01)
    assert 0.0 <= sim.probability_of_hung_leg <= 1.0
    assert 0.0 <= sim.hung_leg_percentage <= 100.0
    assert sim.hung_leg_percentage == pytest.approx(sim.probability_of_hung_leg * 100.0, abs=0.01)
    assert sim.avg_slippage_cost >= 0.0

    # Structured distribution checks
    dist = sim.distribution
    assert "percentiles" in dist
    assert "sample_prices" in dist
    assert len(dist["sample_prices"]) > 0

    pct = dist["percentiles"]
    assert pct["min"] <= pct["p5"] <= pct["p50"] <= pct["p95"] <= pct["max"]
    assert sim.fill_price_min <= sim.fill_price_median <= sim.fill_price_max

    # Dict access parity
    assert sim["valid"] is True
    assert sim["num_simulations"] == 500
    assert "expected_net_savings" in sim
    assert sim.to_dict()["probability_of_hung_leg"] == sim.probability_of_hung_leg


def test_simulate_legging_execution_zero_latency():
    """At 0 latency, price drift is 0, hung leg risk is 0, and variance is 0."""
    legs = [
        {"symbol": "SPY 2026-09-18 $500.00 CALL", "action": "buy", "bid": 10.00, "ask": 10.40},
        {"symbol": "SPY 2026-09-18 $505.00 CALL", "action": "sell", "bid": 7.00, "ask": 7.05},
    ]

    sim = simulate_legging_execution(
        legs=legs,
        spot_price=502.0,
        volatility=0.25,
        latency_seconds=0.0,
        num_simulations=500,
        random_seed=123,
    )

    assert sim.probability_of_hung_leg == 0.0
    assert sim.hung_leg_percentage == 0.0
    assert sim.avg_slippage_cost == 0.0
    assert math.isclose(sim.fill_price_std, 0.0, abs_tol=1e-5)


def test_simulate_legging_execution_reproducibility():
    """Deterministic random_seed guarantees identical simulation results."""
    legs = [
        {"action": "sell", "type": "put", "strike": 100.0, "bid": 3.00, "ask": 3.40, "mid": 3.20},
        {"action": "buy", "type": "put", "strike": 90.0, "bid": 1.00, "ask": 1.30, "mid": 1.15},
    ]

    res1 = simulate_legging_execution(legs, 100.0, 0.30, latency_seconds=3.0, num_simulations=500, random_seed=999)
    res2 = simulate_legging_execution(legs, 100.0, 0.30, latency_seconds=3.0, num_simulations=500, random_seed=999)

    assert res1.probability_of_hung_leg == res2.probability_of_hung_leg
    assert res1.avg_slippage_cost == res2.avg_slippage_cost
    assert res1.fill_price_mean == res2.fill_price_mean
    assert res1.expected_net_edge_captured == res2.expected_net_edge_captured


# ---------------------------------------------------------------------------
# 8. Degenerate and Missing Input Handling (CONSTRAINT #6)
# ---------------------------------------------------------------------------


def test_degenerate_and_empty_inputs():
    # Empty legs
    res_empty = analyze_routing_options([], spot_price=100.0)
    assert res_empty.valid is False
    assert res_empty.cob_pricing is None

    # Invalid spot or empty legs in simulation
    sim_invalid = simulate_legging_execution([], spot_price=-10.0)
    assert sim_invalid.valid is False
    assert sim_invalid.probability_of_hung_leg == 0.0

    # Single leg in simulation
    sim_single = simulate_legging_execution([{"action": "buy", "strike": 100.0, "type": "call"}], spot_price=100.0)
    assert sim_single.valid is False
    assert "At least 2 legs" in sim_single.reason

    # Contract dict format
    legs_contract_format = [
        {
            "type": "call",
            "action": "Buy",
            "contract": {
                "contractSymbol": "AAPL260918C00150000",
                "strike": 150.0,
                "bid": 4.10,
                "ask": 4.15,
                "volume": 1200,
                "openInterest": 5400,
                "impliedVolatility": 0.28,
            },
        },
        {
            "type": "call",
            "action": "Sell",
            "contract": {
                "contractSymbol": "AAPL260918C00155000",
                "strike": 155.0,
                "bid": 2.00,
                "ask": 2.05,
                "volume": 800,
                "openInterest": 3100,
                "impliedVolatility": 0.27,
            },
        },
    ]

    res_contract = analyze_routing_options(legs_contract_format, spot_price=152.0)
    assert res_contract.valid is True
    assert res_contract.legs_count == 2
    assert res_contract.cob_pricing["spread_width"] == pytest.approx(0.10, abs=0.01)


def test_simulate_legging_execution_put_credit_spread():
    """Simulates a Bull Put Credit Spread (Sell 100P, Buy 95P)."""
    legs = [
        {
            "symbol": "AAPL 2026-09-18 $100.00 PUT",
            "action": "sell",
            "strike": 100.0,
            "dte": 45.0,
            "bid": 2.50,
            "ask": 2.80,
            "mid": 2.65,
        },
        {
            "symbol": "AAPL 2026-09-18 $95.00 PUT",
            "action": "buy",
            "strike": 95.0,
            "dte": 45.0,
            "bid": 1.20,
            "ask": 1.40,
            "mid": 1.30,
        },
    ]

    res = simulate_legging_execution(
        legs=legs,
        spot_price=102.0,
        volatility=0.20,
        latency_seconds=1.5,
        num_simulations=1000,
        random_seed=777,
    )

    assert isinstance(res, LeggingSimulationResult)
    assert res.valid is True
    assert res.initial_spread_edge > 0
    assert 0.0 <= res.probability_of_hung_leg <= 1.0
    assert res.avg_slippage_cost >= 0.0
    assert res.expected_net_edge_captured is not None


def test_simulate_legging_execution_iron_condor_4_legs():
    """Simulates a 4-leg Iron Condor structure."""
    legs = [
        {"symbol": "SPY 2026-09-18 $480.00 PUT", "action": "buy", "bid": 1.00, "ask": 1.05},
        {"symbol": "SPY 2026-09-18 $490.00 PUT", "action": "sell", "bid": 2.20, "ask": 2.25},
        {"symbol": "SPY 2026-09-18 $515.00 CALL", "action": "sell", "bid": 2.10, "ask": 2.15},
        {"symbol": "SPY 2026-09-18 $525.00 CALL", "action": "buy", "bid": 0.90, "ask": 0.95},
    ]

    res = simulate_legging_execution(
        legs=legs,
        spot_price=500.0,
        volatility=0.22,
        latency_seconds=2.5,
        num_simulations=500,
        random_seed=101,
    )

    assert isinstance(res, LeggingSimulationResult)
    assert res.valid is True
    assert res.num_simulations == 500
    assert res.avg_slippage_cost >= 0.0
    assert res.recommended_policy in [POLICY_COB_NET_PACKAGE, POLICY_LEG_PASSIVE_FIRST, POLICY_SPLIT_DIRECT]


def test_simulate_legging_execution_high_volatility_hazard():
    """High volatility increases hung leg hazard and slippage cost."""
    legs = [
        {"action": "buy", "type": "call", "strike": 100.0, "bid": 5.00, "ask": 5.40},
        {"action": "sell", "type": "call", "strike": 110.0, "bid": 2.00, "ask": 2.05},
    ]

    res_low_vol = simulate_legging_execution(legs, spot_price=100.0, volatility=0.10, latency_seconds=3.0, num_simulations=1000, random_seed=42)
    res_high_vol = simulate_legging_execution(legs, spot_price=100.0, volatility=0.80, latency_seconds=3.0, num_simulations=1000, random_seed=42)

    assert res_high_vol.avg_slippage_cost > res_low_vol.avg_slippage_cost
    assert res_high_vol.fill_price_std > res_low_vol.fill_price_std


def test_legging_simulation_result_dict_interface():
    """Verifies dict subscripting, get(), __contains__, and to_dict() methods."""
    legs = [
        {"action": "buy", "type": "call", "strike": 100.0, "bid": 5.00, "ask": 5.40},
        {"action": "sell", "type": "call", "strike": 110.0, "bid": 2.00, "ask": 2.05},
    ]

    sim = simulate_legging_execution(legs, spot_price=100.0, volatility=0.25, random_seed=42)

    assert "probability_of_hung_leg" in sim
    assert "expected_net_edge_captured" in sim
    assert "non_existent_key" not in sim
    assert sim.get("probability_of_hung_leg") == sim.probability_of_hung_leg
    assert sim.get("missing_key", "default_val") == "default_val"
    assert sim["probability_of_hung_leg"] == sim.probability_of_hung_leg
    with pytest.raises(KeyError):
        _ = sim["invalid_key"]

    d = sim.to_dict()
    assert isinstance(d, dict)
    assert d["valid"] is True
    assert "distribution" in d
