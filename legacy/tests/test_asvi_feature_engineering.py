import pytest
import pandas as pd
import numpy as np
from ml.asvi_feature_engineering import resolve_sector_proxy, build_lstm_attention_tensors

def test_resolve_sector_proxy():
    assert resolve_sector_proxy("Technology") == "XLK"
    assert resolve_sector_proxy("Financials") == "XLF"
    assert resolve_sector_proxy("UnknownSector") == "SPY"
    assert resolve_sector_proxy(None) == "SPY"

def _full_ohlcv(n=20):
    """All 13 OHLCV + technical columns build_lstm_attention_tensors requires
    -- since the CONSTRAINT #4 fix, a missing one raises rather than being
    silently zero-filled, so every happy-path test must supply the full set."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    df = pd.DataFrame({
        "Open": np.ones(n),
        "High": np.ones(n),
        "Low": np.ones(n),
        "Close": np.arange(1, n + 1, dtype=float),
        "Volume": np.ones(n),
        "RSI_14": np.ones(n) * 50,
        "EMA_12": np.ones(n) * 2.0,
        "EMA_26": np.ones(n) * 3.0,
        "MACD": np.ones(n) * 4.0,
        "MACD_Signal": np.ones(n) * 5.0,
        "MACD_Hist": np.ones(n) * 6.0,
        "Realized_Vol_60D": np.ones(n) * 7.0,
        "SMA_50": np.ones(n) * 8.0,
    }, index=dates)
    return dates, df

def test_build_lstm_attention_tensors():
    dates, df_ohlcv = _full_ohlcv(20)
    df_sector_ohlcv = df_ohlcv.copy()

    df_asvi_symbol = pd.Series(np.ones(20), index=dates)
    df_asvi_sector = pd.Series(np.ones(20), index=dates)

    X_seq, Y_seq, valid_indices, predict_X_seq = build_lstm_attention_tensors(
        "AAPL", df_ohlcv, df_sector_ohlcv, df_asvi_symbol, df_asvi_sector, sequence_length=15
    )

    assert len(X_seq) == 5 # 20 periods, length 15 -> 5 windows
    assert X_seq.shape == (5, 15, 15) # 5 windows, 15 time steps, 15 features
    assert len(Y_seq) == 5
    assert len(valid_indices) == 5

    # Check that feature vector contains expected, genuinely-observed data
    # (ASVI_sym, ASVI_sec, Open, High, Low, Close, Volume, 8 technicals)
    first_window = X_seq[0]
    assert first_window[0, 0] == 1.0 # ASVI sym
    assert first_window[0, 5] == 1.0 # Close price
    assert first_window[0, 8] == 2.0 # EMA_12 (real value, not fabricated)
    assert first_window[0, 14] == 8.0 # SMA_50 (real value, not fabricated)

def test_build_lstm_attention_tensors_insufficient_data():
    dates, df_ohlcv = _full_ohlcv(10)
    df_asvi = pd.Series(np.ones(10), index=dates)

    with pytest.raises(ValueError, match="Not enough data"):
        build_lstm_attention_tensors("AAPL", df_ohlcv, df_ohlcv, df_asvi, df_asvi, sequence_length=15)

def test_build_lstm_attention_tensors_missing_ohlcv_column_raises():
    """CONSTRAINT #4: a missing required OHLCV/technical column must raise,
    never be silently fabricated as a plausible-looking 0.0 (e.g. RSI=0
    implies 'extreme oversold', which is a real, misleading claim about data
    that was never observed)."""
    dates, df_ohlcv = _full_ohlcv(20)
    df_missing = df_ohlcv.drop(columns=["EMA_12"])
    df_asvi = pd.Series(np.ones(20), index=dates)

    with pytest.raises(ValueError, match="EMA_12"):
        build_lstm_attention_tensors("AAPL", df_missing, df_missing, df_asvi, df_asvi, sequence_length=15)

def test_build_lstm_attention_tensors_missing_symbol_asvi_raises():
    """CONSTRAINT #4: a symbol with zero real Google Trends coverage over the
    window must raise rather than be silently treated as 'zero abnormal
    search volume'."""
    dates, df_ohlcv = _full_ohlcv(20)
    empty_asvi = pd.Series(dtype=float)  # reindex -> all-NaN, no real data at all
    present_asvi = pd.Series(np.ones(20), index=dates)

    with pytest.raises(ValueError, match="No Google Trends ASVI data"):
        build_lstm_attention_tensors(
            "AAPL", df_ohlcv, df_ohlcv, empty_asvi, present_asvi, sequence_length=15
        )

def test_build_lstm_attention_tensors_missing_sector_asvi_raises():
    dates, df_ohlcv = _full_ohlcv(20)
    present_asvi = pd.Series(np.ones(20), index=dates)
    empty_asvi = pd.Series(dtype=float)

    with pytest.raises(ValueError, match="No Google Trends sector-proxy ASVI data"):
        build_lstm_attention_tensors(
            "AAPL", df_ohlcv, df_ohlcv, present_asvi, empty_asvi, sequence_length=15
        )
