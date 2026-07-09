"""
tests/test_forecasting_engine.py
=================================
Unit coverage for forecasting_engine.py — the ARIMA / Monte Carlo /
Holt-Winters / CNN-LSTM ensemble that feeds Forecast_10/30/60/90.

This file did not exist prior to this change. Focus areas, picked to match
the gaps identified in the test-coverage analysis:

  * run_monte_carlo's daily-vs-annualized mu/sigma guard (F-05) and its
    dead-letter (never-raise) contract.
  * run_arima / run_holt_winters_grid_search short-history and
    exception-path fallbacks (never fabricate a confident price; degrade to
    0.0 / last-observed value per the documented contract).
  * _blend_with_skill — the Tier 2.2 skill-weighted ensemble blend, its
    "never return 0.0" contract (CONSTRAINT #4), and the static
    sector-preference fallback it reproduces when skill data is absent.
  * generate_forecast end-to-end: zero-price short-circuit, tracker wiring
    (update_actuals -> get_skill_weights -> blend -> record_forecasts), and
    dead-letter resilience when the tracker raises (CONSTRAINT #6).

CNN-LSTM / Prophet are heavy optional dependencies (TensorFlow is not even
in requirements.txt; Prophet is present but slow to fit). Tests that don't
specifically target those paths patch the engine's availability flags to
keep this file fast and deterministic regardless of what happens to be
installed in a given environment.
"""

import math
from datetime import datetime, timezone
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import forecasting_engine
from forecasting_engine import ForecastingEngine


# ============================================================================
# Helpers
# ============================================================================

def _price_series(n: int, seed: int = 0, start: float = 100.0, drift: float = 0.0003) -> pd.Series:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    log_returns = rng.normal(drift, 0.015, n)
    prices = start * np.exp(np.cumsum(log_returns))
    return pd.Series(prices, index=dates, name="Close")


@pytest.fixture
def engine():
    return ForecastingEngine()


@pytest.fixture(autouse=True)
def _no_prophet(monkeypatch):
    """Prophet fits are slow (full Bayesian sampling) and orthogonal to what
    this file targets; force the engine's documented "unavailable" fallback
    path so the suite stays fast and deterministic regardless of whether
    prophet happens to be importable in a given environment."""
    monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", False)


@pytest.fixture(autouse=True)
def _no_tensorflow(monkeypatch):
    """CNN-LSTM training is slow and orthogonal to what this file targets
    (TestRunCnnLstmForecastDegradation below tests the unavailable-fallback
    path explicitly and already disables this per-test; this file-wide
    default closes the same gap for TestGenerateForecast's end-to-end tests,
    which call generate_forecast() with >=70 rows of history_series --
    generate_forecast() builds a history_df internally from that series and
    would silently train a real Keras model, undermining the "fast and
    deterministic regardless of what happens to be installed" guarantee
    _no_prophet already states, whenever TensorFlow happens to be importable
    in a given environment (it is not a pinned requirements.txt dependency,
    so this is environment-dependent, not currently triggered in every
    environment -- but the file's own intent is to never depend on that)."""
    monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)


# ============================================================================
# run_monte_carlo
# ============================================================================

class TestRunMonteCarlo:
    def test_returns_three_floats(self, engine):
        mean, low, high = engine.run_monte_carlo(100.0, 0.0003, 0.015, 30, simulations=500)
        assert isinstance(mean, float)
        assert isinstance(low, float)
        assert isinstance(high, float)
        assert low <= mean <= high

    @pytest.mark.parametrize("days_forward", [0, -5])
    def test_non_positive_days_forward_returns_start_price_triple(self, engine, days_forward):
        result = engine.run_monte_carlo(100.0, 0.0003, 0.015, days_forward)
        assert result == (100.0, 100.0, 100.0)

    @pytest.mark.parametrize("simulations", [0, -1])
    def test_non_positive_simulations_returns_start_price_triple(self, engine, simulations):
        result = engine.run_monte_carlo(100.0, 0.0003, 0.015, 30, simulations=simulations)
        assert result == (100.0, 100.0, 100.0)

    def test_exception_path_returns_start_price_triple_never_raises(self, engine):
        """Dead-letter contract: any internal failure must degrade to the
        start price, never propagate (CONSTRAINT #6)."""
        with mock.patch("numpy.random.normal", side_effect=RuntimeError("rng failure")):
            result = engine.run_monte_carlo(150.0, 0.0003, 0.015, 30)
        assert result == (150.0, 150.0, 150.0)

    def test_annualized_mu_sigma_are_normalized_to_daily(self, engine):
        """F-05 guard: when |mu| > 0.05 (a value that can only realistically
        be an annualized figure mistakenly passed as daily), the engine must
        divide mu by 252 and sigma by sqrt(252) BEFORE simulating — otherwise
        drift would compound 252x and the output would explode to a price
        many multiples of the start price over a 30-day horizon."""
        # Deterministic: silence the stochastic shock so only drift matters.
        annualized_mu, annualized_sigma = 0.20, 0.30
        with mock.patch("numpy.random.normal", return_value=np.zeros((10, 30))):
            mean, low, high = engine.run_monte_carlo(
                100.0, annualized_mu, annualized_sigma, days_forward=30, simulations=10
            )

        daily_mu = annualized_mu / 252
        daily_sigma = annualized_sigma / np.sqrt(252)
        expected_drift = (daily_mu - 0.5 * daily_sigma ** 2) * 30
        expected_price = 100.0 * np.exp(expected_drift)

        assert math.isclose(mean, expected_price, rel_tol=1e-6)
        # Sanity bound: without normalization the naive (un-normalized) drift
        # of 0.20*30 = 6.0 would yield exp(6.0) ~ 403x blow-up. The corrected
        # result must stay within a realistic band around the start price.
        assert 50.0 < mean < 200.0

    def test_small_daily_mu_sigma_are_not_renormalized(self, engine):
        """Values already within daily range (|mu| <= 0.05) must pass through
        unchanged -- the guard must not double-divide legitimate daily inputs."""
        daily_mu, daily_sigma = 0.0003, 0.015
        with mock.patch("numpy.random.normal", return_value=np.zeros((5, 10))):
            mean, _, _ = engine.run_monte_carlo(100.0, daily_mu, daily_sigma, days_forward=10, simulations=5)
        expected_drift = (daily_mu - 0.5 * daily_sigma ** 2) * 10
        expected_price = 100.0 * np.exp(expected_drift)
        assert math.isclose(mean, expected_price, rel_tol=1e-6)


# ============================================================================
# run_arima
# ============================================================================

class TestRunArima:
    def test_short_history_returns_zero(self, engine):
        history = np.linspace(100, 110, 20)  # < 30
        assert engine.run_arima(history, days_forward=10) == 0.0

    def test_sufficient_history_returns_a_float(self, engine):
        history = _price_series(80, seed=1).values
        result = engine.run_arima(history, days_forward=5)
        assert isinstance(result, float)

    def test_fit_exception_returns_zero_not_raises(self, engine):
        history = _price_series(80, seed=2).values
        with mock.patch("forecasting_engine.ARIMA", side_effect=RuntimeError("fit failed")):
            result = engine.run_arima(history, days_forward=5)
        assert result == 0.0


# ============================================================================
# run_holt_winters_grid_search
# ============================================================================

class TestRunHoltWinters:
    def test_short_history_returns_zero(self, engine):
        history = np.linspace(100, 110, 20)
        assert engine.run_holt_winters_grid_search(history, days_forward=10) == 0.0

    def test_sufficient_history_returns_a_float(self, engine):
        history = _price_series(80, seed=3).values
        result = engine.run_holt_winters_grid_search(history, days_forward=5)
        assert isinstance(result, float)

    def test_total_fit_failure_falls_back_to_last_observed_value(self, engine):
        """When both the grid-search fit AND the final default fit raise,
        the documented last-resort fallback is the last historical value --
        never a fabricated number, never an exception."""
        history = _price_series(80, seed=4).values
        with mock.patch("forecasting_engine.ExponentialSmoothing", side_effect=RuntimeError("boom")):
            result = engine.run_holt_winters_grid_search(history, days_forward=5)
        assert result == float(history[-1])


# ============================================================================
# run_prophet_forecast
# ============================================================================

class TestRunProphetForecast:
    def test_unavailable_returns_last_price_triple(self, engine):
        series = _price_series(60, seed=5)
        result = engine.run_prophet_forecast(series, days_forward=30)
        last_price = float(series.iloc[-1])
        assert result == (last_price, last_price, last_price)


# ============================================================================
# run_cnn_lstm_forecast — graceful degradation when TensorFlow is absent
# ============================================================================

class TestRunCnnLstmForecastDegradation:
    def test_returns_zero_dict_when_tensorflow_unavailable(self, engine, monkeypatch):
        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)
        df = pd.DataFrame(
            {"Open": [1] * 100, "High": [1] * 100, "Low": [1] * 100,
             "Close": np.linspace(100, 110, 100), "Volume": [1000] * 100},
            index=pd.date_range("2023-01-01", periods=100, freq="B"),
        )
        result = engine.run_cnn_lstm_forecast(df, horizons=(10, 30, 60, 90))
        assert result == {10: 0.0, 30: 0.0, 60: 0.0, 90: 0.0}

    def test_returns_zero_float_when_days_forward_specified_and_unavailable(self, engine, monkeypatch):
        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)
        df = pd.DataFrame(
            {"Open": [1] * 100, "High": [1] * 100, "Low": [1] * 100,
             "Close": np.linspace(100, 110, 100), "Volume": [1000] * 100},
            index=pd.date_range("2023-01-01", periods=100, freq="B"),
        )
        result = engine.run_cnn_lstm_forecast(df, days_forward=30)
        assert result == 0.0

    def test_insufficient_history_returns_zero_result(self, engine):
        df = pd.DataFrame(
            {"Open": [1] * 10, "High": [1] * 10, "Low": [1] * 10,
             "Close": [100.0] * 10, "Volume": [1000] * 10},
            index=pd.date_range("2023-01-01", periods=10, freq="B"),
        )
        result = engine.run_cnn_lstm_forecast(df, horizons=(10, 30))
        assert result == {10: 0.0, 30: 0.0}


# ============================================================================
# _blend_with_skill
# ============================================================================

class TestBlendWithSkill:
    def test_no_models_returns_current_price_never_zero(self):
        """CONSTRAINT #4: an all-failed forecast cycle must surface the
        current price (a known-real number), never a fabricated 0.0."""
        result = ForecastingEngine._blend_with_skill({}, {}, "MC", current_price=123.45)
        assert result == 123.45

    def test_skill_weights_overlap_produces_weighted_average(self):
        forecasts = {"arima": 100.0, "monte_carlo": 120.0}
        weights = {"arima": 0.25, "monte_carlo": 0.75}
        result = ForecastingEngine._blend_with_skill(forecasts, weights, "MC", current_price=110.0)
        expected = 100.0 * 0.25 + 120.0 * 0.75
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_skill_weights_renormalized_when_subset_overlaps(self):
        """skill_weights may reference a model that produced no forecast this
        cycle; the active subset must be renormalized to sum to 1, not left
        as a partial (under-weighted) blend."""
        forecasts = {"arima": 100.0}
        weights = {"arima": 0.4, "cnn_lstm": 0.6}  # cnn_lstm absent from forecasts
        result = ForecastingEngine._blend_with_skill(forecasts, weights, "MC", current_price=110.0)
        assert result == 100.0

    def test_skill_weights_no_overlap_falls_back_to_static(self):
        forecasts = {"monte_carlo": 105.0}
        weights = {"cnn_lstm": 1.0}  # no overlap with forecasts
        result = ForecastingEngine._blend_with_skill(forecasts, weights, "MC", current_price=110.0)
        assert result == 105.0  # static fallback: mc_price only -> returned directly

    def test_skill_weights_all_zero_falls_back_to_static(self):
        forecasts = {"arima": 100.0, "monte_carlo": 120.0}
        weights = {"arima": 0.0, "monte_carlo": 0.0}
        result = ForecastingEngine._blend_with_skill(forecasts, weights, "MC", current_price=110.0)
        # Static fallback for arima+mc both present: 0.4*arima + 0.6*mc
        expected = 100.0 * 0.4 + 120.0 * 0.6
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_static_preferred_holt_winters(self):
        forecasts = {"holt_winters": 95.0, "arima": 100.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "HW", current_price=100.0)
        assert result == 95.0

    def test_static_preferred_arima(self):
        forecasts = {"arima": 102.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "ARIMA", current_price=100.0)
        assert result == 102.0

    def test_static_lstm_and_arima_blend(self):
        forecasts = {"cnn_lstm": 110.0, "arima": 100.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 110.0 * 0.4 + 100.0 * 0.2 + 105.0 * 0.4
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_static_lstm_only_blends_with_mc(self):
        forecasts = {"cnn_lstm": 110.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 110.0 * 0.5 + 105.0 * 0.5
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_static_arima_and_mc_blend_no_lstm(self):
        forecasts = {"arima": 100.0, "monte_carlo": 110.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 100.0 * 0.4 + 110.0 * 0.6
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_static_arima_only(self):
        forecasts = {"arima": 103.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        assert result == 103.0

    def test_static_mc_only(self):
        forecasts = {"monte_carlo": 107.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        assert result == 107.0


# ============================================================================
# generate_forecast — orchestration & tracker wiring
# ============================================================================

class TestGenerateForecast:
    def test_zero_current_price_returns_default_dict_immediately(self, engine):
        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})
        result = engine.generate_forecast(row, current_price=0.0)
        assert result["Forecast_30"] == 0.0
        assert result["ARIMA"] == 0.0

    def test_end_to_end_populates_all_forecast_horizons(self, engine):
        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})
        history = _price_series(90, seed=6)
        result = engine.generate_forecast(row, current_price=float(history.iloc[-1]), history_series=history)
        for h in (10, 30, 60, 90):
            key = f"Forecast_{h}"
            assert key in result
            assert isinstance(result[key], float)
            assert result[key] > 0.0

    def test_unknown_sector_defaults_to_60_day_mc_config(self, engine):
        row = pd.Series({"sector": "Crypto Mining", "Symbol": "ZZZ"})
        result = engine.generate_forecast(row, current_price=50.0)
        assert result["Target_Days"] == 60

    def test_real_estate_sector_uses_90_day_target(self, engine):
        row = pd.Series({"sector": "Real Estate", "Symbol": "O"})
        history = _price_series(90, seed=7)
        result = engine.generate_forecast(row, current_price=float(history.iloc[-1]), history_series=history)
        assert result["Target_Days"] == 90

    def test_tracker_lifecycle_is_called_for_each_horizon(self, engine):
        tracker = mock.MagicMock()
        tracker.get_skill_weights.return_value = {}
        engine._tracker = tracker
        row = pd.Series({"sector": "Technology", "Symbol": "MSFT"})
        history = _price_series(90, seed=8)
        engine.generate_forecast(row, current_price=float(history.iloc[-1]), history_series=history)

        # update_actuals must be invoked once per horizon (4 horizons), before
        # any new forecast price is generated for that cycle.
        assert tracker.update_actuals.call_count == 4
        assert tracker.get_skill_weights.call_count == 4
        assert tracker.record_forecasts.call_count == 4
        called_symbols = {call.args[0] for call in tracker.update_actuals.call_args_list}
        assert called_symbols == {"MSFT"}
        called_horizons = {call.args[1] for call in tracker.update_actuals.call_args_list}
        assert called_horizons == {10, 30, 60, 90}

    def test_tracker_failures_are_swallowed_dead_letter(self, engine):
        """A broken ForecastTracker (e.g. DB locked) must never crash
        forecast generation -- only the skill-tracking side effect is lost
        (CONSTRAINT #6)."""
        tracker = mock.MagicMock()
        tracker.update_actuals.side_effect = RuntimeError("db locked")
        tracker.get_skill_weights.side_effect = RuntimeError("db locked")
        tracker.record_forecasts.side_effect = RuntimeError("db locked")
        engine._tracker = tracker

        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})
        history = _price_series(90, seed=9)
        result = engine.generate_forecast(row, current_price=float(history.iloc[-1]), history_series=history)

        for h in (10, 30, 60, 90):
            assert result[f"Forecast_{h}"] > 0.0

    def test_no_tracker_reproduces_static_blend_behavior(self):
        """ForecastingEngine() with no args must behave identically to the
        pre-Tier-2.2 engine -- no tracker attribute side effects."""
        engine = ForecastingEngine()
        assert engine._tracker is None

    def test_non_tracker_object_is_silently_ignored(self):
        """Passing a non-ForecastTracker object must not raise and must
        disable skill blending (treated as None)."""
        engine = ForecastingEngine(tracker=object())
        assert engine._tracker is None


# ============================================================================
# Wave-1 fit-once refactor — the crux tests
#
# generate_forecast() must fit ARIMA + Holt-Winters ONCE before the horizon
# loop [10,30,60,90] (plus target_days), NOT once per horizon. The old code
# refit ARIMA 5x and re-ran the HW grid-search (2 train fits) + final fit 5x
# (=> 15 ExponentialSmoothing constructions). The refactor collapses those to
# 1 ARIMA construction and 3 ExponentialSmoothing constructions (2 grid-search
# train fits on ["add"]x{True,False} + 1 final full-history fit).
# ============================================================================


class _FakeFitResult:
    """Deterministic stand-in for a fitted statsmodels result: .forecast()
    returns a constant array so no real optimization runs (keeps the fit-count
    test fast and independent of statsmodels/BLAS)."""

    def forecast(self, steps):
        return np.full(int(steps), 105.0)


class TestFitOnceRefactor:
    def test_arima_fit_once_hw_thrice_per_generate_forecast(self, engine, monkeypatch):
        """PROVES THE SPEEDUP: one generate_forecast() call constructs ARIMA
        exactly ONCE (not 5x) and ExponentialSmoothing exactly 3x (not 15x)."""
        counts = {"arima": 0, "hw": 0}

        class FakeArima:
            def __init__(self, *a, **k):
                counts["arima"] += 1

            def fit(self):
                return _FakeFitResult()

        class FakeHW:
            def __init__(self, *a, **k):
                counts["hw"] += 1

            def fit(self):
                return _FakeFitResult()

        monkeypatch.setattr(forecasting_engine, "ARIMA", FakeArima)
        monkeypatch.setattr(forecasting_engine, "ExponentialSmoothing", FakeHW)

        history = _price_series(120, seed=11)
        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})
        # precomputed_garch_annual_vol short-circuits the GARCH estimator so the
        # only ARIMA/ES constructions come from the fit-once path we are counting.
        result = engine.generate_forecast(
            row,
            current_price=float(history.iloc[-1]),
            history_series=history,
            precomputed_garch_annual_vol=0.30,
        )

        assert counts["arima"] == 1, "ARIMA must be fit exactly once, not per-horizon"
        assert counts["hw"] == 3, (
            "Holt-Winters must construct ExponentialSmoothing exactly 3x "
            "(2 grid-search train fits + 1 final full-history fit), not per-horizon"
        )
        # Sanity: the run still produced all four horizons.
        for h in (10, 30, 60, 90):
            assert result[f"Forecast_{h}"] > 0.0


class TestBackCompatShims:
    """run_arima / run_holt_winters_grid_search are now fit-once-then-forecast
    shims; their output must stay numerically identical to the split
    (fit) + (forecast) call sequence on the same data."""

    def test_run_arima_shim_matches_split_fit_forecast(self, engine):
        history = _price_series(80, seed=21).values
        combined = engine.run_arima(history, days_forward=7)
        fitted = engine.run_arima_fit(history)
        split = engine.forecast_from_arima_fit(fitted, 7)
        assert combined == split

    def test_run_hw_shim_matches_split_fit_forecast(self, engine):
        history = _price_series(80, seed=22).values
        combined = engine.run_holt_winters_grid_search(history, days_forward=7)
        fitted = engine.run_holt_winters_fit(history)
        split = engine.forecast_from_hw_fit(fitted, 7, history)
        assert combined == split


class TestPrecomputedGarchSigma:
    """_estimate_daily_sigma's precomputed-annual-vol branch: a finite >0
    precomputed GARCH annual vol is divided by sqrt(252) and fed to Monte
    Carlo, WITHOUT refitting GJR-GARCH; None routes to the estimator."""

    def _capture_mc_sigma(self, engine, monkeypatch):
        captured = {}

        def fake_mc(start_price, mu, sigma, days_forward=None, simulations=1000, **k):
            captured.setdefault("sigma", sigma)
            return (start_price, start_price, start_price)

        monkeypatch.setattr(engine, "run_monte_carlo", fake_mc)
        return captured

    def test_precomputed_vol_folds_in_and_skips_estimator(self, engine, monkeypatch):
        captured = self._capture_mc_sigma(engine, monkeypatch)
        from technical_options_engine import TechnicalOptionsEngine
        spy = mock.MagicMock(return_value=0.99)
        monkeypatch.setattr(
            TechnicalOptionsEngine, "estimate_gjr_garch_volatility", spy
        )

        history = _price_series(60, seed=31)
        engine.generate_forecast(
            pd.Series({"sector": "Technology", "Symbol": "AAPL"}),
            current_price=float(history.iloc[-1]),
            history_series=history,
            precomputed_garch_annual_vol=0.40,
        )

        assert captured["sigma"] == pytest.approx(0.40 / np.sqrt(252.0), rel=1e-6)
        assert spy.call_count == 0, "estimator must NOT be refit when a precomputed vol is supplied"

    def test_none_precomputed_routes_to_estimator(self, engine, monkeypatch):
        captured = self._capture_mc_sigma(engine, monkeypatch)
        from technical_options_engine import TechnicalOptionsEngine
        spy = mock.MagicMock(return_value=0.50)  # annualized vol
        monkeypatch.setattr(
            TechnicalOptionsEngine, "estimate_gjr_garch_volatility", spy
        )

        history = _price_series(60, seed=32)
        engine.generate_forecast(
            pd.Series({"sector": "Technology", "Symbol": "AAPL"}),
            current_price=float(history.iloc[-1]),
            history_series=history,
            precomputed_garch_annual_vol=None,
        )

        assert spy.call_count >= 1, "estimator must be called when no precomputed vol is supplied"
        assert captured["sigma"] == pytest.approx(0.50 / np.sqrt(252.0), rel=1e-6)
