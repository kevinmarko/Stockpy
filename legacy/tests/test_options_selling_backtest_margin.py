"""
Unit tests for validation/options_selling_backtest.py margin tracking.

Split out of tests/test_walk_forward.py (step 4b, options desk archive) --
this module and validation/options_selling_backtest.py itself move to
legacy/ together; the walk-forward tests that stayed in test_walk_forward.py
are about the kept, general-purpose validation/walk_forward.py and have no
dependency on the options desk.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from validation.options_selling_backtest import (
    simulate_options_strategy_with_margin,
    simulate_put_credit_spread_with_margin,
    simulate_vrp_iron_condor_with_margin,
)


def _generate_single_asset_series(n_bars: int = 400, seed: int = 42) -> pd.Series:
    """Generate single asset price Series."""
    rng = np.random.default_rng(seed=seed)
    dates = pd.bdate_range("2020-01-01", periods=n_bars)
    rets = rng.normal(loc=0.0004, scale=0.012, size=n_bars)
    prices = 100.0 * np.cumprod(1.0 + rets)
    return pd.Series(prices, index=dates, name="SPY_SYNTHETIC")


# =============================================================================
# Options Selling Margin Utilization & Dynamic Margin Calls
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
