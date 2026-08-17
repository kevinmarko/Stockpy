"""Tests for validation/options_harness.py (Options Strategy Validation Harness)."""

from datetime import datetime, timedelta
from unittest import mock
import math
import numpy as np
import pandas as pd
import pytest

from validation.options_harness import (
    OptionLegSpec,
    OptionsStrategySpec,
    OptionsTradeRecord,
    OptionsValidationHarness,
    STANDARD_OPTIONS_STRATEGIES,
    _black_scholes_delta,
    _black_scholes_price,
    _lookup_vix,
    _trailing_ivr,
)


def test_black_scholes_pricing_intrinsic_at_expiration():
    # Call intrinsic
    assert _black_scholes_price(110.0, 100.0, 0.0, 0.20, option_type="call") == 10.0
    assert _black_scholes_price(90.0, 100.0, 0.0, 0.20, option_type="call") == 0.0

    # Put intrinsic
    assert _black_scholes_price(90.0, 100.0, 0.0, 0.20, option_type="put") == 10.0
    assert _black_scholes_price(110.0, 100.0, 0.0, 0.20, option_type="put") == 0.0


def test_black_scholes_pricing_positive_time():
    spot = 100.0
    strike = 100.0
    t_years = 30.0 / 365.0
    sigma = 0.25
    r = 0.05

    call_p = _black_scholes_price(spot, strike, t_years, sigma, r, option_type="call")
    put_p = _black_scholes_price(spot, strike, t_years, sigma, r, option_type="put")

    # Put-Call Parity: C - P = S - K * exp(-r*T)
    disc_k = strike * math.exp(-r * t_years)
    diff = call_p - put_p
    expected_diff = spot - disc_k
    assert abs(diff - expected_diff) < 1e-4


def test_standard_options_strategies_registry():
    assert "Put Credit Spread" in STANDARD_OPTIONS_STRATEGIES
    assert "Call Credit Spread" in STANDARD_OPTIONS_STRATEGIES
    assert "Iron Condor" in STANDARD_OPTIONS_STRATEGIES
    assert "Bull Call Spread" in STANDARD_OPTIONS_STRATEGIES
    assert "Bear Put Spread" in STANDARD_OPTIONS_STRATEGIES
    assert "Long Straddle" in STANDARD_OPTIONS_STRATEGIES

    pcs = STANDARD_OPTIONS_STRATEGIES["Put Credit Spread"]
    assert len(pcs.legs) == 2
    assert pcs.target_profit_pct > 0
    assert pcs.stop_loss_multiple > 0


def test_options_harness_run_backtest_with_synthetic_data():
    # Create 250 trading days of synthetic SPY prices
    start_dt = datetime(2023, 1, 1)
    dates = [start_dt + timedelta(days=i) for i in range(250)]
    
    # Moderate upward drift with volatility
    np.random.seed(42)
    prices = [100.0]
    for _ in range(249):
        ret = np.random.normal(0.0004, 0.01)
        prices.append(prices[-1] * (1.0 + ret))

    df = pd.DataFrame(
        {
            "Open": prices,
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.99 for p in prices],
            "Close": prices,
            "Volume": [1000000] * 250,
        },
        index=dates,
    )

    harness = OptionsValidationHarness()
    res = harness.run_backtest(
        strategy="Put Credit Spread",
        ticker="SPY",
        start_date="2023-01-01",
        end_date="2023-09-08",
        initial_capital=100000.0,
        price_df=df,
        allocation_pct=0.05,
    )

    assert res.strategy_name == "Put Credit Spread"
    assert res.ticker == "SPY"
    assert res.initial_capital == 100000.0
    assert res.final_capital > 0
    assert len(res.equity_curve) > 0
    assert len(res.daily_returns) == 250
    assert res.total_trades > 0
    assert 0.0 <= res.win_rate_pct <= 100.0
    assert res.max_drawdown_pct >= 0.0


# ---------------------------------------------------------------------------
# Real entry-condition fields on OptionsTradeRecord (2026-08, closes audit
# finding F3 -- api/pilots_api.py::post_options_meta_model_retrain previously
# fed the ML meta-labeler hardcoded literals for every trade; these fields
# make the real, already-computed backtest quantities available instead).
# ---------------------------------------------------------------------------


def _synthetic_price_df(n_days: int = 400, seed: int = 7) -> pd.DataFrame:
    start_dt = datetime(2022, 1, 1)
    dates = [start_dt + timedelta(days=i) for i in range(n_days)]
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n_days - 1):
        ret = rng.normal(0.0003, 0.011)
        prices.append(prices[-1] * (1.0 + ret))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.99 for p in prices],
            "Close": prices,
            "Volume": [1_000_000] * n_days,
        },
        index=dates,
    )


def _flat_vix_series(df: pd.DataFrame, base: float = 18.0) -> pd.Series:
    """A real-shaped (but synthetic) VIX series covering every date in df,
    used to stand in for a live FRED fetch in tests."""
    return pd.Series(
        {pd.Timestamp(d): base + (i % 7) for i, d in enumerate(df.index)}
    )


def test_black_scholes_delta_bounds_and_signs():
    # Deep ITM call -> delta near 1.0; deep OTM call -> near 0.0
    assert _black_scholes_delta(150.0, 100.0, 0.1, 0.20, option_type="call") > 0.9
    assert _black_scholes_delta(60.0, 100.0, 0.1, 0.20, option_type="call") < 0.1
    # Deep ITM put -> delta near -1.0; deep OTM put -> near 0.0
    assert _black_scholes_delta(60.0, 100.0, 0.1, 0.20, option_type="put") < -0.9
    assert _black_scholes_delta(150.0, 100.0, 0.1, 0.20, option_type="put") > -0.1
    # At expiration, delta collapses to the ITM indicator
    assert _black_scholes_delta(110.0, 100.0, 0.0, 0.20, option_type="call") == 1.0
    assert _black_scholes_delta(90.0, 100.0, 0.0, 0.20, option_type="call") == 0.0


def test_trailing_ivr_none_below_min_obs():
    short_window = pd.Series([0.20, 0.21, 0.19])
    assert _trailing_ivr(short_window, current_iv=0.20, min_obs=20) is None


def test_trailing_ivr_basic_percentile_rank():
    window = pd.Series([0.10 + 0.01 * i for i in range(30)])  # 0.10 .. 0.39
    # current_iv equal to the max of the window -> 100th percentile
    rank = _trailing_ivr(window, current_iv=0.39, min_obs=20)
    assert rank == pytest.approx(100.0)
    # current_iv equal to the min -> low percentile (1/30 values <= it)
    low_rank = _trailing_ivr(window, current_iv=0.10, min_obs=20)
    assert low_rank < 10.0


def test_lookup_vix_hit_and_miss():
    series = pd.Series({pd.Timestamp("2023-05-01"): 22.5, pd.Timestamp("2023-05-02"): 19.0})
    assert _lookup_vix("2023-05-01", series) == pytest.approx(22.5)
    # A date genuinely absent from the series is an honest None, not a
    # fabricated fallback.
    assert _lookup_vix("2023-05-03", series) is None
    assert _lookup_vix("2023-05-01", pd.Series(dtype=float)) is None


def test_run_backtest_populates_real_entry_condition_fields():
    df = _synthetic_price_df(n_days=400, seed=11)
    vix_series = _flat_vix_series(df)

    harness = OptionsValidationHarness()
    with mock.patch.object(harness, "_fetch_vix_series", return_value=vix_series):
        res = harness.run_backtest(
            strategy="Put Credit Spread",
            ticker="SPY",
            start_date=df.index[0].strftime("%Y-%m-%d"),
            end_date=df.index[-1].strftime("%Y-%m-%d"),
            initial_capital=100000.0,
            price_df=df,
            allocation_pct=0.05,
        )

    assert res.total_trades > 0
    trades_with_ivr = [t for t in res.trades if t.entry_ivr is not None]
    # Enough history (400 days) that at least the later trades have a full
    # trailing-IVR window.
    assert len(trades_with_ivr) > 0

    for t in res.trades:
        # entry_vrp, entry_short_delta, entry_credit_to_width_ratio, entry_vix
        # are all real, computed quantities and should be populated for
        # every trade in this scenario (VIX series covers every date; a
        # short leg always exists for a credit spread).
        assert t.entry_vrp is not None
        assert t.entry_short_delta is not None
        assert 0.0 <= t.entry_short_delta <= 1.0
        assert t.entry_credit_to_width_ratio is not None
        assert t.entry_credit_to_width_ratio > 0.0
        assert t.entry_vix is not None
        if t.entry_ivr is not None:
            assert 0.0 <= t.entry_ivr <= 100.0

    # Real, non-degenerate values -- proves these aren't a hardcoded constant
    # (the bug this fix closes: every trade previously fed the ML
    # meta-labeler the exact same ivr=50.0/vrp=0.02/vix=20.0/... literals).
    vrps = {round(t.entry_vrp, 6) for t in res.trades}
    assert len(vrps) > 1, "entry_vrp should vary across trades, not be a constant"
    deltas = {round(t.entry_short_delta, 6) for t in res.trades}
    assert len(deltas) > 1, "entry_short_delta should vary across trades, not be a constant"


def test_run_backtest_entry_vix_none_when_no_vix_data():
    """An empty (unavailable) VIX series degrades every trade's entry_vix to
    None -- never a fabricated fallback like the old hardcoded 20.0."""
    df = _synthetic_price_df(n_days=200, seed=3)
    harness = OptionsValidationHarness()
    with mock.patch.object(harness, "_fetch_vix_series", return_value=pd.Series(dtype=float)):
        res = harness.run_backtest(
            strategy="Put Credit Spread",
            ticker="SPY",
            start_date=df.index[0].strftime("%Y-%m-%d"),
            end_date=df.index[-1].strftime("%Y-%m-%d"),
            initial_capital=100000.0,
            price_df=df,
            allocation_pct=0.05,
        )
    assert res.total_trades > 0
    assert all(t.entry_vix is None for t in res.trades)


def test_run_backtest_long_straddle_has_no_short_leg_delta():
    """Long Straddle's legs are both 'buy' -- there is no real short leg to
    compute a delta for, so entry_short_delta must stay None rather than
    fabricate a value for a leg that doesn't exist."""
    df = _synthetic_price_df(n_days=250, seed=5)
    vix_series = _flat_vix_series(df)
    harness = OptionsValidationHarness()
    with mock.patch.object(harness, "_fetch_vix_series", return_value=vix_series):
        res = harness.run_backtest(
            strategy="Long Straddle",
            ticker="SPY",
            start_date=df.index[0].strftime("%Y-%m-%d"),
            end_date=df.index[-1].strftime("%Y-%m-%d"),
            initial_capital=100000.0,
            price_df=df,
            allocation_pct=0.05,
        )
    assert res.total_trades > 0
    assert all(t.entry_short_delta is None for t in res.trades)
