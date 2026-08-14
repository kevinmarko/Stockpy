"""
Tests for pilots/lob_simulator.py — Limit Order Book Microstructure & Markovian Queue Simulator.
"""

import ast
import json
import math
from pathlib import Path
import pytest
import numpy as np

from pilots.lob_simulator import (
    OrderFlowEvent,
    LOBArrivalRates,
    QueueSimulationResult,
    LOBLevel,
    LOBSnapshot,
    LOBDynamicsResult,
    UrgencyLevel,
    QueuePlacementCandidate,
    OptimalPlacementResult,
    LiquiditySlice,
    LiquiditySliceResult,
    compute_lob_arrival_rates,
    simulate_queue_position,
    compute_cst_fill_probability,
    simulate_lob_dynamics,
    estimate_execution_slippage_and_timing,
    calculate_cont_stoikov_fill_probability,
    calculate_expected_fill_latency,
    evaluate_optimal_queue_level,
    slice_liquidity_order,
)
from pilots.order_sizing import (
    calculate_stock_sizing,
    calculate_option_sizing,
    calculate_safe_cash_preset,
    validate_order_sizing,
)


# ===========================================================================
# 1. Arrival Rate Estimation Tests
# ===========================================================================

def test_compute_lob_arrival_rates_synthetic_exact():
    """Verifies that Poisson arrival rates match exact synthetic event frequencies."""
    # 50 limit orders, 20 cancellations, 30 market orders over 10.0 seconds
    records = []
    t = 0.0
    for i in range(50):
        records.append({"timestamp": t + i * 0.2, "event_type": "LIMIT", "side": "BID", "size": 1.0, "level": 1, "depth": 10.0})
    for i in range(20):
        records.append({"timestamp": t + i * 0.5, "event_type": "CANCEL", "side": "BID", "size": 1.0, "level": 1, "depth": 10.0})
    for i in range(30):
        records.append({"timestamp": t + i * 0.33, "event_type": "MARKET", "side": "BID", "size": 1.0, "level": 1, "depth": 10.0})

    rates = compute_lob_arrival_rates(records, observation_duration_sec=10.0)

    assert rates.valid is True
    assert rates.total_events == 100
    assert rates.observation_duration_sec == 10.0
    assert pytest.approx(rates.lambda_limit, 0.01) == 5.0  # 50 / 10s
    assert pytest.approx(rates.theta_market, 0.01) == 3.0  # 30 / 10s
    # mu is normalized by average depth (10.0): 20 / (10s * 10 depth) = 0.20
    assert pytest.approx(rates.mu_cancel, 0.01) == 0.20
    assert rates.average_queue_depth == 10.0


def test_compute_lob_arrival_rates_synonym_mapping():
    """Verifies that all event type synonyms ('ADD', 'DELETE', 'TRADE', etc.) map correctly."""
    records = [
        {"timestamp": 1.0, "type": "ADD", "side": "BUY", "size": 5.0},
        {"timestamp": 2.0, "type": "DELETE", "side": "BUY", "size": 2.0},
        {"timestamp": 3.0, "type": "TRADE", "side": "BUY", "size": 1.0},
        {"timestamp": 4.0, "type": "NEW", "side": "SELL", "size": 4.0},
        {"timestamp": 5.0, "type": "REMOVE", "side": "SELL", "size": 1.0},
        {"timestamp": 6.0, "type": "FILL", "side": "SELL", "size": 2.0},
    ]
    rates = compute_lob_arrival_rates(records, observation_duration_sec=6.0)

    assert rates.valid is True
    assert rates.total_events == 6
    assert rates.event_counts["LIMIT"] == 2
    assert rates.event_counts["CANCEL"] == 2
    assert rates.event_counts["MARKET"] == 2
    assert pytest.approx(rates.lambda_limit, 0.01) == 2.0 / 6.0
    assert pytest.approx(rates.theta_market, 0.01) == 2.0 / 6.0


def test_compute_lob_arrival_rates_order_flow_event_objects():
    """Verifies support for strongly-typed OrderFlowEvent instances."""
    events = [
        OrderFlowEvent(timestamp=0.0, event_type="LIMIT", side="BID", price=100.0, size=10.0),
        OrderFlowEvent(timestamp=1.0, event_type="CANCEL", side="BID", price=100.0, size=5.0),
        OrderFlowEvent(timestamp=2.0, event_type="MARKET", side="BID", price=100.0, size=5.0),
    ]
    rates = compute_lob_arrival_rates(events, observation_duration_sec=2.0)
    assert rates.valid is True
    assert rates.total_events == 3
    assert rates.event_counts["LIMIT"] == 1
    assert rates.event_counts["CANCEL"] == 1
    assert rates.event_counts["MARKET"] == 1


def test_compute_lob_arrival_rates_empty_and_degraded():
    """Verifies graceful non-raising handling of empty or degenerate inputs."""
    res_none = compute_lob_arrival_rates([])
    assert res_none.valid is False
    assert res_none.total_events == 0
    assert res_none.lambda_limit == 0.0
    assert res_none.mu_cancel == 0.0
    assert res_none.theta_market == 0.0

    res_invalid = compute_lob_arrival_rates([{"foo": "bar"}, None])
    assert res_invalid.valid is False
    assert res_invalid.total_events == 0


def test_compute_lob_arrival_rates_side_and_level_filtering():
    """Verifies filtering by side and price level."""
    records = [
        {"timestamp": 1.0, "event_type": "LIMIT", "side": "BID", "level": 1},
        {"timestamp": 2.0, "event_type": "LIMIT", "side": "BID", "level": 2},
        {"timestamp": 3.0, "event_type": "LIMIT", "side": "ASK", "level": 1},
        {"timestamp": 4.0, "event_type": "MARKET", "side": "BID", "level": 1},
    ]
    rates_bid_lvl1 = compute_lob_arrival_rates(records, side_filter="BID", level_filter=1, observation_duration_sec=4.0)
    assert rates_bid_lvl1.valid is True
    assert rates_bid_lvl1.total_events == 2
    assert rates_bid_lvl1.event_counts["LIMIT"] == 1
    assert rates_bid_lvl1.event_counts["MARKET"] == 1


# ===========================================================================
# 2. Closed-Form Analytical CST Formula Tests
# ===========================================================================

def test_compute_cst_fill_probability_pure_poisson():
    """Verifies closed-form fill probability matches Poisson arrival CDF when mu=0."""
    # When queue_ahead=0, order_size=1, theta=1.0, T=1.0:
    # Required 1 hit. P(Poisson(1.0) >= 1) = 1 - e^-1 ~= 0.63212
    p = compute_cst_fill_probability(queue_ahead=0, order_size=1, theta_market=1.0, mu_cancel=0.0, time_horizon_sec=1.0)
    assert pytest.approx(p, 0.001) == 1.0 - math.exp(-1.0)

    # When queue_ahead=1, order_size=1 => 2 hits required.
    # P(Poisson(2.0) >= 2) = 1 - (e^-2 * (1 + 2)) = 1 - 3*e^-2 ~= 0.59399
    p2 = compute_cst_fill_probability(queue_ahead=1, order_size=1, theta_market=1.0, mu_cancel=0.0, time_horizon_sec=2.0)
    assert pytest.approx(p2, 0.001) == 1.0 - 3.0 * math.exp(-2.0)


def test_compute_cst_fill_probability_monotonicity():
    """Verifies that fill probability monotonically increases with theta, T, and cancellations."""
    p_base = compute_cst_fill_probability(queue_ahead=10, order_size=5, theta_market=1.0, mu_cancel=0.0, time_horizon_sec=10.0)
    p_high_theta = compute_cst_fill_probability(queue_ahead=10, order_size=5, theta_market=2.0, mu_cancel=0.0, time_horizon_sec=10.0)
    p_long_T = compute_cst_fill_probability(queue_ahead=10, order_size=5, theta_market=1.0, mu_cancel=0.0, time_horizon_sec=20.0)
    p_with_cancel = compute_cst_fill_probability(queue_ahead=10, order_size=5, theta_market=1.0, mu_cancel=0.1, time_horizon_sec=10.0)

    assert p_high_theta > p_base
    assert p_long_T > p_base
    assert p_with_cancel > p_base


def test_compute_cst_fill_probability_edge_cases():
    """Verifies edge cases (order_size=0, T=0, zero theta and mu)."""
    assert compute_cst_fill_probability(queue_ahead=5, order_size=0, theta_market=1.0) == 1.0
    assert compute_cst_fill_probability(queue_ahead=5, order_size=1, theta_market=1.0, time_horizon_sec=0.0) == 0.0
    assert compute_cst_fill_probability(queue_ahead=5, order_size=1, theta_market=0.0, mu_cancel=0.0) == 0.0


# ===========================================================================
# 3. Markovian Queue Position Simulator Tests
# ===========================================================================

def test_simulate_queue_position_trivial_zero_order_size():
    """Trivial order_size=0 should yield instant 100% fill."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=0.0,
        queue_ahead=10.0,
        lambda_limit=1.0,
        mu_cancel=0.1,
        theta_market=1.0,
    )
    assert res.valid is True
    assert res.fill_probability == 1.0
    assert res.expected_fill_time_sec == 0.0
    assert res.expected_fill_ratio == 1.0


def test_simulate_queue_position_zero_market_flow_and_cancels():
    """Zero market flow and zero cancellations with queue ahead results in 0 fill probability."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=5.0,
        queue_ahead=10.0,
        lambda_limit=1.0,
        mu_cancel=0.0,
        theta_market=0.0,
        time_horizon_sec=30.0,
    )
    assert res.valid is True
    assert res.fill_probability == 0.0
    assert res.expected_fill_time_sec is None
    assert res.unconditional_fill_time_sec == 30.0
    assert res.expected_fill_ratio == 0.0


def test_simulate_queue_position_high_flow_fill_convergence():
    """High market flow should yield fill probability near 1.0 with predictable fill time."""
    # queue_ahead = 5, order_size = 5. Total 10 units.
    # theta = 2.0 units/sec, mu = 0.
    # Expected fill time ~ 10 / 2.0 = 5.0 seconds.
    res = simulate_queue_position(
        price_level=100.0,
        order_size=5.0,
        queue_ahead=5.0,
        lambda_limit=1.0,
        mu_cancel=0.0,
        theta_market=2.0,
        time_horizon_sec=30.0,
        num_simulations=1000,
        random_seed=42,
    )
    assert res.valid is True
    assert res.fill_probability > 0.98
    assert res.expected_fill_time_sec is not None
    assert pytest.approx(res.expected_fill_time_sec, 0.5) == 5.0
    assert res.percentiles_fill_time["p50"] is not None
    assert pytest.approx(res.percentiles_fill_time["p50"], 0.6) == 5.0


def test_simulate_queue_position_cancellation_acceleration():
    """Verifies that adding cancellations accelerates queue ahead depletion."""
    # Without cancellations
    res_no_cancel = simulate_queue_position(
        price_level=100.0,
        order_size=2.0,
        queue_ahead=10.0,
        lambda_limit=1.0,
        mu_cancel=0.0,
        theta_market=1.0,
        time_horizon_sec=10.0,
        num_simulations=800,
        random_seed=123,
    )

    # With high cancellation rate
    res_with_cancel = simulate_queue_position(
        price_level=100.0,
        order_size=2.0,
        queue_ahead=10.0,
        lambda_limit=1.0,
        mu_cancel=0.3,
        theta_market=1.0,
        time_horizon_sec=10.0,
        num_simulations=800,
        random_seed=123,
    )

    assert res_with_cancel.fill_probability > res_no_cancel.fill_probability
    assert res_with_cancel.expected_fill_time_sec is not None
    assert res_no_cancel.expected_fill_time_sec is not None
    assert res_with_cancel.expected_fill_time_sec < res_no_cancel.expected_fill_time_sec


def test_simulate_queue_position_adverse_selection_detection():
    """Thin opposite book with active opposite flow triggers adverse move before fill."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=5.0,
        queue_ahead=20.0,  # Deep queue ahead of us
        lambda_limit=0.5,
        mu_cancel=0.01,
        theta_market=0.5,  # Slow fill rate on our side
        opposite_queue=2.0,  # Very thin opposite side
        theta_opposite=3.0,  # Heavy opposite market orders
        time_horizon_sec=15.0,
        num_simulations=600,
        random_seed=999,
    )
    assert res.valid is True
    assert res.prob_adverse_move_before_fill > 0.50


def test_simulate_queue_position_percentiles_order():
    """Verifies that fill time percentiles are monotonically non-decreasing."""
    res = simulate_queue_position(
        price_level=50.0,
        order_size=5.0,
        queue_ahead=10.0,
        lambda_limit=1.0,
        mu_cancel=0.05,
        theta_market=1.5,
        time_horizon_sec=40.0,
        num_simulations=500,
        random_seed=777,
    )
    assert res.valid is True
    p10 = res.percentiles_fill_time["p10"]
    p25 = res.percentiles_fill_time["p25"]
    p50 = res.percentiles_fill_time["p50"]
    p75 = res.percentiles_fill_time["p75"]
    p90 = res.percentiles_fill_time["p90"]
    p95 = res.percentiles_fill_time["p95"]

    assert p10 <= p25 <= p50 <= p75 <= p90 <= p95


def test_simulate_queue_position_sample_trajectories():
    """Verifies sample trajectory recording."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=2.0,
        queue_ahead=4.0,
        lambda_limit=1.0,
        mu_cancel=0.1,
        theta_market=1.0,
        time_horizon_sec=10.0,
        num_simulations=10,
        store_sample_trajectories=True,
        random_seed=42,
    )
    assert res.simulated_trajectories_sample is not None
    assert len(res.simulated_trajectories_sample) == 5
    first_traj = res.simulated_trajectories_sample[0]
    assert "sim_idx" in first_traj
    assert "filled" in first_traj
    assert "trajectory" in first_traj
    assert len(first_traj["trajectory"]) >= 1


# ===========================================================================
# 4. Multi-Level LOB Dynamics Simulation Tests
# ===========================================================================

def test_simulate_lob_dynamics_basic():
    """Verifies full multi-level LOB simulation and time-series evolution."""
    init_snap = LOBSnapshot(
        timestamp=0.0,
        bids=[(100.0, 10.0), (99.99, 15.0), (99.98, 20.0)],
        asks=[(100.02, 10.0), (100.03, 15.0), (100.04, 20.0)],
    )
    assert init_snap.best_bid == 100.0
    assert init_snap.best_ask == 100.02
    assert pytest.approx(init_snap.spread, 0.0001) == 0.02
    assert pytest.approx(init_snap.mid_price, 0.0001) == 100.01

    rates = LOBArrivalRates(
        valid=True,
        lambda_limit=2.0,
        mu_cancel=0.05,
        theta_market=1.5,
        observation_duration_sec=60.0,
        total_events=100,
    )

    res = simulate_lob_dynamics(
        initial_snapshot=init_snap,
        arrival_rates=rates,
        time_horizon_sec=5.0,
        tick_size=0.01,
        random_seed=42,
    )

    assert res.valid is True
    assert len(res.timestamps) > 1
    assert len(res.mid_prices) == len(res.timestamps)
    assert len(res.spreads) == len(res.timestamps)
    assert len(res.micro_prices) == len(res.timestamps)
    assert res.final_snapshot is not None
    assert res.final_snapshot.best_bid > 0
    assert res.final_snapshot.best_ask > res.final_snapshot.best_bid


def test_simulate_lob_dynamics_invalid_snapshot():
    """Handles empty or missing initial snapshot without raising."""
    bad_snap = LOBSnapshot(timestamp=0.0, bids=[], asks=[])
    rates = LOBArrivalRates(valid=True, lambda_limit=1.0, mu_cancel=0.1, theta_market=1.0, observation_duration_sec=10.0, total_events=10)
    res = simulate_lob_dynamics(bad_snap, rates)
    assert res.valid is False
    assert len(res.timestamps) == 0


# ===========================================================================
# 5. Slippage & Execution Timing Estimator Tests
# ===========================================================================

def test_estimate_execution_slippage_and_timing_urgency_high():
    """High urgency (alpha=0.95) should recommend AGGRESSIVE_MARKET."""
    rec = estimate_execution_slippage_and_timing(
        quote_bid=100.0,
        quote_ask=100.10,
        order_side="BUY",
        order_size=10.0,
        touch_queue_ahead=50.0,
        lambda_limit=1.0,
        mu_cancel=0.01,
        theta_market=0.5,
        urgency_alpha=0.95,
        random_seed=42,
    )
    assert rec["valid"] is True
    assert rec["recommended_action"] == "AGGRESSIVE_MARKET"
    assert rec["active_crossing_cost"] == 0.50  # 10 * 0.05 half spread


def test_estimate_execution_slippage_and_timing_passive_limit():
    """Low urgency and liquid market (fast fills) should recommend PASSIVE_LIMIT."""
    rec = estimate_execution_slippage_and_timing(
        quote_bid=100.0,
        quote_ask=100.10,
        order_side="BUY",
        order_size=2.0,
        touch_queue_ahead=1.0,  # Very short queue ahead
        lambda_limit=1.0,
        mu_cancel=0.05,
        theta_market=5.0,  # Fast market order flow
        urgency_alpha=0.10,
        random_seed=42,
    )
    assert rec["valid"] is True
    assert rec["recommended_action"] == "PASSIVE_LIMIT"
    assert rec["passive_fill_probability"] > 0.80
    assert rec["passive_expected_savings"] > 0


def test_estimate_execution_slippage_and_timing_degraded():
    """Verifies graceful handling of invalid numeric inputs."""
    rec = estimate_execution_slippage_and_timing(
        quote_bid=None,  # type: ignore
        quote_ask=100.0,
        order_side="BUY",
        order_size=10.0,
        touch_queue_ahead=10.0,
        lambda_limit=1.0,
        mu_cancel=0.1,
        theta_market=1.0,
    )
    assert rec["valid"] is False
    assert rec["recommended_action"] == "AGGRESSIVE_MARKET"


# ===========================================================================
# 6. Serialization & Dict Parity Tests
# ===========================================================================

def test_lob_simulator_serialization_and_dict_parity():
    """Verifies .to_dict() and JSON serializability across all dataclasses."""
    event = OrderFlowEvent(timestamp=1.5, event_type="LIMIT", side="BID", price=50.0, size=5.0)
    event_dict = event.to_dict()
    assert event_dict["event_type"] == "LIMIT"
    assert event["price"] == 50.0

    rates = LOBArrivalRates(
        valid=True,
        lambda_limit=1.5,
        mu_cancel=0.02,
        theta_market=0.8,
        observation_duration_sec=60.0,
        total_events=50,
    )
    rates_dict = rates.to_dict()
    assert rates_dict["lambda_limit"] == 1.5
    assert rates["mu_cancel"] == 0.02
    json_rates = json.dumps(rates_dict)
    assert "lambda_limit" in json_rates

    q_res = QueueSimulationResult(
        valid=True,
        price_level=100.0,
        order_size=5.0,
        queue_ahead=10.0,
        time_horizon_sec=60.0,
        num_simulations=100,
        fill_probability=0.75,
        expected_fill_time_sec=15.2,
        unconditional_fill_time_sec=26.4,
        median_fill_time_sec=14.0,
        prob_adverse_move_before_fill=0.12,
        expected_fill_ratio=0.85,
        queue_depletion_velocity=0.8,
        percentiles_fill_time={"p50": 14.0},
    )
    q_dict = q_res.to_dict()
    assert q_dict["fill_probability"] == 0.75
    assert q_res["median_fill_time_sec"] == 14.0
    json_q = json.dumps(q_dict)
    assert "fill_probability" in json_q


# ===========================================================================
# 7. Additional Robustness, Determinism & Edge Case Tests
# ===========================================================================

def test_simulate_queue_position_seed_determinism():
    """Identical seeds must produce identical simulation outputs."""
    kwargs = dict(
        price_level=100.0,
        order_size=5.0,
        queue_ahead=10.0,
        lambda_limit=2.0,
        mu_cancel=0.05,
        theta_market=1.2,
        time_horizon_sec=30.0,
        num_simulations=500,
        random_seed=12345,
    )
    res1 = simulate_queue_position(**kwargs)
    res2 = simulate_queue_position(**kwargs)

    assert res1.fill_probability == res2.fill_probability
    assert res1.expected_fill_time_sec == res2.expected_fill_time_sec
    assert res1.unconditional_fill_time_sec == res2.unconditional_fill_time_sec
    assert res1.prob_adverse_move_before_fill == res2.prob_adverse_move_before_fill
    assert res1.percentiles_fill_time == res2.percentiles_fill_time


def test_simulate_queue_position_large_queue_ahead_short_horizon():
    """Very large queue ahead with short horizon results in 0 fill and positive depletion velocity."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=10.0,
        queue_ahead=1000.0,
        lambda_limit=5.0,
        mu_cancel=0.01,
        theta_market=1.0,
        time_horizon_sec=5.0,
        num_simulations=200,
        random_seed=42,
    )
    assert res.valid is True
    assert res.fill_probability == 0.0
    assert res.expected_fill_ratio == 0.0
    assert res.queue_depletion_velocity > 0.0


def test_simulate_queue_position_zero_horizon():
    """Zero time horizon produces 0 fill for non-zero order size."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=5.0,
        queue_ahead=5.0,
        lambda_limit=1.0,
        mu_cancel=0.1,
        theta_market=1.0,
        time_horizon_sec=0.0,
    )
    assert res.valid is True
    assert res.fill_probability == 0.0


def test_simulate_queue_position_clamped_negative_inputs():
    """Negative inputs should be gracefully clamped to zero without crashing."""
    res = simulate_queue_position(
        price_level=100.0,
        order_size=-5.0,
        queue_ahead=-10.0,
        lambda_limit=-1.0,
        mu_cancel=-0.5,
        theta_market=-2.0,
    )
    assert res.valid is True
    assert res.fill_probability == 1.0  # Clamped order_size <= 0 yields instant fill


def test_lob_level_and_snapshot_micro_price_weighting():
    """Verifies volume-weighted micro-price when bid and ask sizes are asymmetric."""
    snap = LOBSnapshot(
        timestamp=1.0,
        bids=[(100.0, 30.0)],  # Heavy bid
        asks=[(100.10, 10.0)],  # Light ask
    )
    assert snap.best_bid == 100.0
    assert snap.best_ask == 100.10
    assert pytest.approx(snap.mid_price, 0.0001) == 100.05
    # micro-price = (Ask_Size * Bid + Bid_Size * Ask) / Total_Depth
    # = (10 * 100.0 + 30 * 100.10) / 40 = (1000 + 3003) / 40 = 4003 / 40 = 100.075
    assert pytest.approx(snap.micro_price, 0.0001) == 100.075


# ===========================================================================
# 8. AST Import Safety Test
# ===========================================================================

def test_lob_simulator_ast_import_safety():
    """Verifies that pilots/lob_simulator.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "lob_simulator.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="lob_simulator.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            for forbidden in forbidden_modules:
                assert forbidden not in mod_name, f"Forbidden from-import found: {mod_name}"


# ===========================================================================
# 9. Workstream 2: Placement Optimization & Order Sizing Tests
# ===========================================================================

@pytest.fixture
def sample_5level_lob():
    """Standard 5-level limit order book for testing placement optimizer."""
    bids = [
        (100.00, 50.0),   # Level 1 (Touch)
        (99.95, 120.0),   # Level 2 (1 tick deep)
        (99.90, 200.0),   # Level 3 (2 ticks deep)
        (99.85, 350.0),   # Level 4
        (99.80, 500.0),   # Level 5
    ]
    asks = [
        (100.10, 40.0),   # Level 1 (Touch)
        (100.15, 100.0),  # Level 2 (1 tick deep)
        (100.20, 180.0),  # Level 3 (2 ticks deep)
        (100.25, 300.0),  # Level 4
        (100.30, 450.0),  # Level 5
    ]
    return bids, asks


def test_cont_stoikov_fill_probability_and_latency():
    """Verifies analytical properties of Cont-Stoikov queue fill probability and latency."""
    p_front = calculate_cont_stoikov_fill_probability(queue_position=0.0, depth_at_price=50.0, target_size=10.0, time_horizon=60.0)
    p_deep = calculate_cont_stoikov_fill_probability(queue_position=100.0, depth_at_price=50.0, target_size=10.0, time_horizon=60.0)

    assert 0.01 <= p_front <= 0.99
    assert 0.01 <= p_deep <= 0.99
    assert p_front > p_deep, "Front of queue must have strictly higher fill probability than deep queue"

    # Latency increases with queue depth ahead
    lat_front = calculate_expected_fill_latency(queue_position=5.0, target_size=10.0)
    lat_deep = calculate_expected_fill_latency(queue_position=100.0, target_size=10.0)
    assert lat_deep > lat_front


def test_evaluate_optimal_queue_level_basic(sample_5level_lob):
    """Test standard evaluation of optimal queue placement for buy order."""
    bids, asks = sample_5level_lob
    res = evaluate_optimal_queue_level(
        bids=bids,
        asks=asks,
        target_size=10.0,
        urgency="normal",
        side="buy",
    )

    assert res.valid is True
    assert res.side == "buy"
    assert res.target_size == 10.0
    assert res.mid_price == 100.05
    assert res.spread == pytest.approx(0.10, abs=1e-4)
    assert res.recommended_level in (1, 2, 3)
    assert res.recommended_price > 0.0
    assert 0.0 <= res.queue_position_score <= 1.0
    assert res.expected_fill_latency_sec > 0.0
    assert 0.0 < res.expected_fill_probability <= 1.0
    assert len(res.candidates) >= 3

    # Candidate comparisons
    c1 = res.candidates[0]  # Level 1
    c2 = res.candidates[1]  # Level 2
    c3 = res.candidates[2]  # Level 3

    # Level 1 has highest fill probability & lowest latency
    assert c1["level_index"] == 1
    assert c1["fill_probability"] > c2["fill_probability"] > c3["fill_probability"]
    assert c1["expected_fill_latency_sec"] < c2["expected_fill_latency_sec"] < c3["expected_fill_latency_sec"]

    # Level 2 & 3 capture higher spread than Level 1
    assert c3["spread_capture"] > c2["spread_capture"] > c1["spread_capture"]

    # Dict-like access and to_dict
    assert res["recommended_price"] == res.recommended_price
    d = res.to_dict()
    assert isinstance(d, dict)
    assert d["valid"] is True


def test_evaluate_optimal_queue_level_urgency_tradeoff(sample_5level_lob):
    """
    Test urgency modulation:
    - 'aggressive' / 'immediate' prioritizes fast execution at Level 1.
    - 'passive' is willing to place deeper at Level 2/3 for higher spread capture.
    """
    bids, asks = sample_5level_lob

    # Immediate / Aggressive urgency
    res_agg = evaluate_optimal_queue_level(
        bids=bids,
        asks=asks,
        target_size=10.0,
        urgency="aggressive",
        side="buy",
    )
    assert res_agg.recommended_level == 1, "Aggressive urgency should select Level 1 (Touch)"
    assert res_agg.recommended_price == 100.00

    # Passive urgency
    res_pas = evaluate_optimal_queue_level(
        bids=bids,
        asks=asks,
        target_size=10.0,
        urgency="passive",
        side="buy",
    )
    # Passive urgency favors deeper book (Level 2 or 3) for higher spread capture
    assert res_pas.recommended_level >= 1
    assert res_pas.expected_spread_capture >= res_agg.expected_spread_capture


def test_evaluate_optimal_queue_level_sell_side(sample_5level_lob):
    """Test sell order placement optimization."""
    bids, asks = sample_5level_lob
    res = evaluate_optimal_queue_level(
        bids=bids,
        asks=asks,
        target_size=20.0,
        urgency="normal",
        side="sell",
    )

    assert res.valid is True
    assert res.side == "sell"
    assert res.recommended_price >= 100.10  # Ask touch or higher
    assert res.expected_spread_capture > 0.0

    # For sell side, Level 1 price is best ask (100.10), Level 2 is 100.15
    assert res.candidates[0]["price"] == 100.10
    assert res.candidates[1]["price"] == 100.15
    assert res.candidates[1]["spread_capture"] > res.candidates[0]["spread_capture"]


def test_evaluate_optimal_queue_level_dict_inputs():
    """Test support for dictionary list representation of bids/asks."""
    bids_dict = [
        {"price": 50.25, "size": 100, "orders": 3},
        {"price": 50.20, "size": 250, "orders": 5},
    ]
    asks_dict = [
        {"price": 50.35, "size": 80, "orders": 2},
        {"price": 50.40, "size": 300, "orders": 6},
    ]

    res = evaluate_optimal_queue_level(
        bids=bids_dict,
        asks=asks_dict,
        target_size=15.0,
        urgency="normal",
        side="buy",
    )
    assert res.valid is True
    assert res.mid_price == 50.30
    assert res.spread == pytest.approx(0.10, abs=1e-4)


def test_evaluate_optimal_queue_level_degenerate_cases():
    """Test non-raising robust degradation on empty or invalid inputs (CONSTRAINT #6)."""
    # Empty book
    res_empty = evaluate_optimal_queue_level(bids=[], asks=[], target_size=10.0)
    assert res_empty.valid is False
    assert res_empty.queue_position_score == 0.0
    assert "Empty" in (res_empty.reason or "")

    # Zero target size
    res_zero = evaluate_optimal_queue_level(bids=[(10.0, 100)], asks=[(10.1, 100)], target_size=0.0)
    assert res_zero.valid is False

    # Negative target size
    res_neg = evaluate_optimal_queue_level(bids=[(10.0, 100)], asks=[(10.1, 100)], target_size=-5.0)
    assert res_neg.valid is False


def test_slice_liquidity_order_basic(sample_5level_lob):
    """Test parent order slicing across LOB levels and schedules."""
    bids, asks = sample_5level_lob
    target_size = 150.0  # Large order relative to Level 1 depth (50.0)

    res = slice_liquidity_order(
        target_size=target_size,
        bids=bids,
        asks=asks,
        side="buy",
        max_participation_pct=0.15,
        max_slice_depth_pct=0.30,
        urgency="normal",
    )

    assert res.valid is True
    assert res.total_target_size == 150.0
    assert res.total_sliced_size == pytest.approx(150.0, abs=1e-2)
    assert res.num_slices >= 2
    assert res.estimated_duration_sec > 0.0
    assert len(res.slices) == res.num_slices

    for s in res.slices:
        assert s["size"] > 0.0
        assert s["target_price"] in (100.00, 99.95)
        assert s["participation_rate"] <= 1.0


def test_slice_liquidity_order_urgency_differences(sample_5level_lob):
    """Aggressive urgency creates fewer slices with shorter duration than passive."""
    bids, asks = sample_5level_lob
    target_size = 100.0

    res_agg = slice_liquidity_order(target_size, bids, asks, side="buy", urgency="aggressive")
    res_pas = slice_liquidity_order(target_size, bids, asks, side="buy", urgency="passive")

    assert res_agg.valid is True
    assert res_pas.valid is True
    assert res_agg.num_slices <= res_pas.num_slices, "Aggressive slicing should have fewer slices than passive"


def test_lob_placement_and_order_sizing_integration(sample_5level_lob):
    """
    End-to-end integration test:
    1. Converts a cash budget ($5,000) with safe 75% preset into share sizing.
    2. Runs LOB queue level optimizer on the sized shares.
    3. Validates that the resulting limit order placement fits within cash sizing boundaries.
    """
    bids, asks = sample_5level_lob
    available_cash = 10000.0

    # Calculate 75% safe cash preset ($7,500)
    safe_preset = calculate_safe_cash_preset(available_cash, percentage=0.75)
    assert safe_preset == 7500.0

    # Sizing stock shares at touch price ($100.00) -> 75 shares
    shares = calculate_stock_sizing(safe_preset, price=100.00, allow_fractional=False)
    assert shares == 75.0

    # Optimize placement for 75 shares
    opt = evaluate_optimal_queue_level(
        bids=bids,
        asks=asks,
        target_size=shares,
        urgency="normal",
        side="buy",
    )
    assert opt.valid is True
    assert opt.recommended_price > 0.0

    # Total estimated cost
    est_total = shares * opt.recommended_price
    valid, err = validate_order_sizing(est_total, available_cash, max_position_pct=0.80)
    assert valid is True
    assert err is None

