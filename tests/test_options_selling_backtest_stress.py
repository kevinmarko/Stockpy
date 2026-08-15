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

import json
from pathlib import Path
from typing import Callable, Dict, List
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import validation.options_selling_backtest as osb
from validation.options_selling_backtest import (
    simulate_options_strategy_returns,
    simulate_put_credit_spread_returns,
    simulate_call_credit_spread_returns,
    simulate_vrp_iron_condor_returns,
    simulate_call_debit_spread_returns,
    simulate_put_debit_spread_returns,
    simulate_covered_call_returns,
    _OptionLeg,
    _simulate_leg_mtm_pnl,
    _reset_cycle_plan_cache,
    STOP_LOSS_CREDIT_MULTIPLE,
    STOP_LOSS_DEBIT_RATIO,
    TARGET_DTE,
)
from technical_options_engine import OptionsPricingRecommender, TechnicalOptionsEngine
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


# =============================================================================
# Finding A regression: shared MTM helper is behavior-preserving
# =============================================================================

_GOLDEN_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "options_selling_backtest_golden.json"


def _synthetic_spy_from_params(params: dict) -> pd.Series:
    """Reconstruct a deterministic synthetic SPY close series from the exact
    parameters recorded in the golden fixture's ``_meta.synthetic_spy_params``
    -- must match ``capture_golden_final.py``'s generation exactly (same
    ``np.random.default_rng`` recipe as this file's own ``_synthetic_spy``,
    with the fixture's own seed/scale/loc substituted in).
    """
    rng = np.random.default_rng(seed=params["seed"])
    rets = rng.normal(loc=params["loc"], scale=params["scale"], size=params["n"])
    prices = params["base_price"] * np.cumprod(1 + rets)
    idx = pd.bdate_range(end=params["index_end"], periods=params["n"])
    return pd.Series(prices, index=idx, name="SPY_synthetic")


@pytest.fixture(scope="module")
def golden() -> dict:
    with open(_GOLDEN_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def golden_spy(golden: dict) -> pd.Series:
    return _synthetic_spy_from_params(golden["_meta"]["synthetic_spy_params"])


class TestSharedMtmHelperByteIdentical:
    """Proves the Finding-A refactor (6 near-duplicate per-day mark-to-market
    loops -> one shared ``_simulate_leg_mtm_pnl`` helper) is behavior-
    preserving: the golden fixture was captured from the exact pre-refactor
    per-branch ``if/elif`` implementations (see the fixture's own ``_meta``
    for the frozen synthetic-SPY parameters and the capture methodology), and
    this test re-runs the SAME 6 public functions against the SAME
    deterministic input and diffs to a tight tolerance.

    The fixture's ``cycle_plan_replay`` freezes, per cycle, the exact
    ``garch_vol``/``ivr_proxy``/``vrp_proxy`` triple AND the exact
    ``directive`` dict (Strategy/Legs/Net_Premium) the capture machine's REAL
    ``arch``-library GJR-GARCH MLE fit and ``find_strike_for_delta`` root
    solve produced for that cycle. This test replays those frozen values
    (rather than re-running the real fit/solve) via
    ``_patched_cycle_plan_replay`` below, because neither
    ``arch_model(...).fit()`` (MLE optimizer) nor a root-finder's exact
    converged value is guaranteed bit-reproducible across platforms/BLAS/
    scipy versions -- a real, pre-existing property of this codebase's
    GJR-GARCH/strike-selection pipeline (unrelated to the MTM-loop refactor
    this test verifies) that was observed to diverge ~1e-4 between a macOS
    capture machine and GitHub Actions' Linux runner on IDENTICAL input
    data. Freezing every upstream input decouples "is the shared MTM helper
    behavior-preserving" (this test's actual claim, and ALL that remains
    once these are frozen -- pure closed-form Black-Scholes pricing plus
    arithmetic, no iterative optimizer) from "are the real GARCH fit and
    strike solver deterministic across platforms" (a separate, much harder
    property no test here claims).
    """

    @staticmethod
    def _patched_cycle_plan_replay(replay: List[dict]):
        """Monkeypatch ``TechnicalOptionsEngine.estimate_gjr_garch_volatility``
        / ``.calculate_realized_vol_rank`` / ``osb.get_vrp`` /
        ``OptionsPricingRecommender.generate_strategy_pricing_matrix`` to pop
        the next frozen ``(garch_vol, ivr_proxy, vrp_proxy, directive)``
        quadruple from ``replay`` in call order instead of computing it for
        real. Valid because ALL 6 strategies walk the identical cycle/date
        sequence and call these 4 functions identically per cycle, in the
        same order, before any per-strategy filtering happens (verified
        against ``_compute_cycle_plan``/the pre-refactor loop body) -- so one
        capture run's call-order sequence replays correctly for every
        strategy.
        """
        state = {"i": 0}

        def fake_garch(self, trailing) -> float:
            entry = replay[state["i"]]
            assert entry["entry_date"] == str(trailing.index[-1].date()), (
                f"cycle_plan_replay call-order drift at index {state['i']}: "
                f"expected entry_date {entry['entry_date']}, got {trailing.index[-1].date()}"
            )
            return float(entry["garch_vol"])

        def fake_ivr(self, trailing, current_vol) -> float:
            return float(replay[state["i"]]["ivr_proxy"])

        def fake_vrp(ticker: str, current_iv: float, garch_vol: float):
            return float(replay[state["i"]]["vrp_proxy"])

        def fake_matrix(self, true_ivr, current_iv, trend_bias, target_dte=30, vrp=None, macro_dto=None, **kw):
            d = replay[state["i"]]["directive"]
            state["i"] += 1  # advance once per cycle, after the 4th (last) call
            return d

        return (
            patch.object(TechnicalOptionsEngine, "estimate_gjr_garch_volatility", fake_garch),
            patch.object(TechnicalOptionsEngine, "calculate_realized_vol_rank", fake_ivr),
            patch.object(osb, "get_vrp", fake_vrp),
            patch.object(OptionsPricingRecommender, "generate_strategy_pricing_matrix", fake_matrix),
        )

    @pytest.mark.parametrize("strat_name,strat_fn", list(OPTIONS_STRATEGY_FNS.items()))
    def test_matches_pre_refactor_golden_output(
        self, golden: dict, golden_spy: pd.Series, strat_name: str, strat_fn: Callable[..., pd.Series]
    ) -> None:
        meta = golden["_meta"]
        replay = [dict(entry) for entry in meta["cycle_plan_replay"]]  # fresh per-test copy
        _reset_cycle_plan_cache()

        p_garch, p_ivr, p_vrp, p_matrix = self._patched_cycle_plan_replay(replay)
        with p_garch, p_ivr, p_vrp, p_matrix:
            actual = strat_fn(meta["start"], meta["end"], ticker=meta["ticker"], closes=golden_spy)

        expected_index = golden["index"]
        expected_values = np.array(golden[strat_name], dtype=float)
        actual_index = [str(d.date()) for d in actual.index]

        assert actual_index == expected_index, f"{strat_name}: index drifted from golden fixture"
        assert len(actual.values) == len(expected_values)
        assert np.allclose(actual.values.astype(float), expected_values, atol=1e-12, rtol=1e-12), (
            f"{strat_name}: post-refactor output diverged from the pre-refactor golden fixture "
            f"beyond 1e-12 tolerance -- max abs diff = "
            f"{np.max(np.abs(actual.values.astype(float) - expected_values))}"
        )

    def test_golden_fixture_actually_exercises_nonzero_trades(self, golden: dict) -> None:
        """Guards against the golden fixture silently degenerating into an
        all-zero/never-traded series (which would make the byte-identical
        comparison above vacuous for the MTM math itself, not just the guard
        branches). At least the credit-spread and covered-call formulas must
        have real, nonzero mark-to-market activity in this fixture.
        """
        active = {
            name: int(np.count_nonzero(np.array(golden[name], dtype=float)))
            for name in OPTIONS_STRATEGY_FNS
        }
        assert active["put_credit_spread"] > 0
        assert active["call_credit_spread"] > 0
        assert active["put_debit_spread"] > 0
        assert active["covered_call"] > 0


class TestSharedMtmHelperDirectFormulaEquivalence:
    """Direct, hand-computed proof that ``_simulate_leg_mtm_pnl`` reproduces
    each strategy's ORIGINAL per-day formula exactly, independently
    reimplemented here (not copy-pasted from production) -- covers Iron
    Condor and Call Debit Spread specifically, since the golden fixture in
    ``TestSharedMtmHelperByteIdentical`` above happens not to activate those
    two strategies for its chosen synthetic window (real macro gating +
    trend/IVR conditions did not select them that cycle -- see the fixture's
    own nonzero-count guard test).
    """

    @staticmethod
    def _ohlcv(prices: List[float]) -> pd.DataFrame:
        idx = pd.bdate_range("2024-01-02", periods=len(prices))
        return pd.DataFrame({"Close": prices}, index=idx), idx

    def test_iron_condor_formula(self) -> None:
        ohlcv, dates = self._ohlcv([100.0, 101.5, 98.0, 103.0, 96.0, 100.5])
        legs = [
            _OptionLeg("short", "put", 95.0),
            _OptionLeg("long", "put", 90.0),
            _OptionLeg("short", "call", 105.0),
            _OptionLeg("long", "call", 110.0),
        ]
        sigma = 0.22
        net_premium = 2.75
        max_risk = 5.0 * 100.0 - net_premium * 100.0
        stop_loss_threshold = STOP_LOSS_CREDIT_MULTIPLE * net_premium * 100.0

        actual = _simulate_leg_mtm_pnl(
            ohlcv, dates, legs, sigma, net_premium, max_risk, stop_loss_threshold,
        )

        # Independent reimplementation of the ORIGINAL (pre-refactor) Iron
        # Condor per-day loop body.
        expected: Dict[pd.Timestamp, float] = {}
        cumulative_pnl = 0.0
        stop_triggered = False
        for i, d in enumerate(dates):
            if stop_triggered:
                expected[d] = 0.0
                continue
            spot_t = float(ohlcv.loc[d, "Close"])
            days_remaining = max(TARGET_DTE - i, 1)
            T = days_remaining / 365.0
            pricer = OptionsPricingRecommender(stock_price=spot_t)
            mtm_short_put = pricer.black_scholes_pricing_and_greeks(95.0, T, sigma, "put")["Price"]
            mtm_long_put = pricer.black_scholes_pricing_and_greeks(90.0, T, sigma, "put")["Price"]
            mtm_short_call = pricer.black_scholes_pricing_and_greeks(105.0, T, sigma, "call")["Price"]
            mtm_long_call = pricer.black_scholes_pricing_and_greeks(110.0, T, sigma, "call")["Price"]
            cost_to_close = (mtm_short_put - mtm_long_put) + (mtm_short_call - mtm_long_call)
            new_cumulative_pnl = (net_premium - cost_to_close) * 100.0
            daily_pnl = new_cumulative_pnl - cumulative_pnl
            expected[d] = daily_pnl / max_risk
            cumulative_pnl = new_cumulative_pnl
            if -cumulative_pnl > stop_loss_threshold:
                stop_triggered = True

        assert set(actual.keys()) == set(expected.keys())
        for d in dates:
            assert actual[d] == pytest.approx(expected[d], abs=1e-12)

    def test_call_debit_spread_formula(self) -> None:
        ohlcv, dates = self._ohlcv([100.0, 102.0, 104.5, 103.0, 106.0, 108.0])
        k_long_call, k_short_call = 100.0, 110.0
        legs = [
            _OptionLeg("long", "call", k_long_call),
            _OptionLeg("short", "call", k_short_call),
        ]
        sigma = 0.18
        net_debit = 3.20
        net_premium = -net_debit  # raw signed Net_Premium from the directive
        max_risk = net_debit * 100.0
        stop_loss_threshold = STOP_LOSS_DEBIT_RATIO * max_risk

        actual = _simulate_leg_mtm_pnl(
            ohlcv, dates, legs, sigma, net_premium, max_risk, stop_loss_threshold,
        )

        # Independent reimplementation of the ORIGINAL (pre-refactor) Call
        # Debit Spread per-day loop body.
        expected: Dict[pd.Timestamp, float] = {}
        cumulative_pnl = 0.0
        stop_triggered = False
        for i, d in enumerate(dates):
            if stop_triggered:
                expected[d] = 0.0
                continue
            spot_t = float(ohlcv.loc[d, "Close"])
            days_remaining = max(TARGET_DTE - i, 1)
            T = days_remaining / 365.0
            pricer = OptionsPricingRecommender(stock_price=spot_t)
            mtm_long = pricer.black_scholes_pricing_and_greeks(k_long_call, T, sigma, "call")["Price"]
            mtm_short = pricer.black_scholes_pricing_and_greeks(k_short_call, T, sigma, "call")["Price"]
            position_value = mtm_long - mtm_short
            new_cumulative_pnl = (position_value - net_debit) * 100.0
            daily_pnl = new_cumulative_pnl - cumulative_pnl
            expected[d] = daily_pnl / max_risk
            cumulative_pnl = new_cumulative_pnl
            if -cumulative_pnl > stop_loss_threshold:
                stop_triggered = True

        assert set(actual.keys()) == set(expected.keys())
        for d in dates:
            assert actual[d] == pytest.approx(expected[d], abs=1e-12)


# =============================================================================
# Finding B regression: process-local cycle-plan cache eliminates redundant
# per-cycle recomputation across the 6 STRATEGY_REGISTRY options adapters
# =============================================================================

class TestCyclePlanCacheAvoidsRedundantRecompute:
    def test_six_strategies_over_same_window_cost_the_same_as_one(self) -> None:
        """The real regression this cache exists to fix: without it, sweeping
        all 6 options-selling ``STRATEGY_REGISTRY`` adapters over the SAME
        window re-runs the GARCH fit once per adapter (6x). Instrumented via
        a call-counting wrapper around
        ``TechnicalOptionsEngine.estimate_gjr_garch_volatility`` (one call per
        priced cycle) rather than wall-clock timing, to avoid flakiness.
        """
        spy = _synthetic_spy(n=350, seed=42)
        start = str(spy.index[60].date())
        end = str(spy.index[-1].date())

        call_count = {"n": 0}
        original = TechnicalOptionsEngine.estimate_gjr_garch_volatility

        def _counting_wrapper(self_, df):
            call_count["n"] += 1
            return original(self_, df)

        with patch.object(
            osb.TechnicalOptionsEngine, "estimate_gjr_garch_volatility", _counting_wrapper
        ):
            _reset_cycle_plan_cache()
            simulate_put_credit_spread_returns(start, end, ticker="SPY", closes=spy)
            single_strategy_calls = call_count["n"]

            _reset_cycle_plan_cache()
            call_count["n"] = 0
            for fn in (
                simulate_put_credit_spread_returns,
                simulate_call_credit_spread_returns,
                simulate_vrp_iron_condor_returns,
                simulate_call_debit_spread_returns,
                simulate_put_debit_spread_returns,
                simulate_covered_call_returns,
            ):
                fn(start, end, ticker="SPY", closes=spy)
            six_strategy_calls = call_count["n"]

        assert single_strategy_calls > 0, "fixture window produced zero priced cycles -- test is vacuous"
        assert six_strategy_calls == single_strategy_calls, (
            f"expected the 6-strategy sweep to cost exactly the same as one strategy "
            f"({single_strategy_calls} GARCH fits) via the cycle-plan cache, but cost "
            f"{six_strategy_calls} -- caching did not eliminate the redundant recompute"
        )

    def test_cache_key_distinguishes_different_price_data_over_same_nominal_window(self) -> None:
        """Explicit proof the cache cannot silently reuse the wrong plan: two
        Series sharing an identical (ticker, start, end) window but different
        underlying price content must produce two distinct cache entries, not
        one incorrectly-shared entry.
        """
        _reset_cycle_plan_cache()
        spy_a = _synthetic_spy(n=350, seed=42)
        spy_b = _synthetic_spy(n=350, seed=99)
        assert spy_a.index.equals(spy_b.index)
        assert not spy_a.equals(spy_b)

        start = str(spy_a.index[60].date())
        end = str(spy_a.index[-1].date())

        simulate_put_credit_spread_returns(start, end, ticker="SPY", closes=spy_a)
        assert len(osb._CYCLE_PLAN_CACHE) == 1

        simulate_put_credit_spread_returns(start, end, ticker="SPY", closes=spy_b)
        assert len(osb._CYCLE_PLAN_CACHE) == 2

    def test_closes_none_vs_explicit_key_correctly(self) -> None:
        """``stress_scenarios.py``'s ``ReturnsFn`` contract calls with
        ``closes=None`` (triggering an internal download); the cache must key
        off the RESOLVED Series either way, not merely the caller's intent.
        Verified here by pre-populating the cache via an explicit ``closes=``
        call and confirming a second call with the IDENTICAL resolved data
        (simulated by passing the same Series explicitly, standing in for
        what an internal download would resolve to) hits the same entry
        rather than growing the cache.
        """
        _reset_cycle_plan_cache()
        spy = _synthetic_spy(n=350, seed=7)
        start = str(spy.index[60].date())
        end = str(spy.index[-1].date())

        simulate_put_credit_spread_returns(start, end, ticker="SPY", closes=spy)
        assert len(osb._CYCLE_PLAN_CACHE) == 1

        # A second call with an equal-content (but distinct object) Series
        # must hit the SAME cache entry -- content, not identity, is the key.
        spy_copy = spy.copy(deep=True)
        assert spy_copy is not spy
        simulate_call_credit_spread_returns(start, end, ticker="SPY", closes=spy_copy)
        assert len(osb._CYCLE_PLAN_CACHE) == 1

