import numpy as np
import pytest
from ml.transformer_vol_forecaster import (
    build_tft_model,
    train_vol_forecaster,
    predict_multi_horizon_vol,
    forward_pass
)

def test_build_model():
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    assert 'weights' in model
    assert 'W_q' in model['weights']
    assert model['weights']['W_q'].shape == (16, 16)

def test_forward_pass():
    model = build_tft_model(seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    X = np.random.randn(32, 10, 16) # batch=32, seq_len=10, d_model=16
    preds, attn = forward_pass(X, model)
    assert preds.shape == (32, 4)
    assert attn.shape == (32, 10, 10)

def test_train_and_predict():
    X_train = np.random.randn(100, 10, 16)
    y_train = np.random.randn(100, 4) # 4 horizons
    
    model = train_vol_forecaster(X_train, y_train, seq_len=10, d_model=16, num_heads=4, horizons=[1, 5, 21, 60])
    
    X_test = np.random.randn(5, 10, 16)
    forecasts, attn = predict_multi_horizon_vol(X_test, model)
    
    assert isinstance(forecasts, dict)
    assert '1d' in forecasts
    assert '5d' in forecasts
    assert '21d' in forecasts
    assert '60d' in forecasts
    
    assert forecasts['1d'].shape == (5,)
    assert forecasts['60d'].shape == (5,)
    assert attn.shape == (5, 10, 10)
