"""
tests/test_forecasting_improvements.py
======================================
Unit coverage for the two 2026-07 forecasting improvements in
forecasting_engine.py:

  1. GARCH -> Monte Carlo sigma.  ``ForecastingEngine._estimate_daily_sigma``
     sources the Monte Carlo diffusion sigma from the GJR-GARCH(1,1) estimator
     (which returns ANNUALIZED vol), converts it to DAILY via ``/sqrt(252)``,
     floors at 1e-6, and degrades to the caller's historical daily stdev when
     the ``FORECAST_USE_GARCH_SIGMA`` flag is off, ``history_df`` is
     None/insufficient, or the estimator raises.  ``generate_forecast`` computes
     ``mc_sigma`` once and threads it into every ``run_monte_carlo`` call.

  2. Prophet -> ensemble.  At the h=30 horizon iteration Prophet's 30-day yhat is
     injected into ``model_forecasts``, and ``_blend_with_skill``'s STATIC branch
     folds it in as ``base = base*(1-w) + prophet*w`` with
     ``w = settings.FORECAST_PROPHET_WEIGHT`` (default 0.25).  When Prophet is
     absent the static blend is byte-identical to the pre-improvement behavior.

Style mirrors tests/test_forecasting_engine.py: autouse fixtures force
``PROPHET_AVAILABLE`` / ``TENSORFLOW_AVAILABLE`` off by default (so heavy
optional deps never run unless a test opts in), ``run_monte_carlo`` sigma
recording is done with a spy, and ``_blend_with_skill`` static-weight math is
asserted directly.  All offline — no ``network`` marker needed.
"""

import math
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import settings as settings_module
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


def _ohlcv_df(n: int, seed: int = 0, start: float = 100.0, vol: float = 0.02) -> pd.DataFrame:
    """Synthetic OHLCV frame with a genuine daily-return dispersion so the
    GJR-GARCH estimator has something to fit."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    rets = rng.normal(0.0002, vol, n)
    close = start * np.exp(np.cumsum(rets))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.005, n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.005, n)))
    open_ = close * (1.0 + rng.normal(0.0, 0.003, n))
    volume = rng.randint(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


@pytest.fixture
def engine():
    return ForecastingEngine()


@pytest.fixture(autouse=True)
def _no_prophet(monkeypatch):
    """Default the Prophet availability flag off so nothing accidentally fits a
    real Prophet model; Prophet-path tests flip it back on explicitly."""
    monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", False)


@pytest.fixture(autouse=True)
def _no_tensorflow(monkeypatch):
    """Default the TensorFlow availability flag off so generate_forecast never
    trains a real Keras CNN-LSTM from the internally-built history_df."""
    monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)


# ============================================================================
# GARCH -> daily Monte Carlo sigma
# ============================================================================

class TestGarchSigma:
    def test_annualized_garch_vol_is_converted_to_daily_for_monte_carlo(self, engine):
        """CORE CORRECTNESS: estimate_gjr_garch_volatility returns ANNUALIZED vol;
        _estimate_daily_sigma must divide by sqrt(252) before it reaches
        run_monte_carlo. A recorded sigma of ~0.40 (annualized) instead of
        ~0.0252 (daily) would be the bug this test guards against."""
        annual_vol = 0.40
        expected_daily = annual_vol / np.sqrt(252.0)

        recorded_sigmas = []

        def _spy_monte_carlo(start_price, mu, sigma, days_forward, simulations=1000):
            recorded_sigmas.append(sigma)
            return start_price, start_price, start_price

        history = _price_series(90, seed=6)
        hist_df = _ohlcv_df(90, seed=6)
        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})

        with mock.patch.object(
            forecasting_engine.ForecastingEngine, "run_monte_carlo", side_effect=_spy_monte_carlo
        ), mock.patch(
            "technical_options_engine.TechnicalOptionsEngine.estimate_gjr_garch_volatility",
            return_value=annual_vol,
        ):
            engine.generate_forecast(
                row,
                current_price=float(history.iloc[-1]),
                history_series=history,
                history_df=hist_df,
            )

        assert recorded_sigmas, "run_monte_carlo was never called"
        for sigma in recorded_sigmas:
            assert math.isclose(sigma, expected_daily, rel_tol=1e-9)
            # And definitely NOT the raw annualized value.
            assert not math.isclose(sigma, annual_vol, rel_tol=1e-3)

    def test_direct_helper_returns_small_daily_value(self, engine):
        """_estimate_daily_sigma returns a DAILY figure: 0 < x < 0.2, and
        x*sqrt(252) lands in a realistic annualized band [0.02, 3.0]."""
        df_high_vol = _ohlcv_df(300, seed=11, vol=0.03)
        daily = engine._estimate_daily_sigma(df_high_vol, fallback_daily_sigma=0.015)
        assert 0.0 < daily < 0.2
        annualized = daily * np.sqrt(252.0)
        assert 0.02 <= annualized <= 3.0

    def test_fallback_when_flag_off(self, engine):
        df = _ohlcv_df(120, seed=12)
        with mock.patch.object(settings_module.settings, "FORECAST_USE_GARCH_SIGMA", False):
            result = engine._estimate_daily_sigma(df, fallback_daily_sigma=0.0123)
        assert result == 0.0123

    def test_fallback_when_history_df_none(self, engine):
        result = engine._estimate_daily_sigma(None, fallback_daily_sigma=0.0177)
        assert result == 0.0177

    def test_fallback_when_garch_raises(self, engine):
        """A GARCH estimator failure must degrade to the historical stdev, never
        propagate the exception."""
        df = _ohlcv_df(120, seed=13)
        with mock.patch(
            "technical_options_engine.TechnicalOptionsEngine.estimate_gjr_garch_volatility",
            side_effect=RuntimeError("arch fit blew up"),
        ):
            result = engine._estimate_daily_sigma(df, fallback_daily_sigma=0.0201)
        assert result == 0.0201

    def test_fallback_when_insufficient_rows(self, engine):
        df = _ohlcv_df(15, seed=14)  # < 22 rows
        result = engine._estimate_daily_sigma(df, fallback_daily_sigma=0.0155)
        assert result == 0.0155


# ============================================================================
# Prophet -> static-blend ensemble overlay
# ============================================================================

class TestProphetEnsemble:
    def test_overlay_math_default_weight(self):
        """With Prophet present the static blend base is overlaid with the
        Prophet yhat at the default weight 0.25:
        base = 110*0.4 + 100*0.2 + 105*0.4 = 106.0
        result = 106.0*0.75 + 120.0*0.25 = 109.5"""
        forecasts = {"cnn_lstm": 110.0, "arima": 100.0, "monte_carlo": 105.0, "prophet": 120.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 106.0 * 0.75 + 120.0 * 0.25
        assert math.isclose(result, expected, rel_tol=1e-9)
        assert math.isclose(result, 109.5, rel_tol=1e-9)

    def test_overlay_math_custom_weight(self):
        forecasts = {"cnn_lstm": 110.0, "arima": 100.0, "monte_carlo": 105.0, "prophet": 120.0}
        with mock.patch.object(settings_module.settings, "FORECAST_PROPHET_WEIGHT", 0.5):
            result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 106.0 * 0.5 + 120.0 * 0.5  # 113.0
        assert math.isclose(result, expected, rel_tol=1e-9)
        assert math.isclose(result, 113.0, rel_tol=1e-9)

    def test_prophet_weight_zero_leaves_base_unchanged(self):
        forecasts = {"cnn_lstm": 110.0, "arima": 100.0, "monte_carlo": 105.0, "prophet": 120.0}
        with mock.patch.object(settings_module.settings, "FORECAST_PROPHET_WEIGHT", 0.0):
            result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        assert math.isclose(result, 106.0, rel_tol=1e-9)

    def test_regression_prophet_absent_lstm_arima_mc_byte_identical(self):
        forecasts = {"cnn_lstm": 110.0, "arima": 100.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 110.0 * 0.4 + 100.0 * 0.2 + 105.0 * 0.4  # 106.0
        assert math.isclose(result, expected, rel_tol=1e-9)
        assert math.isclose(result, 106.0, rel_tol=1e-9)

    def test_regression_prophet_absent_lstm_mc_byte_identical(self):
        forecasts = {"cnn_lstm": 110.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 110.0 * 0.5 + 105.0 * 0.5  # 107.5
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_regression_prophet_absent_arima_mc_byte_identical(self):
        forecasts = {"arima": 100.0, "monte_carlo": 105.0}
        result = ForecastingEngine._blend_with_skill(forecasts, {}, "MC", current_price=100.0)
        expected = 100.0 * 0.4 + 105.0 * 0.6  # 103.0
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_skill_weighted_path_includes_prophet_generically_no_overlay(self):
        """When skill weights cover prophet, prophet participates in the generic
        weighted average — the overlay (a static-branch-only construct) must NOT
        also fire, so there is no double-counting."""
        result = ForecastingEngine._blend_with_skill(
            {"arima": 100.0, "prophet": 120.0},
            {"arima": 1.0, "prophet": 1.0},
            "MC",
            current_price=100.0,
        )
        assert math.isclose(result, 110.0, rel_tol=1e-9)  # (100 + 120) / 2

    def test_prophet_enters_model_forecasts_only_at_h30(self, engine, monkeypatch):
        """Prophet is computed once (30-day only) and must be injected into
        model_forecasts ONLY on the h=30 blend call, never on h=10/60/90."""
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
        monkeypatch.setattr(
            forecasting_engine.ForecastingEngine,
            "run_prophet_forecast",
            lambda self, series, days_forward, ticker=None: (150.0, 140.0, 160.0),
        )

        recorded = []  # (preferred_model unused) -> capture model_forecasts per call

        def _spy_blend(model_forecasts, skill_weights, preferred_model, current_price):
            recorded.append(dict(model_forecasts))
            return current_price

        monkeypatch.setattr(
            forecasting_engine.ForecastingEngine, "_blend_with_skill", staticmethod(_spy_blend)
        )

        history = _price_series(90, seed=21)
        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})
        engine.generate_forecast(row, current_price=float(history.iloc[-1]), history_series=history)

        # Horizons iterate in order [10, 30, 60, 90] -> index 1 is h=30.
        assert len(recorded) == 4
        h30 = recorded[1]
        assert h30.get("prophet") == 150.0
        for i in (0, 2, 3):
            assert "prophet" not in recorded[i]

    def test_prophet_runs_at_most_once_per_generate_forecast(self, engine, monkeypatch):
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
        prophet_spy = mock.MagicMock(return_value=(150.0, 140.0, 160.0))
        monkeypatch.setattr(
            forecasting_engine.ForecastingEngine,
            "run_prophet_forecast",
            lambda self, series, days_forward, ticker=None: prophet_spy(series, days_forward),
        )

        history = _price_series(90, seed=22)
        row = pd.Series({"sector": "Technology", "Symbol": "AAPL"})
        engine.generate_forecast(row, current_price=float(history.iloc[-1]), history_series=history)

        assert prophet_spy.call_count == 1
