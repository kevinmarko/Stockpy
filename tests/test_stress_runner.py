"""
InvestYo Quant Platform - Stress Runner Sanity Tests
======================================================
Confirms the dated stress windows (validation/stress_scenarios.py) are real
trading windows that produce data, and that the runner executes end-to-end
without error against both a real (yfinance) returns provider and a
deterministic synthetic one.
"""

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from validation.stress_scenarios import (
    STRESS_SCENARIOS,
    StressScenario,
    run_stress_tests,
    run_stress_scenario,
    compute_max_drawdown,
    account_survived,
)


def _spy_returns_fn(start: str, end: str) -> pd.Series:
    """A real returns provider: daily SPY returns over the window (a buy-and-hold
    proxy). yfinance's `end` is exclusive, so nudge it out by a day so the last
    in-window session is included."""
    end_inclusive = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download("SPY", start=start, end=end_inclusive, progress=False)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    close = df["Close"].squeeze()
    return close.pct_change().dropna()


# =============================================================================
# Date ranges produce data (network)
# =============================================================================
@pytest.mark.network
@pytest.mark.parametrize("name", list(STRESS_SCENARIOS.keys()))
def test_each_scenario_window_produces_data(name):
    scenario = STRESS_SCENARIOS[name]
    returns = _spy_returns_fn(scenario.start, scenario.end)
    assert not returns.empty, f"Scenario {name} ({scenario.start}..{scenario.end}) produced no SPY data"
    assert len(returns) >= 3, f"Scenario {name} window unexpectedly short ({len(returns)} days)"


@pytest.mark.network
def test_runner_executes_end_to_end_on_real_data():
    results = run_stress_tests(_spy_returns_fn)
    # Every canonical scenario must have a result, and none should error out on
    # real SPY data (the windows are valid trading ranges).
    assert set(results.keys()) == set(STRESS_SCENARIOS.keys())
    for name, res in results.items():
        assert res.error is None, f"{name} errored: {res.error}"
        assert res.n_days > 0
        assert not np.isnan(res.max_drawdown)
        # SPY itself never blows up an account (returns never <= -100%).
        assert res.survived is True


# =============================================================================
# Runner executes on deterministic synthetic data (no network)
# =============================================================================
def test_runner_executes_on_synthetic_returns():
    def flat_small_gains(start, end):
        idx = pd.bdate_range(start=start, end=end)
        return pd.Series(0.001, index=idx)  # +0.1%/day, no drawdown

    results = run_stress_tests(flat_small_gains)
    assert set(results.keys()) == set(STRESS_SCENARIOS.keys())
    for res in results.values():
        assert res.error is None
        assert res.survived is True
        assert res.max_drawdown == 0.0  # monotonically rising equity


def test_runner_records_error_when_returns_fn_yields_no_data():
    def empty_fn(start, end):
        return pd.Series(dtype=float)

    sc = StressScenario("TEST", "2020-01-01", "2020-01-31", 0.5, "synthetic")
    res = run_stress_scenario(empty_fn, sc)
    assert res.error is not None
    assert res.survived is False
    assert res.passed is False


def test_runner_catches_returns_fn_exception():
    def boom_fn(start, end):
        raise RuntimeError("data vendor down")

    sc = StressScenario("TEST", "2020-01-01", "2020-01-31", 0.5, "synthetic")
    res = run_stress_scenario(boom_fn, sc)
    assert res.error is not None and "data vendor down" in res.error
    assert res.passed is False


# =============================================================================
# Drawdown / survival primitives
# =============================================================================
def test_compute_max_drawdown_known_value():
    # +10% then -50%: equity 1.1 -> 0.55, drawdown from peak = (0.55-1.1)/1.1 = -0.5
    returns = pd.Series([0.10, -0.50])
    assert compute_max_drawdown(returns) == pytest.approx(0.50, abs=1e-9)


def test_compute_max_drawdown_empty_is_nan():
    assert np.isnan(compute_max_drawdown(pd.Series(dtype=float)))


def test_account_survival_blowup_detected():
    # A -100% day wipes the account.
    assert account_survived(pd.Series([0.01, -1.0, 0.02])) is False
    assert account_survived(pd.Series([0.01, -0.30, 0.02])) is True
