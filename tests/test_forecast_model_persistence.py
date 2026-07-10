"""
tests/test_forecast_model_persistence.py
=========================================
PR C (performance overhaul) — CNN-LSTM + Prophet model persistence.

Covers:
  * forecasting/model_persistence.py — pure save/load-path + staleness logic
    (artifact_path, is_fresh, touch, ticker sanitization). No TF/Prophet needed.
  * run_prophet_forecast's persistence wiring, exercised end-to-end with a
    lightweight picklable stand-in Prophet class (the real `prophet` package
    is a slow/optional dependency this suite must not require): cache-miss ->
    fit + save; cache-hit -> skip fit, inference only, same result; disabled
    (default) -> no artifact ever written, behavior unchanged.
  * run_cnn_lstm_forecast's persistence wiring: the cache-HIT path (the new
    code) exercised via a fake `tf.keras.models.load_model`, proving the
    expensive Sequential/model.fit() path is skipped entirely; the
    TensorFlow-unavailable safety path (ticker + persistence enabled but no
    TF installed) stays byte-identical to the pre-PR-C zero-result contract.
  * generate_forecast threads ticker=None for an unlabeled/synthetic row so
    ad-hoc/test calls never touch the on-disk artifact cache.
  * settings defaults: FORECAST_MODEL_PERSISTENCE_ENABLED=False (opt-in,
    matching the FORECAST_USE_GARCH_SIGMA / FORECAST_SKILL_WEIGHTING_ENABLED
    convention), FORECAST_MODEL_RETRAIN_DAYS=7.
"""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import forecasting_engine
from forecasting_engine import ForecastingEngine
from forecasting import model_persistence as mp


def _price_series(n: int, seed: int = 0, start: float = 100.0) -> pd.Series:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    log_returns = rng.normal(0.0003, 0.015, n)
    prices = start * np.exp(np.cumsum(log_returns))
    return pd.Series(prices, index=dates, name="Close")


def _bars_df(n: int, seed: int = 0) -> pd.DataFrame:
    close = _price_series(n, seed=seed)
    return pd.DataFrame(
        {"Open": close.values, "High": close.values * 1.01,
         "Low": close.values * 0.99, "Close": close.values,
         "Volume": [10000.0] * n},
        index=close.index,
    )


@pytest.fixture
def engine():
    return ForecastingEngine()


@pytest.fixture(autouse=True)
def _isolated_models_dir(tmp_path, monkeypatch):
    """Redirect the artifact cache to a per-test tmp dir so tests never touch
    (or are polluted by) the real ml/models/forecast_cache/ directory."""
    monkeypatch.setattr(mp, "MODELS_DIR", tmp_path / "forecast_cache")


# ============================================================================
# forecasting/model_persistence.py — pure helpers
# ============================================================================

class TestArtifactPath:
    def test_fixed_filename_per_ticker(self):
        p1 = mp.artifact_path("cnn_lstm", "AAPL", ".keras")
        p2 = mp.artifact_path("cnn_lstm", "AAPL", ".keras")
        assert p1 == p2  # same ticker -> same path (overwrite-in-place, not dated)
        assert p1.name == "cnn_lstm_AAPL.keras"

    def test_different_tickers_different_paths(self):
        p1 = mp.artifact_path("prophet", "AAPL", ".pkl")
        p2 = mp.artifact_path("prophet", "MSFT", ".pkl")
        assert p1 != p2

    def test_creates_parent_directory(self):
        p = mp.artifact_path("cnn_lstm", "AAPL", ".keras")
        assert p.parent.exists()

    def test_sanitizes_unsafe_ticker_characters(self):
        p = mp.artifact_path("prophet", "../../etc/passwd", ".pkl")
        assert "/" not in p.name
        assert ".." not in p.name
        # Must still resolve inside MODELS_DIR, never escape it.
        assert p.parent == mp.MODELS_DIR


class TestIsFresh:
    def test_missing_file_is_not_fresh(self, tmp_path):
        assert mp.is_fresh(tmp_path / "nope.pkl", retrain_days=7) is False

    def test_just_written_file_is_fresh(self, tmp_path):
        f = tmp_path / "a.pkl"
        f.write_bytes(b"x")
        assert mp.is_fresh(f, retrain_days=7) is True

    def test_stale_file_beyond_retrain_window(self, tmp_path, monkeypatch):
        f = tmp_path / "a.pkl"
        f.write_bytes(b"x")
        # Jump the wall clock 8 days forward relative to the file's mtime.
        future = time.time() + 8 * 86400
        monkeypatch.setattr(mp.time, "time", lambda: future)
        assert mp.is_fresh(f, retrain_days=7) is False

    def test_boundary_just_inside_window_is_fresh(self, tmp_path, monkeypatch):
        f = tmp_path / "a.pkl"
        f.write_bytes(b"x")
        future = time.time() + 6 * 86400  # inside a 7-day window
        monkeypatch.setattr(mp.time, "time", lambda: future)
        assert mp.is_fresh(f, retrain_days=7) is True

    def test_stat_failure_degrades_to_false_never_raises(self, tmp_path, monkeypatch):
        f = tmp_path / "a.pkl"
        f.write_bytes(b"x")

        def _raise(*a, **k):
            raise OSError("simulated stat failure")

        monkeypatch.setattr(Path, "stat", _raise)
        assert mp.is_fresh(f, retrain_days=7) is False


class TestTouch:
    def test_touch_never_raises_on_bad_path(self):
        # A path whose parent doesn't exist -> OSError swallowed, not raised.
        mp.touch(Path("/nonexistent_dir_xyz/model.pkl"))


# ============================================================================
# run_prophet_forecast — persistence wiring (fakeable without the real
# `prophet` package)
# ============================================================================

class _FakeProphetModel:
    """Minimal picklable stand-in for a fitted Prophet model."""

    def __init__(self, daily_seasonality=False, weekly_seasonality=True,
                 yearly_seasonality=False):
        self.fitted = False
        self.fit_calls = 0

    def fit(self, df):
        self.fitted = True
        self.fit_calls += 1
        self._last_close = float(df["y"].iloc[-1])
        return self

    def make_future_dataframe(self, periods):
        return pd.DataFrame({"ds": pd.date_range("2023-01-01", periods=periods + 1)})

    def predict(self, future):
        # Deterministic "forecast": last observed close, flat.
        n = len(future)
        val = getattr(self, "_last_close", 100.0)
        return pd.DataFrame({
            "yhat": [val] * n,
            "yhat_lower": [val * 0.95] * n,
            "yhat_upper": [val * 1.05] * n,
        })


@pytest.fixture(autouse=True)
def _fake_prophet(monkeypatch):
    monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
    monkeypatch.setattr(forecasting_engine, "Prophet", _FakeProphetModel, raising=False)


class TestProphetPersistenceDisabledByDefault:
    def test_default_settings_never_touches_artifact_cache(self, engine):
        series = _price_series(60)
        engine.run_prophet_forecast(series, days_forward=30, ticker="AAPL")
        assert not mp.MODELS_DIR.exists() or not list(mp.MODELS_DIR.glob("prophet_*"))

    def test_no_ticker_never_touches_artifact_cache(self, engine, monkeypatch):
        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True):
            series = _price_series(60)
            engine.run_prophet_forecast(series, days_forward=30, ticker=None)
        assert not mp.MODELS_DIR.exists() or not list(mp.MODELS_DIR.glob("prophet_*"))


class TestProphetPersistenceEnabled:
    def test_cache_miss_fits_and_persists_artifact(self, engine):
        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_RETRAIN_DAYS", 7):
            series = _price_series(60)
            yhat, lo, hi = engine.run_prophet_forecast(series, days_forward=30, ticker="AAPL")
        artifact = mp.artifact_path("prophet", "AAPL", ".pkl")
        assert artifact.exists()
        with open(artifact, "rb") as f:
            saved = pickle.load(f)
        assert isinstance(saved, _FakeProphetModel)
        assert saved.fit_calls == 1
        assert yhat == pytest.approx(float(series.iloc[-1]))

    def test_cache_hit_skips_fit_and_uses_cached_model(self, engine):
        artifact = mp.artifact_path("prophet", "AAPL", ".pkl")
        cached = _FakeProphetModel()
        cached.fit(pd.DataFrame({"y": [42.0]}))  # pre-fit once, out of band
        cached.fit_calls = 0  # reset counter to prove the NEW call doesn't refit
        with open(artifact, "wb") as f:
            pickle.dump(cached, f)
        mp.touch(artifact)

        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_RETRAIN_DAYS", 7):
            series = _price_series(60)  # different data than the cached fit
            yhat, lo, hi = engine.run_prophet_forecast(series, days_forward=30, ticker="AAPL")

        # The cached model's fit() was never called again -- inference only.
        with open(artifact, "rb") as f:
            reloaded = pickle.load(f)
        assert reloaded.fit_calls == 0
        # Result reflects the CACHED model's state (42.0), not a fresh fit on `series`.
        assert yhat == pytest.approx(42.0)

    def test_stale_cache_triggers_refit(self, engine, monkeypatch):
        artifact = mp.artifact_path("prophet", "AAPL", ".pkl")
        cached = _FakeProphetModel()
        cached.fit(pd.DataFrame({"y": [42.0]}))
        with open(artifact, "wb") as f:
            pickle.dump(cached, f)
        mp.touch(artifact)

        future = time.time() + 8 * 86400
        monkeypatch.setattr(mp.time, "time", lambda: future)

        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_RETRAIN_DAYS", 7):
            series = _price_series(60)
            yhat, lo, hi = engine.run_prophet_forecast(series, days_forward=30, ticker="AAPL")

        # Stale -> refit against `series`, not the cached 42.0 value.
        assert yhat == pytest.approx(float(series.iloc[-1]))

    def test_corrupt_cache_degrades_to_refit_never_raises(self, engine):
        artifact = mp.artifact_path("prophet", "AAPL", ".pkl")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"not a valid pickle")
        mp.touch(artifact)

        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_RETRAIN_DAYS", 7):
            series = _price_series(60)
            yhat, lo, hi = engine.run_prophet_forecast(series, days_forward=30, ticker="AAPL")
        assert yhat == pytest.approx(float(series.iloc[-1]))


# ============================================================================
# run_cnn_lstm_forecast — persistence wiring
# ============================================================================

class _FakeKerasModel:
    def __init__(self, n_horizons: int, out_value: float = 55.0):
        self.output_shape = (None, n_horizons)
        self._out_value = out_value
        self.predict_calls = 0

    def predict(self, X, verbose=0):
        self.predict_calls += 1
        n_horizons = self.output_shape[-1]
        return np.array([[self._out_value] * n_horizons])


class _FakeTFKerasModelsNamespace:
    def __init__(self, model_to_return):
        self._model = model_to_return
        self.load_calls = 0

    def load_model(self, path):
        self.load_calls += 1
        return self._model


class _FakeTFKerasNamespace:
    def __init__(self, models_ns):
        self.models = models_ns


class _FakeTF:
    def __init__(self, models_ns):
        self.keras = _FakeTFKerasNamespace(models_ns)


class TestCnnLstmPersistenceDisabledByDefault:
    def test_default_settings_never_touches_artifact_cache(self, engine):
        df = _bars_df(100)
        engine.run_cnn_lstm_forecast(df, horizons=(10, 30, 60, 90), ticker="AAPL")
        assert not mp.MODELS_DIR.exists() or not list(mp.MODELS_DIR.glob("cnn_lstm_*"))


class TestCnnLstmTensorFlowUnavailableSafety:
    def test_persistence_enabled_but_tf_absent_returns_zero_result(self, engine, monkeypatch):
        """TENSORFLOW_AVAILABLE is checked BEFORE any persistence logic runs --
        enabling the flag with no TF installed must be byte-identical to the
        pre-PR-C contract (never raises, never fabricates a value)."""
        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)
        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True):
            df = _bars_df(100)
            result = engine.run_cnn_lstm_forecast(
                df, horizons=(10, 30, 60, 90), ticker="AAPL"
            )
        assert result == {10: 0.0, 30: 0.0, 60: 0.0, 90: 0.0}


class TestCnnLstmCacheHit:
    def test_cache_hit_skips_training_and_uses_loaded_model(self, engine, monkeypatch):
        horizons = (10, 30, 60, 90)
        fake_model = _FakeKerasModel(n_horizons=len(horizons), out_value=0.5)
        fake_models_ns = _FakeTFKerasModelsNamespace(fake_model)
        fake_tf = _FakeTF(fake_models_ns)

        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", True)
        monkeypatch.setattr(forecasting_engine, "tf", fake_tf, raising=False)
        # Prove the expensive fresh-train path is never entered: Sequential
        # raising makes any fallthrough into the training try-block fail loudly.
        monkeypatch.setattr(
            forecasting_engine, "Sequential",
            mock.Mock(side_effect=AssertionError("fresh training path must not run")),
            raising=False,
        )

        df = _bars_df(120)
        # scaler_X/scaler_y must be real (fitted) MinMaxScalers so .transform()/
        # .inverse_transform() work -- fit them on this engine's own feature build
        # so column counts line up exactly with what run_cnn_lstm_forecast expects.
        df_features = engine.build_lstm_features(df)
        feature_cols = engine.LSTM_FEATURE_COLS
        from sklearn.preprocessing import MinMaxScaler
        scaler_X = MinMaxScaler().fit(df_features[feature_cols].values)
        scaler_y = MinMaxScaler().fit(df_features[["Close"]].values)

        keras_path = mp.artifact_path("cnn_lstm", "AAPL", ".keras")
        scaler_x_path = mp.artifact_path("cnn_lstm", "AAPL", "_scaler_x.pkl")
        scaler_y_path = mp.artifact_path("cnn_lstm", "AAPL", "_scaler_y.pkl")
        keras_path.write_bytes(b"stub")  # load_model() is faked, contents irrelevant
        with open(scaler_x_path, "wb") as f:
            pickle.dump(scaler_X, f)
        with open(scaler_y_path, "wb") as f:
            pickle.dump(scaler_y, f)
        for p in (keras_path, scaler_x_path, scaler_y_path):
            mp.touch(p)

        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_RETRAIN_DAYS", 7):
            result = engine.run_cnn_lstm_forecast(df, horizons=horizons, ticker="AAPL")

        assert fake_models_ns.load_calls == 1
        assert fake_model.predict_calls == 1
        assert set(result.keys()) == set(horizons)
        # Every horizon comes from the same constant-output fake model.
        vals = list(result.values())
        assert all(v == pytest.approx(vals[0]) for v in vals)

    def test_single_horizon_path_never_persists(self, engine):
        """days_forward is explicitly out of scope for persistence (see
        docstring) -- must behave exactly as if persistence were disabled."""
        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True):
            df = _bars_df(100)
            engine.run_cnn_lstm_forecast(df, days_forward=30, ticker="AAPL")
        assert not mp.MODELS_DIR.exists() or not list(mp.MODELS_DIR.glob("cnn_lstm_*"))


# ============================================================================
# generate_forecast — ticker threading
# ============================================================================

class TestGenerateForecastTickerThreading:
    def test_unlabeled_row_passes_none_ticker(self, engine, monkeypatch):
        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", True)
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
        captured = {}

        def _fake_lstm(history_df, horizons=(10, 30, 60, 90), days_forward=None, ticker=None):
            captured["cnn_lstm_ticker"] = ticker
            return {h: 0.0 for h in horizons}

        def _fake_prophet(history_series, days_forward, ticker=None):
            captured["prophet_ticker"] = ticker
            last = float(history_series.iloc[-1])
            return last, last, last

        monkeypatch.setattr(engine, "run_cnn_lstm_forecast", _fake_lstm)
        monkeypatch.setattr(engine, "run_prophet_forecast", _fake_prophet)

        series = _price_series(80)
        row = pd.Series({"sector": "Technology"})  # no Symbol/Ticker key
        engine.generate_forecast(row, current_price=100.0, history_series=series)

        assert captured["cnn_lstm_ticker"] is None
        assert captured["prophet_ticker"] is None

    def test_labeled_row_passes_real_ticker(self, engine, monkeypatch):
        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", True)
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
        captured = {}

        def _fake_lstm(history_df, horizons=(10, 30, 60, 90), days_forward=None, ticker=None):
            captured["cnn_lstm_ticker"] = ticker
            return {h: 0.0 for h in horizons}

        def _fake_prophet(history_series, days_forward, ticker=None):
            captured["prophet_ticker"] = ticker
            last = float(history_series.iloc[-1])
            return last, last, last

        monkeypatch.setattr(engine, "run_cnn_lstm_forecast", _fake_lstm)
        monkeypatch.setattr(engine, "run_prophet_forecast", _fake_prophet)

        series = _price_series(80)
        row = pd.Series({"sector": "Technology", "Symbol": "aapl"})
        engine.generate_forecast(row, current_price=100.0, history_series=series)

        assert captured["cnn_lstm_ticker"] == "AAPL"
        assert captured["prophet_ticker"] == "AAPL"


# ============================================================================
# settings defaults
# ============================================================================

class TestSettingsDefaults:
    def test_persistence_disabled_by_default(self):
        from settings import Settings
        assert Settings().FORECAST_MODEL_PERSISTENCE_ENABLED is False

    def test_retrain_days_default_is_seven(self):
        from settings import Settings
        assert Settings().FORECAST_MODEL_RETRAIN_DAYS == 7
