"""
Unit tests for institutional quantitative metrics in validation/metrics.py:
- Profit Factor
- Ulcer Index
- Ulcer Performance Index (UPI / Martin Ratio)
- Walk-Forward Efficiency (WFE) Ratio
"""
import numpy as np
import pandas as pd
import pytest

from validation.metrics import (
    profit_factor,
    ulcer_index,
    ulcer_performance_index,
    walk_forward_efficiency_ratio,
)


def test_profit_factor_calculation():
    """Verify gross profits / gross losses calculation and edge cases."""
    # 3 winning days (+0.02, +0.03, +0.01 = +0.06), 2 losing days (-0.01, -0.01 = -0.02) -> PF = 3.0
    returns = pd.Series([0.02, -0.01, 0.03, -0.01, 0.01])
    pf = profit_factor(returns)
    assert pf == pytest.approx(3.0, abs=1e-4)

    # All wins -> np.inf
    assert np.isinf(profit_factor(pd.Series([0.01, 0.02, 0.03])))

    # All losses -> 0.0
    assert profit_factor(pd.Series([-0.01, -0.02])) == 0.0

    # Empty series -> NaN
    assert np.isnan(profit_factor(pd.Series([], dtype=float)))


def test_ulcer_index_calculation():
    """Verify Ulcer Index root-mean-square percentage drawdown calculation."""
    # Flat/strictly increasing equity -> 0 drawdown -> UI = 0.0
    returns_up = pd.Series([0.01, 0.01, 0.01, 0.01, 0.01])
    assert ulcer_index(returns_up) == pytest.approx(0.0, abs=1e-5)

    # 10% drop on day 2 that does not recover
    returns_drop = pd.Series([0.0, -0.10, 0.0, 0.0])
    # Peak = 1.0, Equity = [1.0, 0.90, 0.90, 0.90] -> Drawdown = [0%, -10%, -10%, -10%]
    # Mean of squared drawdowns = (0 + 100 + 100 + 100) / 4 = 75.0 -> sqrt(75.0) = 8.660%
    ui = ulcer_index(returns_drop)
    assert ui == pytest.approx(np.sqrt(75.0), abs=1e-4)


def test_ulcer_performance_index():
    """Verify UPI (Martin Ratio) annualized return per unit of UI downside risk."""
    # Mean daily return 0.0005 (~12.6% annual), UI = 5.0%
    # If UI = 0.0 -> returns np.inf if return > 0
    returns_up = pd.Series([0.01, 0.01, 0.01])
    assert np.isinf(ulcer_performance_index(returns_up))

    returns = pd.Series([0.02, -0.01, 0.01, -0.005, 0.015, -0.002, 0.01])
    upi = ulcer_performance_index(returns, freq=252)
    assert isinstance(upi, float)
    assert upi > 0.0


def test_walk_forward_efficiency_ratio():
    """Verify Walk-Forward Efficiency (OOS Profit Factor / IS Profit Factor)."""
    is_returns = pd.Series([0.02, -0.01, 0.03, -0.01])  # PF = 0.05 / 0.02 = 2.5
    oos_returns = pd.Series([0.015, -0.01, 0.02, -0.01])  # PF = 0.035 / 0.02 = 1.75

    wfe = walk_forward_efficiency_ratio(is_returns, oos_returns)
    assert wfe == pytest.approx(1.75 / 2.5, abs=1e-4)
    assert wfe > 0.50  # Stable edge passing WFE threshold
