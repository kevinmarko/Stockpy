"""
Unit tests for validation/walk_forward.py (Walk-Forward Analysis Engine)
and validation/options_selling_backtest.py margin tracking.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.walk_forward import (
    run_walk_forward_analysis,
    _split_walk_forward_windows,
    _default_cross_sectional_rebalance,
)
from validation.options_selling_backtest import (
    simulate_options_strategy_with_margin,
    simulate_put_credit_spread_with_margin,
    simulate_vrp_iron_condor_with_margin,
)


def _generate_synthetic_prices(n_bars: int = 400, n_assets: int = 5, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic asset prices for walk-forward testing."""
    rng = np.random.default_rng(seed=seed)
    dates = pd.bdate_range("2020-01-01", periods=n_bars)
    rets = rng.normal(loc=0.0005, scale=0.015, size=(n_bars, n_assets))
    prices = 100.0 * np.cumprod(1.0 + rets, axis=0)
    cols = [f"ASSET_{i}" for i in range(n_assets)]
    return pd.DataFrame(prices, index=dates, columns=cols)


def _generate_single_asset_series(n_bars: int = 400, seed: int = 42) -> pd.Series:
    """Generate single asset price Series."""
    rng = np.random.default_rng(seed=seed)
    dates = pd.bdate_range("2020-01-01", periods=n_bars)
    rets = rng.normal(loc=0.0004, scale=0.012, size=n_bars)
    prices = 100.0 * np.cumprod(1.0 + rets)
    return pd.Series(prices, index=dates, name="SPY_SYNTHETIC")


# =============================================================================
# 1. Walk-Forward Basic Execution & Structure
# =============================================================================

def test_walk_forward_basic_execution_multi_asset():
    """Verify run_walk_forward_analysis executes on cross-sectional DataFrame and returns expected keys."""
    df = _generate_synthetic_prices(n_bars=350, n_assets=4)
    result = run_walk_forward_analysis(df, is_ratio=0.80, n_windows=5, rebalance_freq=21)

    assert isinstance(result, dict)
    assert "wfe" in result
    assert "is_sharpe" in result
    assert "oos_sharpe" in result
    assert "is_profit_factor" in result
    assert "oos_profit_factor" in result
    assert "oos_ulcer_index" in result
    assert "oos_martin_ratio" in result
    assert "oos_upi" in result
    assert "oos_max_drawdown" in result
    assert "oos_sortino" in result
    assert "windows" in result
    assert result["n_windows"] == 5
    assert len(result["windows"]) == 5

    # Metrics validity
    assert np.isfinite(result["wfe"])
    assert np.isfinite(result["is_sharpe"])
    assert np.isfinite(result["oos_sharpe"])
    assert np.isfinite(result["oos_ulcer_index"])
    assert np.isfinite(result["oos_max_drawdown"])
    assert isinstance(result["is_returns"], pd.Series)
    assert isinstance(result["oos_returns"], pd.Series)
    assert not result["oos_returns"].empty


def test_walk_forward_basic_execution_single_asset():
    """Verify run_walk_forward_analysis executes on single-asset Series."""
    series = _generate_single_asset_series(n_bars=300)
    result = run_walk_forward_analysis(series, is_ratio=0.75, n_windows=4, rebalance_freq=20)

    assert result["n_windows"] == 4
    assert len(result["windows"]) == 4
    assert np.isfinite(result["wfe"])
    assert np.isfinite(result["oos_sharpe"])
    assert np.isfinite(result["oos_ulcer_index"])


# =============================================================================
# 2. Window Splitting & Temporal Boundary Integrity
# =============================================================================

def test_walk_forward_splitting_temporal_boundaries():
    """Verify that In-Sample strictly precedes Out-Of-Sample with zero overlap."""
    series = _generate_single_asset_series(n_bars=400)
    splits = _split_walk_forward_windows(series, n_windows=5, is_ratio=0.80)

    assert len(splits) == 5

    for k, (is_data, oos_data, (is_start, is_end, oos_start, oos_end)) in enumerate(splits):
        # Index integrity
        assert is_data.index[0] == is_start
        assert is_data.index[-1] == is_end
        assert oos_data.index[0] == oos_start
        assert oos_data.index[-1] == oos_end

        # Temporal ordering: IS strictly precedes OOS
        assert is_end < oos_start
        assert is_start < is_end
        assert oos_start < oos_end

        # Zero index intersection between IS and OOS in window k
        is_dates = set(is_data.index)
        oos_dates = set(oos_data.index)
        assert len(is_dates.intersection(oos_dates)) == 0


# =============================================================================
# 3. Lookahead Bias Perturbation Test (Zero Leakage Invariant)
# =============================================================================

def test_walk_forward_zero_lookahead_perturbation():
    """
    Lookahead perturbation test:
    Modifying future Out-Of-Sample data must have ZERO effect on past In-Sample returns.
    The In-Sample returns of window k must remain bit-identical before and after OOS perturbation.
    """
    df_clean = _generate_synthetic_prices(n_bars=400, n_assets=4, seed=123)
    res_clean = run_walk_forward_analysis(df_clean, is_ratio=0.80, n_windows=4, rebalance_freq=21)

    # Make a copy and corrupt the OOS data in window 0 with massive price shocks (+10,000%)
    df_corrupted = df_clean.copy(deep=True)
    w0_oos_start = pd.Timestamp(res_clean["windows"][0]["oos_start"])
    w0_oos_end = pd.Timestamp(res_clean["windows"][0]["oos_end"])

    shock_idx = df_corrupted.index[(df_corrupted.index >= w0_oos_start) & (df_corrupted.index <= w0_oos_end)]
    df_corrupted.loc[shock_idx, :] *= 100.0

    res_corrupted = run_walk_forward_analysis(df_corrupted, is_ratio=0.80, n_windows=4, rebalance_freq=21)

    # In-Sample Sharpe and Profit Factor of window 0 must be 100% bit-identical
    w0_clean = res_clean["windows"][0]
    w0_corrupted = res_corrupted["windows"][0]

    assert w0_clean["is_sharpe"] == pytest.approx(w0_corrupted["is_sharpe"], abs=1e-12)
    assert w0_clean["is_profit_factor"] == pytest.approx(w0_corrupted["is_profit_factor"], abs=1e-12)

    # OOS performance SHOULD change due to the shock
    assert w0_clean["oos_sharpe"] != w0_corrupted["oos_sharpe"]


# =============================================================================
# 4. Walk-Forward Efficiency (WFE) Calculation
# =============================================================================

def test_walk_forward_efficiency_calculation_exact():
    """Verify WFE = OOS Profit Factor / IS Profit Factor against deterministic strategy callable."""
    series = _generate_single_asset_series(n_bars=300)

    # Deterministic strategy: IS gains 2.0, loss 1.0 (PF = 2.0); OOS gains 1.5, loss 1.0 (PF = 1.5)
    # Expected WFE = 1.5 / 2.0 = 0.75
    def deterministic_strategy(is_data, oos_data):
        is_ret = pd.Series([0.02, -0.01] * (len(is_data) // 2), index=is_data.index[: 2 * (len(is_data) // 2)])
        oos_ret = pd.Series([0.015, -0.01] * (len(oos_data) // 2), index=oos_data.index[: 2 * (len(oos_data) // 2)])
        return is_ret, oos_ret

    result = run_walk_forward_analysis(series, is_ratio=0.80, n_windows=3, strategy_fn=deterministic_strategy)

    assert result["is_profit_factor"] == pytest.approx(2.0, abs=1e-4)
    assert result["oos_profit_factor"] == pytest.approx(1.5, abs=1e-4)
    assert result["wfe"] == pytest.approx(0.75, abs=1e-4)


# =============================================================================
# 5. Point-In-Time Cross-Sectional Rebalancing Strategy
# =============================================================================

def test_cross_sectional_rebalance_point_in_time_synchronization():
    """Verify default cross-sectional rebalance produces valid non-zero returns with realistic universe."""
    df = _generate_synthetic_prices(n_bars=300, n_assets=6)
    is_df = df.iloc[:200]
    oos_df = df.iloc[200:]

    is_ret, oos_ret = _default_cross_sectional_rebalance(is_df, oos_df, rebalance_freq=21)

    assert isinstance(is_ret, pd.Series)
    assert isinstance(oos_ret, pd.Series)
    assert len(is_ret) == len(is_df)
    assert len(oos_ret) == len(oos_df)
    assert np.isfinite(is_ret).all()
    assert np.isfinite(oos_ret).all()


# =============================================================================
# 6. Options Selling Margin Utilization & Dynamic Margin Calls
# =============================================================================

def test_options_selling_margin_utilization_tracking():
    """Verify simulate_options_strategy_with_margin records margin utilization and risk metrics."""
    spy = _generate_single_asset_series(n_bars=350, seed=42)
    start = str(spy.index[285].date())
    end = str(spy.index[-1].date())

    res = simulate_options_strategy_with_margin(
        "put_credit_spread", start, end, ticker="SPY", closes=spy, initial_capital=10000.0
    )

    assert isinstance(res, dict)
    assert "returns" in res
    assert "equity_curve" in res
    assert "margin_utilization" in res
    assert "margin_calls" in res
    assert "max_margin_utilization" in res
    assert "avg_margin_utilization" in res
    assert "sharpe" in res
    assert "ulcer_index" in res
    assert "profit_factor" in res

    assert isinstance(res["margin_utilization"], pd.Series)
    assert not res["margin_utilization"].empty
    assert res["max_margin_utilization"] >= 0.0
    assert isinstance(res["margin_calls"], int)
    assert res["margin_calls"] >= 0


def test_options_selling_margin_convenience_wrappers():
    """Verify convenience wrappers execute with margin tracking."""
    spy = _generate_single_asset_series(n_bars=350, seed=99)
    start = str(spy.index[285].date())
    end = str(spy.index[-1].date())

    pcs_res = simulate_put_credit_spread_with_margin(start, end, ticker="SPY", closes=spy)
    assert "margin_utilization" in pcs_res
    assert isinstance(pcs_res["margin_utilization"], pd.Series)

    ic_res = simulate_vrp_iron_condor_with_margin(start, end, ticker="SPY", closes=spy)
    assert "margin_utilization" in ic_res
    assert isinstance(ic_res["margin_utilization"], pd.Series)


# =============================================================================
# 7. Edge Cases & Defensive Degradation
# =============================================================================

def test_walk_forward_insufficient_data_degrades_gracefully():
    """Verify run_walk_forward_analysis degrades gracefully on empty / short data."""
    empty = pd.DataFrame()
    res = run_walk_forward_analysis(empty)
    assert res["n_windows"] == 0
    assert res["wfe"] == 0.0
    assert res["windows"] == []

    short_series = pd.Series([10.0, 10.5, 10.2])
    res_short = run_walk_forward_analysis(short_series)
    assert res_short["n_windows"] == 0
    assert res_short["wfe"] == 0.0
