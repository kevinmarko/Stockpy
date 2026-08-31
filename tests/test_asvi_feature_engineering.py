import pytest
import pandas as pd
import numpy as np
from ml.asvi_feature_engineering import resolve_sector_proxy, build_lstm_attention_tensors

def test_resolve_sector_proxy():
    assert resolve_sector_proxy("Technology") == "XLK"
    assert resolve_sector_proxy("Financials") == "XLF"
    assert resolve_sector_proxy("UnknownSector") == "SPY"
    assert resolve_sector_proxy(None) == "SPY"

def test_build_lstm_attention_tensors():
    # Create fake data
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    df_ohlcv = pd.DataFrame({
        "Open": np.ones(20),
        "High": np.ones(20),
        "Low": np.ones(20),
        "Close": np.arange(1, 21),
        "Volume": np.ones(20),
        "RSI_14": np.ones(20) * 50
    }, index=dates)
    df_sector_ohlcv = df_ohlcv.copy()
    
    df_asvi_symbol = pd.Series(np.ones(20), index=dates)
    df_asvi_sector = pd.Series(np.ones(20), index=dates)
    
    X_seq, Y_seq, valid_indices = build_lstm_attention_tensors(
        "AAPL", df_ohlcv, df_sector_ohlcv, df_asvi_symbol, df_asvi_sector, sequence_length=15
    )
    
    assert len(X_seq) == 5 # 20 periods, length 15 -> 5 windows
    assert X_seq.shape == (5, 15, 15) # 5 windows, 15 time steps, 15 features
    assert len(Y_seq) == 5
    assert len(valid_indices) == 5
    
    # Check that feature vector contains expected data
    # (ASVI_sym, ASVI_sec, Open, High, Low, Close, Volume, 8 technicals)
    # The last 6 technicals were missing, so they should be 0.0
    first_window = X_seq[0]
    assert first_window[0, 0] == 1.0 # ASVI sym
    assert first_window[0, 5] == 1.0 # Close price
    assert first_window[0, 14] == 0.0 # Missing technical

def test_build_lstm_attention_tensors_insufficient_data():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    df_ohlcv = pd.DataFrame({"Close": np.ones(10)}, index=dates)
    df_asvi = pd.Series(np.ones(10), index=dates)
    
    with pytest.raises(ValueError, match="Not enough data"):
        build_lstm_attention_tensors("AAPL", df_ohlcv, df_ohlcv, df_asvi, df_asvi, sequence_length=15)

