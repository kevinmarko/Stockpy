from __future__ import annotations
import sys
import types
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

mock_tf = types.ModuleType('tensorflow')
mock_models = types.ModuleType('tensorflow.keras.models')
mock_layers = types.ModuleType('tensorflow.keras.layers')
mock_callbacks = types.ModuleType('tensorflow.keras.callbacks')

mock_tf.keras = types.ModuleType('keras')
mock_tf.keras.models = mock_models
mock_tf.keras.layers = mock_layers
mock_tf.keras.callbacks = mock_callbacks

mock_sequential = MagicMock()
mock_model = MagicMock()
mock_models.Sequential = mock_sequential
mock_models.Model = mock_model
mock_models.load_model = MagicMock()

mock_layers.Conv1D = MagicMock()
mock_layers.LSTM = MagicMock()
mock_layers.Dense = MagicMock()
mock_layers.MaxPooling1D = MagicMock()

mha_instance = MagicMock()
mha_instance.return_value = (MagicMock(), MagicMock())
mock_layers.MultiHeadAttention = MagicMock(return_value=mha_instance)

mock_layers.GlobalAveragePooling1D = MagicMock()
mock_layers.LayerNormalization = MagicMock()
mock_layers.Input = MagicMock()

mock_callbacks.EarlyStopping = MagicMock()
mock_tf.random = MagicMock()

sys.modules['tensorflow'] = mock_tf
sys.modules['tensorflow.keras'] = mock_tf.keras
sys.modules['tensorflow.keras.models'] = mock_models
sys.modules['tensorflow.keras.layers'] = mock_layers
sys.modules['tensorflow.keras.callbacks'] = mock_callbacks

import cnn_lstm_worker


def _rebind_mocks_onto_cnn_lstm_worker() -> None:
    """Bind this file's mocks directly onto the cnn_lstm_worker module,
    mirroring tests/test_cnn_lstm_worker.py's own established rationale: if
    some other test module (e.g. tests/test_cnn_lstm_worker.py, which
    alphabetically collects first and does this same direct rebind for its
    own names) already imported cnn_lstm_worker first, this file's
    sys.modules mocking above is a no-op for cnn_lstm_worker's own
    namespace -- `import cnn_lstm_worker` returns the already-cached module
    object, whose top-level `from tensorflow.keras.layers import ...`
    already ran against WHATEVER mock was in sys.modules at that earlier
    point, not this file's own carefully-configured MultiHeadAttention
    (which, unlike the other test file's bare MagicMock(), returns a real
    2-tuple via mha_instance).

    Called BOTH at module-collection time (below) AND from this file's own
    autouse fixture, immediately before every test -- a single
    collection-time call is not enough, since whichever of these two test
    files' module-level code runs LAST at collection time wins the shared
    cnn_lstm_worker module's state for every test that follows, in either
    file, regardless of which file's tests execute first. Re-applying in
    the fixture makes both files' tests pass regardless of collection
    order.
    """
    cnn_lstm_worker.TENSORFLOW_AVAILABLE = True
    cnn_lstm_worker.tf = mock_tf
    cnn_lstm_worker.Sequential = mock_models.Sequential
    cnn_lstm_worker.Model = mock_models.Model
    cnn_lstm_worker.load_model = mock_models.load_model
    cnn_lstm_worker.Conv1D = mock_layers.Conv1D
    cnn_lstm_worker.LSTM = mock_layers.LSTM
    cnn_lstm_worker.Dense = mock_layers.Dense
    cnn_lstm_worker.MaxPooling1D = mock_layers.MaxPooling1D
    cnn_lstm_worker.MultiHeadAttention = mock_layers.MultiHeadAttention
    cnn_lstm_worker.GlobalAveragePooling1D = mock_layers.GlobalAveragePooling1D
    cnn_lstm_worker.Input = mock_layers.Input
    cnn_lstm_worker.LayerNormalization = mock_layers.LayerNormalization
    cnn_lstm_worker.EarlyStopping = mock_callbacks.EarlyStopping


_rebind_mocks_onto_cnn_lstm_worker()


class TestFitPredictLstmAttention:
    @pytest.fixture(autouse=True)
    def reset_mocks(self):
        _rebind_mocks_onto_cnn_lstm_worker()
        mock_model.reset_mock()
        mock_model.return_value.fit.reset_mock()
        mock_model.return_value.predict.reset_mock()
        mock_model.return_value.get_weights.reset_mock()
        mock_layers.Input.reset_mock()
        mock_layers.LSTM.reset_mock()
        mock_layers.MultiHeadAttention.reset_mock()
        mock_layers.GlobalAveragePooling1D.reset_mock()
        mock_layers.Dense.reset_mock()
        
    def _seq(self, n_samples=40, seq_len=10, n_features=15):
        X_seq = np.random.rand(n_samples, seq_len, n_features)
        Y_seq = np.random.rand(n_samples)
        predict_X_seq = np.random.rand(5, seq_len, n_features)
        return X_seq, Y_seq, predict_X_seq

    def test_fit_mode_trains_and_returns_weights(self):
        X_seq, Y_seq, predict_X_seq = self._seq()
        mock_model.return_value.predict.return_value = (np.array([[0.1], [0.2], [0.3], [0.4], [0.5]]), np.ones((5, 2, 10, 10)))
        mock_model.return_value.get_weights.return_value = [np.array([1.0]), np.array([2.0])]

        result = cnn_lstm_worker.fit_predict_lstm_attention(X_seq, Y_seq, predict_X_seq, hidden_dim=8, num_heads=2)

        assert result["predictions"] == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])
        assert result["weights"] == [[1.0], [2.0]]
        assert "attention_weights" in result
        mock_model.return_value.fit.assert_called_once()
        mock_model.return_value.set_weights.assert_not_called()

    def test_fit_mode_requires_y_seq(self):
        X_seq, _, predict_X_seq = self._seq()
        with pytest.raises(ValueError, match="Y_seq"):
            cnn_lstm_worker.fit_predict_lstm_attention(X_seq, None, predict_X_seq, hidden_dim=8, num_heads=2)

    def test_inference_only_mode_skips_training(self):
        _, _, predict_X_seq = self._seq()
        stored_weights = [[1.0], [2.0]]
        mock_model.return_value.predict.return_value = (np.zeros((5, 1)), np.ones((5, 2, 10, 10)))
        mock_model.return_value.get_weights.return_value = [np.array([1.0]), np.array([2.0])]

        result = cnn_lstm_worker.fit_predict_lstm_attention(
            np.empty((0,10,15)), None, predict_X_seq, hidden_dim=8, num_heads=2, weights=stored_weights
        )

        mock_model.return_value.fit.assert_not_called()
        mock_model.return_value.set_weights.assert_called_once()
        set_call_args = mock_model.return_value.set_weights.call_args[0][0]
        assert [w.tolist() for w in set_call_args] == stored_weights
        assert result["weights"] == stored_weights

    def test_architecture_matches_shape(self):
        X_seq, Y_seq, predict_X_seq = self._seq(seq_len=15, n_features=15)
        mock_model.return_value.get_weights.return_value = []
        cnn_lstm_worker.fit_predict_lstm_attention(X_seq, Y_seq, predict_X_seq, hidden_dim=12, num_heads=4)

        input_call = mock_layers.Input.call_args
        assert input_call.kwargs["shape"] == (15, 15)
        lstm_call = mock_layers.LSTM.call_args
        assert lstm_call.kwargs["units"] == 12
        assert lstm_call.kwargs["return_sequences"] is True
        attention_call = mock_layers.MultiHeadAttention.call_args
        assert attention_call.kwargs["num_heads"] == 4
        assert attention_call.kwargs["key_dim"] == 12
        dense_call = mock_layers.Dense.call_args
        assert dense_call.kwargs["units"] == 1

class TestLoadPredictLstmAttention:
    @pytest.fixture(autouse=True)
    def reset_mocks(self):
        # Same rationale as TestFitPredictLstmAttention.reset_mocks above --
        # re-applied per class since pytest autouse fixtures are class-scoped,
        # not file-scoped, and this class has its own tests that reach
        # cnn_lstm_worker.load_model/Model.
        _rebind_mocks_onto_cnn_lstm_worker()
        mock_models.load_model.reset_mock()

    def test_returns_pred_scaled_from_loaded_model(self):
        fake_model = MagicMock()
        # A real multi-output Functional model (predictions + attention_scores,
        # predictions at output index 0) reports `.output_shape` as a LIST of
        # per-output shape tuples, not a single tuple -- this must be a list
        # of two tuples to actually exercise the real failure mode this test
        # covers (see cnn_lstm_worker.load_predict_lstm_attention).
        fake_model.output_shape = [(None, 1), (None, 2, 10, 10)]
        fake_model.predict.return_value = np.array([[1.0, 2.0, 3.0, 4.0]])
        mock_models.load_model.return_value = fake_model

        last_window = np.random.rand(1, 10, 15)
        result = cnn_lstm_worker.load_predict_lstm_attention("some/path.keras", last_window, num_horizons=1)

        assert result["pred_scaled"] == pytest.approx([1.0, 2.0, 3.0, 4.0])
        mock_models.load_model.assert_called_once_with("some/path.keras")

    def test_raises_on_horizon_count_mismatch(self):
        fake_model = MagicMock()
        fake_model.output_shape = [(None, 1), (None, 2, 10, 10)]
        mock_models.load_model.return_value = fake_model

        last_window = np.random.rand(1, 10, 15)
        with pytest.raises(ValueError, match="horizon count mismatch"):
            cnn_lstm_worker.load_predict_lstm_attention("some/path.keras", last_window, num_horizons=4)
