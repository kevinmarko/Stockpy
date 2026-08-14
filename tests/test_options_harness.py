"""Tests for validation/options_harness.py (Options Strategy Validation Harness)."""

from datetime import datetime, timedelta
import math
import numpy as np
import pandas as pd
import pytest

from validation.options_harness import (
    OptionLegSpec,
    OptionsStrategySpec,
    OptionsValidationHarness,
    STANDARD_OPTIONS_STRATEGIES,
    _black_scholes_price,
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
