import sys
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd

# Create mocks for TensorFlow before importing any engine that imports tf
mock_tf = MagicMock()
mock_sequential = MagicMock()
# By default, predict should return a 2D array of shape (1, 4) with dummy scaled values
mock_sequential.return_value.predict.return_value = np.ones((1, 4)) * 0.5

sys.modules['tensorflow'] = mock_tf
sys.modules['tensorflow.keras.models'] = MagicMock()
sys.modules['tensorflow.keras.models'].Sequential = mock_sequential
sys.modules['tensorflow.keras.layers'] = MagicMock()
sys.modules['tensorflow.keras.callbacks'] = MagicMock()

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
