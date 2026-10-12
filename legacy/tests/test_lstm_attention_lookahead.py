import pytest
import numpy as np
import pandas as pd
from ml.asvi_feature_engineering import build_lstm_attention_tensors

def test_lstm_attention_no_lookahead_perturbation():
    """
    CRITICAL LOOKAHEAD TEST:
    Inject a massive perturbation into t+1 and assert that predictions/features at t remain EXACTLY identical.
    """
    dates = pd.date_range("2026-01-01", periods=100, freq="D")
    df_ohlcv = pd.DataFrame({
        "Open": np.random.rand(100),
        "High": np.random.rand(100),
        "Low": np.random.rand(100),
        "Close": np.random.rand(100),
        "Volume": np.random.rand(100) * 1000,
        "RSI_14": np.random.rand(100) * 100,
        "EMA_12": np.random.rand(100),
        "EMA_26": np.random.rand(100),
        "MACD": np.random.rand(100),
        "MACD_Signal": np.random.rand(100),
        "MACD_Hist": np.random.rand(100),
        "Realized_Vol_60D": np.random.rand(100),
        "SMA_50": np.random.rand(100)
    }, index=dates)
    
    df_sector = df_ohlcv.copy()
    asvi_sym = pd.Series(np.random.rand(100), index=dates)
    asvi_sec = pd.Series(np.random.rand(100), index=dates)
    
    # Baseline
    X_base, Y_base, _, P_base = build_lstm_attention_tensors("AAPL", df_ohlcv, df_sector, asvi_sym, asvi_sec, sequence_length=15)
    
    # Perturb the last day
    df_ohlcv_perturbed = df_ohlcv.copy()
    df_ohlcv_perturbed.loc[dates[-1], "Close"] = 99999.9
    
    X_pert, Y_pert, _, P_pert = build_lstm_attention_tensors("AAPL", df_ohlcv_perturbed, df_sector, asvi_sym, asvi_sec, sequence_length=15)
    
    # All windows in X_base don't include the last day. The last day is only in Y_base for the last window and in P_base.
    # Therefore, X_base and X_pert must be EXACTLY identical everywhere.
    assert np.allclose(X_base, X_pert)
    
    # Y_base will differ ONLY at the very last index, because the last index of Y_seq points to the perturbed day.
    assert np.allclose(Y_base[:-1], Y_pert[:-1])
    assert not np.allclose(Y_base[-1], Y_pert[-1])
    
    # The prediction window WILL differ because it contains the perturbed day.
    assert not np.allclose(P_base, P_pert)
