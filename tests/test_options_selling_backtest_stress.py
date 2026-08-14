"""
InvestYo Quant Platform - Options Backtest x Stress Gate Test Suite
===================================================================
Exercises the REAL ``validation.options_selling_backtest`` return simulators:
  * ``simulate_put_credit_spread_returns``
  * ``simulate_call_credit_spread_returns``
  * ``simulate_vrp_iron_condor_returns``
  * ``simulate_call_debit_spread_returns``
  * ``simulate_put_debit_spread_returns``
  * ``simulate_covered_call_returns``
  * ``simulate_options_strategy_returns``

Tests both online historical Yahoo Finance downloads sliced to each of the
four dated ``validation.stress_scenarios.STRESS_SCENARIOS`` windows (OCT_2008,
FEB_2018, MAR_2020, AUG_2024), and offline deterministic synthetic price
series.
"""

from typing import Callable, Dict, List
import numpy as np
import pandas as pd
import pytest

from validation.options_selling_backtest import (
    simulate_options_strategy_returns,
    simulate_put_credit_spread_returns,
    simulate_call_credit_spread_returns,
    simulate_vrp_iron_condor_returns,
    simulate_call_debit_spread_returns,
    simulate_put_debit_spread_returns,
    simulate_covered_call_returns,
)
from validation.stress_scenarios import (
    STRESS_SCENARIOS,
    run_stress_tests,
    passes_stress_gate,
)

OPTIONS_STRATEGY_FNS: Dict[str, Callable[..., pd.Series]] = {
    "put_credit_spread": simulate_put_credit_spread_returns,
    "call_credit_spread": simulate_call_credit_spread_returns,
    "iron_condor": simulate_vrp_iron_condor_returns,
    "call_debit_spread": simulate_call_debit_spread_returns,
    "put_debit_spread": simulate_put_debit_spread_returns,
    "covered_call": simulate_covered_call_returns,
}

OPTIONS_SELLING_STRATEGY_FNS: Dict[str, Callable[..., pd.Series]] = {
    "put_credit_spread": simulate_put_credit_spread_returns,
    "call_credit_spread": simulate_call_credit_spread_returns,
    "iron_condor": simulate_vrp_iron_condor_returns,
    "covered_call": simulate_covered_call_returns,
}


def _synthetic_spy(n: int = 500, seed: int = 42) -> pd.Series:
    """Return a deterministic SPY-like close series (business days, ~$300)."""
    rng = np.random.default_rng(seed=seed)
    rets = rng.normal(loc=0.0004, scale=0.01, size=n)
    prices = 300.0 * np.cumprod(1 + rets)
    idx = pd.bdate_range(end="2024-12-31", periods=n)
    return pd.Series(prices, index=idx)


# =============================================================================
# Offline / Synthetic Unit Tests
# =============================================================================

class TestOptionsBacktestOffline:
    @pytest.mark.parametrize("strat_name,strat_fn", list(OPTIONS_STRATEGY_FNS.items()))
    def test_all_strategies_run_offline_with_synthetic_closes(self, strat_name, strat_fn):
        spy = _synthetic_spy(n=400)
        start = str(spy.index[100].date())
        end = str(spy.index[-1].date())

        returns = strat_fn(start, end, ticker="SPY", closes=spy)
        assert isinstance(returns, pd.Series)
        if not returns.empty:
            assert np.isfinite(returns).all()
            assert returns.index.is_monotonic_increasing

    def test_empty_closes_returns_empty_series(self):
        empty = pd.Series(dtype=float)
        ret = simulate_options_strategy_returns("put_credit_spread", "2020-01-01", "2020-06-01", closes=empty)
        assert isinstance(ret, pd.Series)
        assert ret.empty

    def test_insufficient_warmup_returns_zeros(self):
        # With < WARMUP_TRADING_DAYS (280), returns are all zero (flat/cash)
        spy = _synthetic_spy(n=100)
        start = str(spy.index[0].date())
        end = str(spy.index[-1].date())
        ret = simulate_options_strategy_returns("iron_condor", start, end, closes=spy)
        assert isinstance(ret, pd.Series)
        assert not ret.empty
        assert (ret == 0.0).all()

    @pytest.mark.parametrize("strat_name", [
        "put_credit_spread", "call_credit_spread", "iron_condor",
        "call_debit_spread", "put_debit_spread", "covered_call", "dynamic"
    ])
    def test_simulate_options_strategy_returns_dispatcher(self, strat_name):
        spy = _synthetic_spy(n=350)
        start = str(spy.index[290].date())
        end = str(spy.index[-1].date())
        ret = simulate_options_strategy_returns(strat_name, start, end, closes=spy)
        assert isinstance(ret, pd.Series)
        if not ret.empty:
            assert np.isfinite(ret).all()


# =============================================================================
# Online / Stress Gate Tests (Yahoo Finance Network Dependent)
# =============================================================================

@pytest.mark.network
@pytest.mark.parametrize("strat_name,strat_fn", list(OPTIONS_STRATEGY_FNS.items()))
@pytest.mark.parametrize("scenario_name", list(STRESS_SCENARIOS.keys()))
def test_scenario_window_produces_well_formed_returns_for_all_strategies(strat_name, strat_fn, scenario_name):
    scenario = STRESS_SCENARIOS[scenario_name]
    returns = strat_fn(scenario.start, scenario.end, ticker="SPY")
    # Never raises regardless of gate state (CONSTRAINT #6); a genuinely
    # gate-closed-throughout window degrades to an all-zero series, which is
    # itself well-formed (finite, real index), not empty/NaN.
    assert isinstance(returns, pd.Series)
    if not returns.empty:
        assert np.isfinite(returns).all()
        assert returns.index.is_monotonic_increasing


@pytest.mark.network
@pytest.mark.parametrize("strat_name,strat_fn", list(OPTIONS_SELLING_STRATEGY_FNS.items()))
def test_full_stress_gate_runs_end_to_end_for_all_options_selling_strategies(strat_name, strat_fn):
    """The real stress-gate evaluation run for each options-selling strategy.
    No result is hardcoded -- this test's job is to prove the pipeline produces
    a genuine, well-formed verdict (never a crash, never a fabricated number).
    """
    results = run_stress_tests(
        lambda start, end: strat_fn(start, end, ticker="SPY")
    )
    assert set(results.keys()) == set(STRESS_SCENARIOS.keys())

    for name, result in results.items():
        assert result.error is None, f"{name} ({strat_name}): unexpected data-gap error: {result.error}"
        assert np.isfinite(result.max_drawdown)
        assert isinstance(result.survived, bool)

    gate_result = passes_stress_gate(results)
    assert isinstance(gate_result, bool)

