import sys
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd

import types

# Create mock modules for TensorFlow before importing any engine that imports tf
mock_tf = types.ModuleType('tensorflow')
mock_keras = types.ModuleType('tensorflow.keras')
mock_models = types.ModuleType('tensorflow.keras.models')
mock_layers = types.ModuleType('tensorflow.keras.layers')
mock_callbacks = types.ModuleType('tensorflow.keras.callbacks')

# Wire them together just in case
mock_tf.keras = mock_keras
mock_keras.models = mock_models
mock_keras.layers = mock_layers
mock_keras.callbacks = mock_callbacks

# Create the specific mock class for Sequential
mock_sequential = MagicMock()
# By default, predict should return a 2D array of shape (1, 4) with dummy scaled values
mock_sequential.return_value.predict.return_value = np.ones((1, 4)) * 0.5
mock_models.Sequential = mock_sequential

# Add other required imports
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

import forecasting_engine
# Force TENSORFLOW_AVAILABLE to be True for testing
forecasting_engine.TENSORFLOW_AVAILABLE = True

import pytest
from sklearn.preprocessing import MinMaxScaler
from forecasting_engine import ForecastingEngine

@pytest.fixture
def sine_wave_data():
    """Generates a deterministic sine-wave series (300 days)."""
    dates = pd.date_range(end="2026-06-24", periods=300)
    # Sine wave for Close
    t = np.linspace(0, 6 * np.pi, 300)
    close = 100.0 + 10.0 * np.sin(t)
    
    df = pd.DataFrame({
        "Open": close - 0.5,
        "High": close + 0.5,
        "Low": close - 0.5,
        "Close": close,
        "Volume": [1000.0] * 300
    }, index=dates)
    return df

def test_forecasting_scaler_fit_on_train_only(sine_wave_data):
    """
    Asserts:
    1. MinMaxScaler.fit is called strictly on the training slice (excluding last lookback + max_horizon rows).
    2. The forecast horizon shape is correct.
    """
    engine = ForecastingEngine()
    horizons = (10, 30, 60, 90)
    max_h = max(horizons)
    lookback = engine.LSTM_LOOKBACK
    n_reserve = lookback + max_h # 60 + 90 = 150
    
    # Pre-calculate features to know the expected length after dropna
    df_features = engine.build_lstm_features(sine_wave_data)
    N_features = len(df_features)
    expected_train_len = N_features - n_reserve
    
    fit_call_args = []
    
    # Store the original fit method to call it so scaling doesn't break
    original_fit = MinMaxScaler.fit
    
    def mock_fit(self, X, y=None):
        fit_call_args.append(X)
        return original_fit(self, X, y)
        
    with patch.object(MinMaxScaler, 'fit', autospec=True, side_effect=mock_fit):
        forecasts = engine.run_cnn_lstm_forecast(sine_wave_data, horizons=horizons)
        
        # Verify TensorFlow forecast returned result or fallback
        assert isinstance(forecasts, dict)
        assert len(forecasts) == len(horizons)
        for h in horizons:
            assert h in forecasts
            
        # Verify the MinMaxScaler fit arguments
        # MinMaxScaler is fit on feature matrix X (columns: feature_cols) and Close price y.
        # We expect fit to be called at least twice (once for X, once for y)
        assert len(fit_call_args) >= 2, "MinMaxScaler.fit was not called as expected."
        
        for arg in fit_call_args:
            # The length of the passed training slice must be exactly expected_train_len
            assert len(arg) == expected_train_len, (
                f"MinMaxScaler.fit was called on size {len(arg)} but training slice expected {expected_train_len}."
            )


# =============================================================================
# build_lstm_features() -- direct perturbation coverage
# =============================================================================
# build_lstm_features()'s own docstring claims its causality "is verified by
# tests/test_indicators_lookahead.py via the perturbation detector" -- but
# that file only exercises the underlying pandas_ta primitives directly
# (ta.rsi/ta.macd/ta.atr/ta.aroon), never this assembled function itself
# (its specific sanitize_ohlcv preprocessing, fillna/ffill warm-up handling,
# and Volatility_20_Annual rolling-std computation were never directly
# proven causal). This closes that gap using the same
# tests.lookahead_check.verify_no_lookahead harness the docstring alludes to.

from tests.lookahead_check import verify_no_lookahead


class TestBuildLstmFeaturesLookahead:
    def test_aroon_oscillator_feature(self, sine_wave_data):
        engine = ForecastingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            features = engine.build_lstm_features(sliced)
            if features.empty:
                return float("nan")
            return features["Aroon_Oscillator"].iloc[-1]

        assert verify_no_lookahead(calc, sine_wave_data, t=100)

    def test_coppock_curve_feature(self, sine_wave_data):
        engine = ForecastingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            features = engine.build_lstm_features(sliced)
            if features.empty:
                return float("nan")
            return features["Coppock_Curve"].iloc[-1]

        assert verify_no_lookahead(calc, sine_wave_data, t=100)

    def test_chandelier_long_feature(self, sine_wave_data):
        engine = ForecastingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            features = engine.build_lstm_features(sliced)
            if features.empty:
                return float("nan")
            return features["Chandelier_Long"].iloc[-1]

        assert verify_no_lookahead(calc, sine_wave_data, t=100)

    def test_volatility_20_annual_feature(self, sine_wave_data):
        """The 20-day rolling-std volatility feature is the most likely spot
        for an accidental centered window (pandas .rolling() defaults to a
        trailing window, but a typo'd `center=True` would silently leak)."""
        engine = ForecastingEngine()

        def calc(df, t):
            sliced = df.iloc[:t + 1]
            features = engine.build_lstm_features(sliced)
            if features.empty:
                return float("nan")
            return features["Volatility_20_Annual"].iloc[-1]

        assert verify_no_lookahead(calc, sine_wave_data, t=100)


# =============================================================================
# make_direct_multistep_windows() -- deterministic index-alignment proof
# =============================================================================
# This static method takes plain numpy arrays (no time index), so the
# perturb-after-t harness doesn't apply directly. Instead, this uses
# index-valued synthetic arrays (scaled_X[i] == i, scaled_close[i] == i) so
# every window's exact row range and every label's exact source index can be
# asserted precisely -- a stronger, more direct proof than a perturbation
# test would give for pure index arithmetic.

class TestMakeDirectMultistepWindowsIndexing:
    def test_windows_never_include_rows_at_or_after_their_own_label(self):
        lookback = 5
        horizons = [1, 3, 5]
        n_features = 2
        n_rows = 30

        # scaled_X[i, :] == i (broadcast across both feature columns);
        # scaled_close[i, 0] == i. Content IS the row index, so any leaked
        # future row is immediately visible in the returned values.
        scaled_X = np.tile(np.arange(n_rows).reshape(-1, 1), (1, n_features)).astype(float)
        scaled_close = np.arange(n_rows).reshape(-1, 1).astype(float)

        X_seq, Y_seq = ForecastingEngine.make_direct_multistep_windows(
            scaled_X, scaled_close, lookback=lookback, horizons=horizons
        )

        max_h = max(horizons)
        expected_n_samples = n_rows - lookback - max_h + 1
        assert X_seq.shape == (expected_n_samples, lookback, n_features)
        assert Y_seq.shape == (expected_n_samples, len(horizons))

        for k in range(expected_n_samples):
            end = lookback + k
            last = end - 1
            # Window k's rows must be exactly [end-lookback, end) -- every
            # row strictly BEFORE the window's own "last" row, never at or
            # after any of this sample's label indices (last+h for h>=1).
            window_rows = X_seq[k, :, 0]
            assert list(window_rows) == list(range(end - lookback, end))
            assert window_rows.max() == last
            # Labels are exactly close[last+h] for each horizon -- strictly
            # AFTER the window's last row, confirming direction of the
            # prediction target relative to the window (not leaked INTO it).
            expected_labels = [last + h for h in horizons]
            assert list(Y_seq[k]) == expected_labels
            for label_idx in expected_labels:
                assert label_idx > last, (
                    "A label index fell at or before the window's own last "
                    "row -- this would mean the window could see its own "
                    "label's timestamp, a genuine lookahead bug."
                )

    def test_perturbing_rows_beyond_a_windows_reach_never_changes_that_window(self):
        """Direct perturbation-style proof: two windows/labels sharing the
        same early samples must be byte-identical whether or not far-future
        rows exist at all."""
        lookback = 4
        horizons = [2]
        n_features = 1

        long_X = np.arange(40).reshape(-1, 1).astype(float)
        long_close = np.arange(40).reshape(-1, 1).astype(float)
        short_X = long_X[:20].copy()
        short_close = long_close[:20].copy()

        X_long, Y_long = ForecastingEngine.make_direct_multistep_windows(
            long_X, long_close, lookback=lookback, horizons=horizons
        )
        X_short, Y_short = ForecastingEngine.make_direct_multistep_windows(
            short_X, short_close, lookback=lookback, horizons=horizons
        )

        # Every window/label pair computable from the short array must be
        # identical in the long array -- truncating (or extending) the
        # series far in the future must never retroactively change an
        # earlier window.
        n_shared = X_short.shape[0]
        assert n_shared > 0
        np.testing.assert_array_equal(X_long[:n_shared], X_short)
        np.testing.assert_array_equal(Y_long[:n_shared], Y_short)
