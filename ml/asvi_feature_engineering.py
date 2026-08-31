import pandas as pd
import numpy as np
from typing import Tuple, List, Optional
import logging

logger = logging.getLogger(__name__)

# Standard GICS Sector to SPDR ETF Mapping
SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Industrials": "XLI",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
    "Basic Materials": "XLB",
    "Communication Services": "XLC"
}

def resolve_sector_proxy(sector: Optional[str]) -> str:
    """Returns the SPDR ETF ticker for a given sector name. Falls back to SPY."""
    if not sector:
        return "SPY"
    return SECTOR_TO_ETF.get(sector, "SPY")

def build_lstm_attention_tensors(
    symbol: str,
    df_ohlcv: pd.DataFrame,
    df_sector_ohlcv: pd.DataFrame,
    df_asvi_symbol: pd.Series,
    df_asvi_sector: pd.Series,
    sequence_length: int = 15,
) -> Tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]:
    """
    Builds the 3D sliding window tensors for the LSTM-Attention model.
    Expected to be called after technical indicators have been added to df_ohlcv.
    """
    # 1. Align all data on the same index
    common_idx = df_ohlcv.index
    
    # Forward fill ASVI gaps, then fill remaining with 0
    asvi_sym_aligned = df_asvi_symbol.reindex(common_idx).ffill().fillna(0.0)
    asvi_sec_aligned = df_asvi_sector.reindex(common_idx).ffill().fillna(0.0)
    
    # 2. Extract features
    # Required columns in df_ohlcv: Open, High, Low, Close, Volume
    # Optional technicals: RSI_14, EMA_12, EMA_26, MACD, MACD_Signal, MACD_Hist, Realized_Vol_60D
    # We will safely extract what is available and fillna
    
    features = []
    
    # Feature 1: Target ASVI
    features.append(asvi_sym_aligned.values)
    
    # Feature 2: Sector ASVI
    features.append(asvi_sec_aligned.values)
    
    # Features 3-7: OHLCV
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df_ohlcv.columns:
            features.append(df_ohlcv[col].ffill().fillna(0.0).values)
        else:
            features.append(np.zeros(len(common_idx)))
            
    # Features 8+: Technicals
    tech_cols = ['RSI_14', 'EMA_12', 'EMA_26', 'MACD', 'MACD_Signal', 'MACD_Hist', 'Realized_Vol_60D', 'SMA_50']
    for col in tech_cols:
        if col in df_ohlcv.columns:
            features.append(df_ohlcv[col].ffill().fillna(0.0).values)
        else:
            features.append(np.zeros(len(common_idx)))
            
    # Stack into (time_steps, num_features)
    feature_matrix = np.column_stack(features)
    
    # Standard scale features
    # CRITICAL: We only scale across the time dimension for numerical stability.
    # To prevent lookahead, we do NOT use global mean/std from the future.
    # We'll use a simple rolling standardization or just train on the raw values 
    # and rely on BatchNorm/LayerNorm inside the network. Wait, neural nets need scaling.
    # Actually, we should standard-scale the feature matrix using the entire matrix 
    # if it's strictly historical, but for prediction we need the training mean/std.
    # Let's return raw features and let the caller do train/val split and scaling, 
    # OR we return it unscaled and let `fit_predict_lstm_attention` add LayerNormalization?
    # Adding LayerNormalization as the first layer in the model is the safest and most robust 
    # way to avoid lookahead bias in scaling.
    
    # Let's create sliding windows
    n_samples = len(feature_matrix)
    if n_samples < sequence_length:
        raise ValueError(f"Not enough data to create sequence of length {sequence_length}")
        
    X_seq = []
    Y_seq = []
    valid_indices = []
    
    # Y is the forward 1-day return of 'Close'
    close_idx = 5 # 0: ASVI, 1: SectorASVI, 2: Open, 3: High, 4: Low, 5: Close
    
    for i in range(n_samples - sequence_length):
        window = feature_matrix[i:i+sequence_length]
        target = (feature_matrix[i+sequence_length, close_idx] - feature_matrix[i+sequence_length-1, close_idx]) / (feature_matrix[i+sequence_length-1, close_idx] + 1e-9)
        
        X_seq.append(window)
        Y_seq.append(target)
        valid_indices.append(common_idx[i+sequence_length])
        
    return np.array(X_seq), np.array(Y_seq), valid_indices

