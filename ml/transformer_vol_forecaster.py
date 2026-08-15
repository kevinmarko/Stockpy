import numpy as np
import pandas as pd
from scipy.special import softmax
from scipy.optimize import minimize
from typing import Dict, List, Tuple, Any

def build_tft_model(seq_len: int, d_model: int, num_heads: int, horizons: List[int]) -> Dict[str, Any]:
    """
    Initializes weights for the NumPy-based Transformer/TFT model.
    """
    assert d_model % num_heads == 0, "d_model must be divisible by num_heads"
    
    # Initialize weights
    weights = {
        'W_q': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        'W_k': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        'W_v': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        'W_o': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        # Gating network weights (GLU)
        'W_gate1': np.random.randn(d_model, d_model * 2) / np.sqrt(d_model),
        'b_gate1': np.zeros(d_model * 2),
        'W_gate2': np.random.randn(d_model, d_model * 2) / np.sqrt(d_model),
        'b_gate2': np.zeros(d_model * 2),
        # Output layers for each horizon
        'W_out': np.random.randn(d_model, len(horizons)) / np.sqrt(d_model),
        'b_out': np.zeros(len(horizons))
    }
    
    model = {
        'weights': weights,
        'hyperparameters': {
            'seq_len': seq_len,
            'd_model': d_model,
            'num_heads': num_heads,
            'horizons': horizons
        }
    }
    return model

def glu_forward(X: np.ndarray, W: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Gated Linear Unit forward pass.
    X: (batch_size, seq_len, d_model)
    W: (d_model, d_model * 2)
    b: (d_model * 2,)
    """
    linear = X @ W + b
    d_model = W.shape[0]
    # Split into two halves
    a = linear[..., :d_model]
    b_gate = linear[..., d_model:]
    # Sigmoid for gating
    sigmoid_b = 1 / (1 + np.exp(-np.clip(b_gate, -20, 20)))
    return a * sigmoid_b

def attention_forward(X: np.ndarray, weights: Dict[str, np.ndarray], num_heads: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Multi-head attention forward pass.
    """
    batch_size, seq_len, d_model = X.shape
    d_head = d_model // num_heads
    
    Q = X @ weights['W_q']
    K = X @ weights['W_k']
    V = X @ weights['W_v']
    
    Q = Q.reshape(batch_size, seq_len, num_heads, d_head).transpose(0, 2, 1, 3)
    K = K.reshape(batch_size, seq_len, num_heads, d_head).transpose(0, 2, 1, 3)
    V = V.reshape(batch_size, seq_len, num_heads, d_head).transpose(0, 2, 1, 3)
    
    scores = np.matmul(Q, K.transpose(0, 1, 3, 2)) / np.sqrt(d_head)
    
    # We can use scipy.special.softmax along the last axis
    attn_weights = softmax(scores, axis=-1)
    
    context = np.matmul(attn_weights, V)
    context = context.transpose(0, 2, 1, 3).reshape(batch_size, seq_len, d_model)
    
    output = context @ weights['W_o']
    
    # Average attention weights across heads for return
    avg_attn_weights = np.mean(attn_weights, axis=1) # (batch, seq, seq)
    
    return output, avg_attn_weights

def forward_pass(X: np.ndarray, model: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    """
    Full forward pass including TFT gating and attention.
    """
    weights = model['weights']
    hyperparams = model['hyperparameters']
    
    # 1. Gating
    gated_X = glu_forward(X, weights['W_gate1'], weights['b_gate1'])
    
    # 2. Attention
    attn_out, attn_weights = attention_forward(gated_X, weights, hyperparams['num_heads'])
    
    # 3. Residual & Layer Norm (Simplified, just residual here)
    X_res = X + attn_out
    
    # 4. Another gating
    final_features = glu_forward(X_res, weights['W_gate2'], weights['b_gate2'])
    
    # Extract the last sequence step features for forecasting
    last_step_features = final_features[:, -1, :] # (batch_size, d_model)
    
    # 5. Output predictions
    preds = last_step_features @ weights['W_out'] + weights['b_out'] # (batch_size, len(horizons))
    
    return preds, attn_weights

def train_vol_forecaster(X_train: np.ndarray, y_train: np.ndarray, seq_len: int, d_model: int, num_heads: int, horizons: List[int]) -> Dict[str, Any]:
    """
    Trains the forecaster. Since this is pure numpy, we use an ELM (Extreme Learning Machine) approach:
    Freeze attention and gating weights as random projections, and fit the output layer via Ridge Regression.
    """
    model = build_tft_model(seq_len, d_model, num_heads, horizons)
    weights = model['weights']
    hyperparams = model['hyperparameters']
    
    # Forward pass to get last_step_features for all training data
    # 1. Gating
    gated_X = glu_forward(X_train, weights['W_gate1'], weights['b_gate1'])
    # 2. Attention
    attn_out, _ = attention_forward(gated_X, weights, hyperparams['num_heads'])
    # 3. Residual
    X_res = X_train + attn_out
    # 4. Gating
    final_features = glu_forward(X_res, weights['W_gate2'], weights['b_gate2'])
    
    H = final_features[:, -1, :] # (batch_size, d_model)
    
    # Ridge regression: W_out = (H^T H + lambda I)^-1 H^T y
    lambda_reg = 1e-3
    I = np.eye(H.shape[1])
    
    # Add bias term to H
    H_bias = np.hstack([H, np.ones((H.shape[0], 1))])
    I_bias = np.eye(H_bias.shape[1])
    I_bias[-1, -1] = 0 # Don't regularize bias
    
    # Solve for weights
    try:
        W_out_bias = np.linalg.solve(H_bias.T @ H_bias + lambda_reg * I_bias, H_bias.T @ y_train)
    except np.linalg.LinAlgError:
        # Fallback to pseudo-inverse
        W_out_bias = np.linalg.pinv(H_bias.T @ H_bias + lambda_reg * I_bias) @ (H_bias.T @ y_train)
        
    weights['W_out'] = W_out_bias[:-1, :]
    weights['b_out'] = W_out_bias[-1, :]
    
    return model

def predict_multi_horizon_vol(X: np.ndarray, model: Dict[str, Any]) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    """
    Predicts multi-horizon volatility.
    Returns:
        forecasts: Dict mapping horizon label (e.g., '1d', '5d', '21d', '60d') to predictions.
        attention_weights: The attention matrix from the forward pass.
    """
    preds, attn_weights = forward_pass(X, model)
    
    horizons = model['hyperparameters']['horizons']
    
    forecasts = {}
    for i, h in enumerate(horizons):
        label = f"{h}d"
        forecasts[label] = preds[:, i]
        
    return forecasts, attn_weights
