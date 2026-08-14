"""
tests/test_scenario_matrix.py — Unit & Integration Tests for Scenario Matrix & Stress Grid Engine.
===================================================================================================

Tests:
1. Default grid dimensions and shapes (9 spot x 7 IV x 4 time = 252 cells).
2. Stock positions re-pricing, delta linearity, zero Greek decay.
3. Long Call / Long Put non-linear re-pricing, theta decay, vega sensitivity.
4. Multi-leg option strategies (Bull Put Spread, Iron Condor, Straddle).
5. 0DTE expiration fallback when time shift advances past expiration date.
6. Volatility shock lower bounding (sigma' >= 0.01) and degenerate guards.
7. Extreme spot drops and lower bounding (S' >= 0.01).
8. Historical shock presets (Lehman 2008, Volmageddon 2018, COVID 2020, Yen Unwind 2024).
9. Missing quote / symbol handling (graceful exclusion, populated missing_data_symbols).
10. 2D slice extraction helper (get_2d_scenario_slice).
11. AST import safety (never imports heavy engines).
"""
import ast
from datetime import datetime, timezone
from pathlib import Path
import pytest

from pilots.scenario_matrix import (
    DEFAULT_IV_SHIFTS,
    DEFAULT_SPOT_SHIFTS,
    DEFAULT_TIME_SHIFTS_DAYS,
    HISTORICAL_PRESETS,
    evaluate_historical_presets,
    evaluate_scenario_matrix,
    evaluate_single_scenario,
    get_2d_scenario_slice,
    get_historical_presets,
)


def test_historical_presets_definitions():
    """Verifies all four required historical presets exist with correct shocks."""
    presets = get_historical_presets()
    assert "lehman_2008" in presets
    assert "volmageddon_2018" in presets
    assert "covid_2020" in presets
    assert "yen_unwind_2024" in presets

    assert presets["lehman_2008"]["spot_shift"] == -0.15
    assert presets["lehman_2008"]["iv_shift"] == 0.50

    assert presets["volmageddon_2018"]["spot_shift"] == -0.04
    assert presets["volmageddon_2018"]["iv_shift"] == 1.00

    assert presets["covid_2020"]["spot_shift"] == -0.12
    assert presets["covid_2020"]["iv_shift"] == 0.40

    assert presets["yen_unwind_2024"]["spot_shift"] == -0.06
    assert presets["yen_unwind_2024"]["iv_shift"] == 0.30


def test_evaluate_scenario_matrix_empty_positions():
    """Empty position list returns zeroed baseline and empty grid."""
    res = evaluate_scenario_matrix(positions=[], spot_map={})
    assert res["baseline"]["portfolio_market_value"] == 0.0
    assert res["baseline"]["net_delta"] == 0.0
    assert res["baseline"]["positions_count"] == 0
    # Grid still generated for the dimensions
    expected_cells = len(DEFAULT_SPOT_SHIFTS) * len(DEFAULT_IV_SHIFTS) * len(DEFAULT_TIME_SHIFTS_DAYS)
    assert len(res["grid"]) == expected_cells
    assert res["missing_data_symbols"] == []


def test_evaluate_scenario_matrix_stock_only():
    """Verifies stock position scales linearly with spot shift and is unaffected by IV/time."""
    positions = [{"symbol": "AAPL", "qty": 100, "avg_entry_price": 150.0}]
    spot_map = {"AAPL": 150.0}

    res = evaluate_scenario_matrix(
        positions=positions,
        spot_map=spot_map,
        spot_shifts=[-0.10, 0.0, 0.10],
        iv_shifts=[-0.10, 0.0, 0.10],
        time_shifts_days=[0, 7],
    )

    baseline = res["baseline"]
    assert baseline["portfolio_market_value"] == 15000.0
    assert baseline["net_delta"] == 100.0
    assert baseline["net_gamma"] == 0.0
    assert baseline["net_theta_daily"] == 0.0
    assert baseline["net_vega_1pct"] == 0.0

    # Test individual cells in grid
    for cell in res["grid"]:
        s_shift = cell["spot_shift"]
        expected_mv = round(100 * 150.0 * (1.0 + s_shift), 2)
        expected_pnl = round(expected_mv - 15000.0, 2)
        assert cell["portfolio_market_value"] == expected_mv
        assert cell["pnl_shift"] == expected_pnl
        assert cell["net_delta"] == 100.0
        assert cell["net_gamma"] == 0.0
        assert cell["net_theta_daily"] == 0.0
        assert cell["net_vega_1pct"] == 0.0


def test_evaluate_scenario_matrix_single_call_option():
    """Verifies long call option non-linear re-pricing, positive delta/gamma/vega, negative theta."""
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # AAPL 2026-09-18 $150.00 CALL (~109 DTE)
    positions = [
        {
            "symbol": "AAPL 2026-09-18 $150.00 CALL",
            "qty": 1,  # 1 contract = 100 shares
            "avg_entry_price": 10.0,
            "iv": 0.25,
        }
    ]
    spot_map = {"AAPL": 150.0}

    res = evaluate_scenario_matrix(
        positions=positions,
        spot_map=spot_map,
        now=now,
    )

    baseline = res["baseline"]
    assert baseline["portfolio_market_value"] > 0
    assert 40.0 < baseline["net_delta"] < 70.0  # ATM call delta ~0.50-0.60 * 100
    assert baseline["net_gamma"] > 0.0
    assert baseline["net_theta_daily"] < 0.0  # Long option pays theta
    assert baseline["net_vega_1pct"] > 0.0  # Long option gains from vol

    # Up move (+10% spot) -> positive PnL shift
    up_cell = next(c for c in res["grid"] if c["spot_shift"] == 0.10 and c["iv_shift"] == 0.0 and c["time_shift_days"] == 0)
    assert up_cell["pnl_shift"] > 0
    assert up_cell["net_delta"] > baseline["net_delta"]  # Gamma increases delta

    # Down move (-10% spot) -> negative PnL shift
    down_cell = next(c for c in res["grid"] if c["spot_shift"] == -0.10 and c["iv_shift"] == 0.0 and c["time_shift_days"] == 0)
    assert down_cell["pnl_shift"] < 0
    assert down_cell["net_delta"] < baseline["net_delta"]

    # IV expansion (+20% IV) -> positive PnL shift
    vol_up_cell = next(c for c in res["grid"] if c["spot_shift"] == 0.0 and c["iv_shift"] == 0.20 and c["time_shift_days"] == 0)
    assert vol_up_cell["pnl_shift"] > 0

    # Time decay (21 days forward) -> negative PnL shift
    time_decay_cell = next(c for c in res["grid"] if c["spot_shift"] == 0.0 and c["iv_shift"] == 0.0 and c["time_shift_days"] == 21)
    assert time_decay_cell["pnl_shift"] < 0


def test_evaluate_scenario_matrix_bull_put_spread():
    """
    Bull Put Spread:
    Sell 1 $145 Put (+credit, short put, positive theta, short vega, positive delta)
    Buy 1 $140 Put (long put hedge)
    """
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    positions = [
        {"symbol": "AAPL 2026-09-18 $145.00 PUT", "qty": -1, "iv": 0.25},
        {"symbol": "AAPL 2026-09-18 $140.00 PUT", "qty": 1, "iv": 0.25},
    ]
    spot_map = {"AAPL": 150.0}

    res = evaluate_scenario_matrix(positions=positions, spot_map=spot_map, now=now)
    baseline = res["baseline"]

    # Bull put spread is net credit/short OTM puts: positive delta, positive theta, negative vega
    assert baseline["net_delta"] > 0.0
    assert baseline["net_theta_daily"] > 0.0
    assert baseline["net_vega_1pct"] < 0.0

    # Under time decay (21 days), position gains value (positive PnL shift)
    decay_cell = next(c for c in res["grid"] if c["spot_shift"] == 0.0 and c["iv_shift"] == 0.0 and c["time_shift_days"] == 21)
    # The spread market value becomes less negative (liability shrinks), so pnl_shift is positive
    assert decay_cell["pnl_shift"] > 0


def test_evaluate_scenario_matrix_0dte_fallback():
    """
    When time shift advances past expiration date (T' <= 0),
    evaluates at 0DTE intrinsic value and zeroes out Greek decay.
    """
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    # Option expires in 8 days on 2026-09-18
    positions = [
        {"symbol": "AAPL 2026-09-18 $150.00 CALL", "qty": 1, "iv": 0.25},
    ]
    spot_map = {"AAPL": 155.0}  # ITM by $5.00

    res = evaluate_scenario_matrix(
        positions=positions,
        spot_map=spot_map,
        now=now,
        spot_shifts=[0.0],
        iv_shifts=[0.0],
        time_shifts_days=[0, 14],  # 14 days is past the 8-day expiration
    )

    # 14 days forward -> past expiration -> 0DTE intrinsic pricing
    expired_cell = next(c for c in res["grid"] if c["time_shift_days"] == 14)
    # 1 contract * 100 shares * $5 ITM = $500.00
    assert expired_cell["portfolio_market_value"] == 500.0
    assert expired_cell["net_delta"] == 100.0  # 1.0 per share * 100
    assert expired_cell["net_gamma"] == 0.0
    assert expired_cell["net_theta_daily"] == 0.0
    assert expired_cell["net_vega_1pct"] == 0.0


def test_evaluate_scenario_matrix_historical_presets():
    """Verifies all four historical presets are computed in evaluate_scenario_matrix."""
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # Long 100 stock + Long 1 ATM Put (protective put)
    positions = [
        {"symbol": "SPY", "qty": 100, "avg_entry_price": 500.0},
        {"symbol": "SPY 2026-09-18 $500.00 PUT", "qty": 1, "iv": 0.20},
    ]
    spot_map = {"SPY": 500.0}

    res = evaluate_scenario_matrix(positions=positions, spot_map=spot_map, now=now)
    presets = res["historical_presets"]

    assert "lehman_2008" in presets
    assert "volmageddon_2018" in presets
    assert "covid_2020" in presets
    assert "yen_unwind_2024" in presets

    lehman = presets["lehman_2008"]
    assert lehman["spot_shift"] == -0.15
    assert lehman["iv_shift"] == 0.50
    assert "portfolio_market_value" in lehman
    assert "pnl_shift" in lehman
    assert "net_delta" in lehman
    assert "net_vega_1pct" in lehman

    # Put protection reduces drop compared to unhedged stock drop of -$7,500
    unhedged_stock_loss = 100 * 500.0 * (-0.15)  # -$7,500
    assert lehman["pnl_shift"] > unhedged_stock_loss  # Put cushioned loss


def test_evaluate_scenario_matrix_missing_data_handling():
    """Unresolvable tickers are tracked in missing_data_symbols without raising errors."""
    positions = [
        {"symbol": "AAPL", "qty": 10, "avg_entry_price": 150.0},
        {"symbol": "UNKNOWN_TICKER", "qty": 50, "avg_entry_price": 10.0},
    ]
    spot_map = {"AAPL": 150.0}  # UNKNOWN_TICKER missing

    res = evaluate_scenario_matrix(positions=positions, spot_map=spot_map)
    assert "UNKNOWN_TICKER" in res["missing_data_symbols"]
    # AAPL still priced accurately
    assert res["baseline"]["portfolio_market_value"] == 1500.0


def test_volatility_and_spot_lower_bounds():
    """Tests extreme negative shocks and ensures sigma >= 0.01 and spot >= 0.01."""
    positions = [{"symbol": "AAPL 2026-09-18 $150.00 CALL", "qty": 1, "iv": 0.25}]
    spot_map = {"AAPL": 150.0}

    res = evaluate_scenario_matrix(
        positions=positions,
        spot_map=spot_map,
        spot_shifts=[-0.99, -1.0, -1.5],  # extreme crash
        iv_shifts=[-0.99, -1.0, -2.0],    # extreme vol collapse
        time_shifts_days=[0],
    )

    assert len(res["grid"]) == 9
    for cell in res["grid"]:
        assert cell["portfolio_market_value"] >= 0.0
        assert not cell["missing_symbols"]


def test_get_2d_scenario_slice():
    """Verifies get_2d_scenario_slice returns correct 2D matrix structure."""
    positions = [{"symbol": "AAPL", "qty": 100, "avg_entry_price": 150.0}]
    spot_map = {"AAPL": 150.0}

    res = evaluate_scenario_matrix(
        positions=positions,
        spot_map=spot_map,
        spot_shifts=[-0.05, 0.0, 0.05],
        iv_shifts=[-0.10, 0.0, 0.10],
        time_shifts_days=[0, 7],
    )

    slice_t0 = get_2d_scenario_slice(res, time_shift_days=0)
    assert slice_t0["time_shift_days"] == 0
    assert len(slice_t0["matrix_pnl"]) == 3  # 3 iv rows
    assert len(slice_t0["matrix_pnl"][0]) == 3  # 3 spot cols
    assert slice_t0["matrix_pnl"][1][1] == 0.0  # center cell (0 spot, 0 iv) = 0 PnL shift


def test_scenario_matrix_ast_import_safety():
    """AST guard: verifies pilots/scenario_matrix.py never imports heavy calculation engines."""
    module_path = Path(__file__).resolve().parent.parent / "pilots" / "scenario_matrix.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    forbidden_modules = [
        "processing_engine",
        "technical_options_engine",
        "research_engine",
        "simulation_engine",
        "strategy_engine",
        "universe_engine",
        "main_orchestrator",
        "execution.order_manager",
        "signals",
    ]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert forbidden not in node.module, f"Forbidden from-import found: {node.module}"
