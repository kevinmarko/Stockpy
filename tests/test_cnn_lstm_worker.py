"""
tests/test_cnn_lstm_worker.py
==============================
Unit tests for cnn_lstm_worker.py -- the standalone module isolated CNN-LSTM
fit/predict runs in when settings.CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED is
True (see docs/known_issues/cnn_lstm_tf_deadlock.md, issue #381).

Mocks TensorFlow the same way tests/test_forecasting_lookahead.py does
(inject fake modules into sys.modules before import) since real TensorFlow
is an optional heavy dependency. These tests verify the plumbing (model
construction call shape, save/no-save branching, output extraction, error
propagation) -- NOT the real native deadlock fix, which by construction
cannot be exercised without real TensorFlow on the original macOS arm64
environment (see the module's own docstring and the known-issues doc).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Mock TensorFlow before importing cnn_lstm_worker, mirroring
# tests/test_forecasting_lookahead.py's established pattern.
# ---------------------------------------------------------------------------
mock_tf = types.ModuleType('tensorflow')
mock_keras = types.ModuleType('tensorflow.keras')
mock_models = types.ModuleType('tensorflow.keras.models')
mock_layers = types.ModuleType('tensorflow.keras.layers')
mock_callbacks = types.ModuleType('tensorflow.keras.callbacks')

mock_tf.keras = mock_keras
mock_keras.models = mock_models
mock_keras.layers = mock_layers
mock_keras.callbacks = mock_callbacks

mock_sequential = MagicMock()
mock_sequential.return_value.predict.return_value = np.ones((1, 4)) * 0.5
mock_models.Sequential = mock_sequential
mock_models.load_model = MagicMock()

mock_layers.Conv1D = MagicMock()
mock_layers.LSTM = MagicMock()
mock_layers.Dense = MagicMock()
mock_layers.MaxPooling1D = MagicMock()
mock_callbacks.EarlyStopping = MagicMock()

sys.modules['tensorflow'] = mock_tf
sys.modules['tensorflow.keras'] = mock_keras
sys.modules['tensorflow.keras.models'] = mock_models
sys.modules['tensorflow.keras.layers'] = mock_layers
sys.modules['tensorflow.keras.callbacks'] = mock_callbacks

import cnn_lstm_worker  # noqa: E402

# Bind the mocks directly onto the module, mirroring
# test_forecasting_lookahead.py's rationale: if some other test module
# already imported cnn_lstm_worker first (unlikely today, but a real
# collection-order hazard the sibling test file documents), module-level
# names are whatever that first import saw, regardless of what
# sys.modules['tensorflow'] is reassigned to afterwards.
cnn_lstm_worker.TENSORFLOW_AVAILABLE = True
cnn_lstm_worker.Sequential = mock_models.Sequential
cnn_lstm_worker.load_model = mock_models.load_model
cnn_lstm_worker.Conv1D = mock_layers.Conv1D
cnn_lstm_worker.LSTM = mock_layers.LSTM
cnn_lstm_worker.Dense = mock_layers.Dense
cnn_lstm_worker.MaxPooling1D = mock_layers.MaxPooling1D
cnn_lstm_worker.EarlyStopping = mock_callbacks.EarlyStopping


@pytest.fixture(autouse=True)
def _reset_mock_call_state():
    mock_sequential.reset_mock()
    mock_sequential.return_value.predict.return_value = np.ones((1, 4)) * 0.5
    mock_models.load_model.reset_mock()
    yield


def _windows(n_samples=40, lookback=60, n_features=10, n_horizons=4):
    X_seq = np.random.rand(n_samples, lookback, n_features)
    Y_seq = np.random.rand(n_samples, n_horizons)
    last_window = np.random.rand(1, lookback, n_features)
    return X_seq, Y_seq, last_window


class TestFitPredictCnnLstm:
    def test_returns_pred_scaled_from_model_predict(self):
        X_seq, Y_seq, last_window = _windows()
        mock_sequential.return_value.predict.return_value = np.array([[0.1, 0.2, 0.3, 0.4]])

        result = cnn_lstm_worker.fit_predict_cnn_lstm(X_seq, Y_seq, last_window, num_horizons=4)

        assert result["pred_scaled"] == pytest.approx([0.1, 0.2, 0.3, 0.4])
        assert result["saved"] is False
        mock_sequential.return_value.fit.assert_called_once()
        mock_sequential.return_value.predict.assert_called_once()

    def test_saves_model_when_keras_save_path_given(self, tmp_path):
        X_seq, Y_seq, last_window = _windows()
        save_path = str(tmp_path / "model.keras")

        result = cnn_lstm_worker.fit_predict_cnn_lstm(
            X_seq, Y_seq, last_window, num_horizons=4, keras_save_path=save_path
        )

        assert result["saved"] is True
        mock_sequential.return_value.save.assert_called_once_with(save_path)

    def test_does_not_save_when_no_path_given(self):
        X_seq, Y_seq, last_window = _windows()
        result = cnn_lstm_worker.fit_predict_cnn_lstm(X_seq, Y_seq, last_window, num_horizons=4)
        assert result["saved"] is False
        mock_sequential.return_value.save.assert_not_called()

    def test_raises_when_tensorflow_unavailable(self, monkeypatch):
        monkeypatch.setattr(cnn_lstm_worker, "TENSORFLOW_AVAILABLE", False)
        X_seq, Y_seq, last_window = _windows()
        with pytest.raises(RuntimeError, match="tensorflow"):
            cnn_lstm_worker.fit_predict_cnn_lstm(X_seq, Y_seq, last_window, num_horizons=4)

    def test_model_architecture_matches_shape(self):
        """Conv1D input_shape and Dense width must match (time_steps,
        num_features) from X_seq and num_horizons -- a drift here would
        silently produce a differently-shaped model than the in-process
        legacy path in forecasting_engine.py."""
        X_seq, Y_seq, last_window = _windows(lookback=60, n_features=10, n_horizons=4)
        cnn_lstm_worker.fit_predict_cnn_lstm(X_seq, Y_seq, last_window, num_horizons=4)

        layers_arg = mock_sequential.call_args[0][0]
        conv_call = cnn_lstm_worker.Conv1D.call_args
        assert conv_call.kwargs["input_shape"] == (60, 10)
        dense_call = cnn_lstm_worker.Dense.call_args
        assert dense_call.kwargs["units"] == 4
        assert len(layers_arg) == 4


class TestLoadPredictCnnLstm:
    def test_returns_pred_scaled_from_loaded_model(self):
        fake_model = MagicMock()
        fake_model.output_shape = (None, 4)
        fake_model.predict.return_value = np.array([[1.0, 2.0, 3.0, 4.0]])
        mock_models.load_model.return_value = fake_model

        _, _, last_window = _windows()
        result = cnn_lstm_worker.load_predict_cnn_lstm("some/path.keras", last_window, num_horizons=4)

        assert result["pred_scaled"] == pytest.approx([1.0, 2.0, 3.0, 4.0])
        mock_models.load_model.assert_called_once_with("some/path.keras")

    def test_raises_on_horizon_count_mismatch(self):
        fake_model = MagicMock()
        fake_model.output_shape = (None, 3)  # trained for 3 horizons
        mock_models.load_model.return_value = fake_model

        _, _, last_window = _windows()
        with pytest.raises(ValueError, match="horizon count mismatch"):
            cnn_lstm_worker.load_predict_cnn_lstm("some/path.keras", last_window, num_horizons=4)

    def test_raises_when_tensorflow_unavailable(self, monkeypatch):
        monkeypatch.setattr(cnn_lstm_worker, "TENSORFLOW_AVAILABLE", False)
        _, _, last_window = _windows()
        with pytest.raises(RuntimeError, match="tensorflow"):
            cnn_lstm_worker.load_predict_cnn_lstm("some/path.keras", last_window, num_horizons=4)
