"""
tests/test_cnn_lstm_isolation_dispatch.py
==========================================
Verifies ForecastingEngine.run_cnn_lstm_forecast's dispatch between the
legacy in-process path and the CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED
subprocess path (issue #381, docs/known_issues/cnn_lstm_tf_deadlock.md).

Uses the same TENSORFLOW_AVAILABLE/Sequential monkeypatch technique as
tests/test_forecast_model_persistence.py (collection-order independent,
unlike the sys.modules injection tests/test_forecasting_lookahead.py and
tests/test_cnn_lstm_worker.py use). cnn_lstm_process_pool.run_in_subprocess
is stubbed here, so these tests verify DISPATCH -- which path runs, with
what arguments, how failures propagate -- not the real multiprocessing
mechanics (tests/test_cnn_lstm_process_pool.py) or real TensorFlow model
behavior (tests/test_cnn_lstm_worker.py).
"""

from __future__ import annotations

from unittest import mock

import numpy as np
import pandas as pd
import pytest

import forecasting_engine
from forecasting_engine import ForecastingEngine


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
def _fake_tf_available(monkeypatch):
    """Every test in this file needs TENSORFLOW_AVAILABLE=True to reach
    run_cnn_lstm_forecast's training/dispatch logic at all. Sequential is
    made to raise loudly if ever touched -- the isolated-path tests use that
    to PROVE the legacy in-process path was not entered."""
    monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", True)
    monkeypatch.setattr(
        forecasting_engine, "Sequential",
        mock.Mock(side_effect=AssertionError("legacy in-process path must not run")),
        raising=False,
    )


class TestFreshFitIsolationDispatch:
    def test_isolation_enabled_dispatches_to_run_in_subprocess(self, engine):
        horizons = (10, 30, 60, 90)
        fake_result = {"pred_scaled": [0.5, 0.5, 0.5, 0.5], "saved": False}

        with mock.patch("settings.settings.CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", True), \
             mock.patch("cnn_lstm_process_pool.run_in_subprocess", return_value=fake_result) as mock_run:
            df = _bars_df(340)
            result = engine.run_cnn_lstm_forecast(df, horizons=horizons)

        assert set(result.keys()) == set(horizons)
        mock_run.assert_called_once()
        called_func, called_args = mock_run.call_args[0]
        assert called_func.__name__ == "fit_predict_cnn_lstm"
        X_seq, Y_seq, last_window, num_horizons, save_path = called_args
        assert num_horizons == 4
        assert save_path is None  # persistence not enabled in this test
        assert isinstance(X_seq, np.ndarray) and isinstance(Y_seq, np.ndarray)

    def test_isolation_enabled_and_persistence_enabled_passes_save_path(self, engine, tmp_path, monkeypatch):
        from forecasting import model_persistence as mp
        monkeypatch.setattr(mp, "MODELS_DIR", tmp_path / "forecast_cache")
        fake_result = {"pred_scaled": [0.5, 0.5, 0.5, 0.5], "saved": True}

        with mock.patch("settings.settings.CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("cnn_lstm_process_pool.run_in_subprocess", return_value=fake_result) as mock_run:
            df = _bars_df(340)
            engine.run_cnn_lstm_forecast(df, horizons=(10, 30, 60, 90), ticker="ISOTEST")

        _, called_args = mock_run.call_args[0]
        save_path = called_args[4]
        assert save_path is not None
        assert "ISOTEST" in str(save_path) or "cnn_lstm" in str(save_path)

    def test_subprocess_failure_degrades_to_zero_result(self, engine):
        """Timeout / BrokenProcessPool / a real training exception inside the
        worker must never crash the pipeline -- CONSTRAINT #6. The existing
        outer try/except in run_cnn_lstm_forecast already covers this; this
        test proves the isolation dispatch doesn't bypass it."""
        horizons = (10, 30, 60, 90)
        with mock.patch("settings.settings.CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", True), \
             mock.patch("cnn_lstm_process_pool.run_in_subprocess", side_effect=TimeoutError("stuck")):
            df = _bars_df(340)
            result = engine.run_cnn_lstm_forecast(df, horizons=horizons)

        assert result == {h: 0.0 for h in horizons}

    def test_isolation_disabled_by_default_uses_legacy_in_process_path(self, engine, monkeypatch):
        """Default (no settings override): the legacy path must run to a
        real result, and the isolation pool must never be touched. Both
        directions are proven at once -- if dispatch went the wrong way
        either function's raising side effect would be hit, the outer
        except would swallow it, and the fit-call / result assertions below
        would fail."""
        import sys
        import types

        working_sequential = mock.MagicMock()
        working_sequential.return_value.predict.return_value = np.ones((1, 4)) * 0.5
        monkeypatch.setattr(forecasting_engine, "Sequential", working_sequential, raising=False)
        monkeypatch.setattr(forecasting_engine, "Conv1D", mock.MagicMock(), raising=False)
        monkeypatch.setattr(forecasting_engine, "LSTM", mock.MagicMock(), raising=False)
        monkeypatch.setattr(forecasting_engine, "Dense", mock.MagicMock(), raising=False)
        monkeypatch.setattr(forecasting_engine, "MaxPooling1D", mock.MagicMock(), raising=False)

        # EarlyStopping is imported locally (`from tensorflow.keras.callbacks
        # import EarlyStopping`) inside the legacy branch -- mock the real
        # import target via sys.modules rather than a module-level name, and
        # scope it to this test via monkeypatch so it can't leak into other
        # test files' collection order.
        mock_callbacks = types.ModuleType('tensorflow.keras.callbacks')
        mock_callbacks.EarlyStopping = mock.MagicMock()
        mock_keras = types.ModuleType('tensorflow.keras')
        mock_keras.callbacks = mock_callbacks
        mock_tf = types.ModuleType('tensorflow')
        mock_tf.keras = mock_keras
        monkeypatch.setitem(sys.modules, 'tensorflow', mock_tf)
        monkeypatch.setitem(sys.modules, 'tensorflow.keras', mock_keras)
        monkeypatch.setitem(sys.modules, 'tensorflow.keras.callbacks', mock_callbacks)

        with mock.patch(
            "cnn_lstm_process_pool.run_in_subprocess",
            side_effect=AssertionError("isolation path must not run when disabled"),
        ):
            df = _bars_df(340)
            result = engine.run_cnn_lstm_forecast(df, horizons=(10, 30, 60, 90))

        assert set(result.keys()) == {10, 30, 60, 90}
        working_sequential.return_value.fit.assert_called_once()


class TestCachedModelIsolationDispatch:
    def test_isolation_enabled_dispatches_cached_predict_to_subprocess(self, engine, tmp_path, monkeypatch):
        import pickle
        from sklearn.preprocessing import MinMaxScaler
        from forecasting import model_persistence as mp

        monkeypatch.setattr(mp, "MODELS_DIR", tmp_path / "forecast_cache")

        horizons = (10, 30, 60, 90)
        df = _bars_df(120)
        df_features = engine.build_lstm_features(df)
        feature_cols = engine.LSTM_FEATURE_COLS
        scaler_X = MinMaxScaler().fit(df_features[feature_cols].values)
        scaler_y = MinMaxScaler().fit(df_features[["Close"]].values)

        keras_path = mp.artifact_path("cnn_lstm", "AAPL", ".keras")
        scaler_x_path = mp.artifact_path("cnn_lstm", "AAPL", "_scaler_x.pkl")
        scaler_y_path = mp.artifact_path("cnn_lstm", "AAPL", "_scaler_y.pkl")
        keras_path.write_bytes(b"stub")  # load happens in the (mocked) subprocess
        with open(scaler_x_path, "wb") as f:
            pickle.dump(scaler_X, f)
        with open(scaler_y_path, "wb") as f:
            pickle.dump(scaler_y, f)
        for p in (keras_path, scaler_x_path, scaler_y_path):
            mp.touch(p)

        fake_result = {"pred_scaled": [0.5, 0.5, 0.5, 0.5]}
        with mock.patch("settings.settings.FORECAST_MODEL_PERSISTENCE_ENABLED", True), \
             mock.patch("settings.settings.FORECAST_MODEL_RETRAIN_DAYS", 7), \
             mock.patch("settings.settings.CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", True), \
             mock.patch("cnn_lstm_process_pool.run_in_subprocess", return_value=fake_result) as mock_run:
            result = engine.run_cnn_lstm_forecast(df, horizons=horizons, ticker="AAPL")

        assert set(result.keys()) == set(horizons)
        mock_run.assert_called_once()
        called_func = mock_run.call_args[0][0]
        assert called_func.__name__ == "load_predict_cnn_lstm"
