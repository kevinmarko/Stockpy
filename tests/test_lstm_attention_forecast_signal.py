import pandas as pd
import numpy as np
from signals.lstm_attention_forecast import LstmAttentionForecastSignal

def test_lstm_attention_forecast_signal_degrades_on_missing():
    """Test that missing forecast degrades gracefully."""
    signal = LstmAttentionForecastSignal()
    
    # Missing Forecast
    df = pd.DataFrame({
        "Close": [100.0, 101.0, 102.0],
    }, index=pd.date_range("2026-01-01", periods=3))
    
    out = signal.compute_vectorized(df, None)
    
    assert (out["score"] == 0.0).all()
    assert (out["confidence"] == 0.0).all()
    assert (out["explanation"] == "WARNING: Insufficient ASVI/LSTM history").all()

def test_lstm_attention_forecast_signal_scoring():
    """Test that scoring maps properly."""
    signal = LstmAttentionForecastSignal()
    
    df = pd.DataFrame({
        "Close": [100.0, 101.0, 102.0, 103.0],
        "Google_Trends_LSTM_Forecast": [0.015, 0.005, -0.01, 0.0]
    }, index=pd.date_range("2026-01-01", periods=4))
    
    out = signal.compute_vectorized(df, None)
    
    # +10pts for 1.5% (idx 0)
    assert out["score"].iloc[0] == 1.0
    assert out["confidence"].iloc[0] == 1.0
    assert "+10pts" in out["explanation"].iloc[0]
    
    # +5pts for 0.5% (idx 1)
    assert out["score"].iloc[1] == 0.5
    assert "+5pts" in out["explanation"].iloc[1]
    
    # -10pts for -1.0% (idx 2)
    assert out["score"].iloc[2] == -1.0
    assert "-10pts" in out["explanation"].iloc[2]

    # -10pts for 0.0% (idx 3)
    assert out["score"].iloc[3] == -1.0
    assert "-10pts" in out["explanation"].iloc[3]

def test_lstm_attention_forecast_signal_no_lookahead():
    """Test for zero lookahead bias."""
    from tests.lookahead_check import verify_no_lookahead
    
    df = pd.DataFrame({
        "Close": np.random.randn(100) + 100,
        "Google_Trends_LSTM_Forecast": np.random.randn(100) * 0.02
    }, index=pd.date_range("2026-01-01", periods=100))
    
    signal = LstmAttentionForecastSignal()
    
    def func(data, t):
        out = signal.compute_vectorized(data, None)
        return out["score"].iloc[t]
        
    assert verify_no_lookahead(func, df, 50)
