"""InvestYo Quant Platform — Synthetic Diffusion Stress Testing Engine.

Generates non-Gaussian synthetic market return paths and volatility regimes
using historical return window calibration for rigorous stress testing.
"""

import numpy as np
from typing import Dict, Tuple


def build_return_windows(returns: np.ndarray, window_len: int, max_windows: int = 200) -> np.ndarray:
    """
    Builds overlapping windows of REAL historical returns for
    ``train_diffusion_model``'s ``historical_data`` argument (closes audit
    finding F7: this previously fed the model
    ``np.random.randn(...) * volatility + drift`` -- fabricated Gaussian
    noise, not real market data).

    Uses the MOST RECENT ``max_windows`` windows when more are available
    (most relevant to the current volatility regime); uses every available
    window otherwise. Purely a windowing utility over an already-real,
    already-ordered array -- introduces no lookahead of its own, since every
    window is a contiguous slice of ``returns`` and never derives a value
    from outside the array it's given.

    Returns an ``(N, window_len)`` array; ``N == 0`` (never fabricated
    padding) when ``returns`` is shorter than ``window_len``.
    """
    n_available = len(returns) - window_len + 1
    if n_available <= 0:
        return np.zeros((0, window_len))
    n_windows = min(n_available, max_windows)
    start = n_available - n_windows
    return np.stack([returns[i: i + window_len] for i in range(start, start + n_windows)], axis=0)


def train_diffusion_model(historical_data: np.ndarray, epochs: int = 1000, lr: float = 1e-2) -> Dict:
    """
    Train a score-based generative diffusion model on historical market paths.
    Uses an Ornstein-Uhlenbeck (OU) forward process.
    
    Args:
        historical_data: (N, L) numpy array of N historical paths of length L.
        epochs: Number of training epochs.
        lr: Learning rate for Adam optimizer.
        
    Returns:
        dict: Trained model parameters.
    """
    N, L = historical_data.shape
    
    # Simple MLP score network: 1 hidden layer
    hidden_dim = 64
    np.random.seed(42) # For reproducibility
    W1 = np.random.randn(L + 1, hidden_dim) * np.sqrt(2.0 / (L + 1))
    b1 = np.zeros(hidden_dim)
    W2 = np.random.randn(hidden_dim, L) * np.sqrt(2.0 / hidden_dim)
    b2 = np.zeros(L)
    
    # Adam state
    m_W1, v_W1 = np.zeros_like(W1), np.zeros_like(W1)
    m_b1, v_b1 = np.zeros_like(b1), np.zeros_like(b1)
    m_W2, v_W2 = np.zeros_like(W2), np.zeros_like(W2)
    m_b2, v_b2 = np.zeros_like(b2), np.zeros_like(b2)
    
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    batch_size = min(64, N)
    
    for epoch in range(1, epochs + 1):
        idx = np.random.randint(0, N, size=batch_size)
        x0 = historical_data[idx]
        
        # Sample tau in (0.01, 1.0)
        tau = np.random.uniform(0.01, 1.0, size=(batch_size, 1))
        z = np.random.randn(batch_size, L)
        
        # OU process marginal p(x_tau | x_0)
        mean = x0 * np.exp(-tau)
        var = 1.0 - np.exp(-2.0 * tau)
        std = np.sqrt(var)
        x_tau = mean + std * z
        
        # True score for Gaussian marginal
        target_score = -z / std
        
        # Forward pass
        inputs = np.concatenate([x_tau, tau], axis=1)
        h1 = inputs @ W1 + b1
        h1_relu = np.maximum(0, h1)
        pred_score = h1_relu @ W2 + b2
        
        # Gradients
        d_pred = 2.0 * (pred_score - target_score) / (batch_size * L)
        d_W2 = h1_relu.T @ d_pred
        d_b2 = np.sum(d_pred, axis=0)
        
        d_h1_relu = d_pred @ W2.T
        d_h1 = d_h1_relu.copy()
        d_h1[h1 <= 0] = 0
        
        d_W1 = inputs.T @ d_h1
        d_b1 = np.sum(d_h1, axis=0)
        
        # Adam updates
        m_W1 = beta1 * m_W1 + (1 - beta1) * d_W1
        v_W1 = beta2 * v_W1 + (1 - beta2) * (d_W1 ** 2)
        m_hat_W1 = m_W1 / (1 - beta1 ** epoch)
        v_hat_W1 = v_W1 / (1 - beta2 ** epoch)
        W1 -= lr * m_hat_W1 / (np.sqrt(v_hat_W1) + epsilon)
        
        m_b1 = beta1 * m_b1 + (1 - beta1) * d_b1
        v_b1 = beta2 * v_b1 + (1 - beta2) * (d_b1 ** 2)
        m_hat_b1 = m_b1 / (1 - beta1 ** epoch)
        v_hat_b1 = v_b1 / (1 - beta2 ** epoch)
        b1 -= lr * m_hat_b1 / (np.sqrt(v_hat_b1) + epsilon)
        
        m_W2 = beta1 * m_W2 + (1 - beta1) * d_W2
        v_W2 = beta2 * v_W2 + (1 - beta2) * (d_W2 ** 2)
        m_hat_W2 = m_W2 / (1 - beta1 ** epoch)
        v_hat_W2 = v_W2 / (1 - beta2 ** epoch)
        W2 -= lr * m_hat_W2 / (np.sqrt(v_hat_W2) + epsilon)
        
        m_b2 = beta1 * m_b2 + (1 - beta1) * d_b2
        v_b2 = beta2 * v_b2 + (1 - beta2) * (d_b2 ** 2)
        m_hat_b2 = m_b2 / (1 - beta1 ** epoch)
        v_hat_b2 = v_b2 / (1 - beta2 ** epoch)
        b2 -= lr * m_hat_b2 / (np.sqrt(v_hat_b2) + epsilon)
        
    return {"W1": W1, "b1": b1, "W2": W2, "b2": b2, "L": L}

def generate_synthetic_crash_paths(model: Dict, num_paths: int = 1000, steps: int = 100, dt: float = 0.01) -> np.ndarray:
    """
    Generate synthetic paths by solving the reverse SDE with Euler-Maruyama.
    
    Args:
        model: Trained model parameters.
        num_paths: Number of synthetic paths to generate.
        steps: Number of integration steps.
        dt: Integration time step (should sum to tau_max - tau_min).
        
    Returns:
        np.ndarray: Generated synthetic paths of shape (num_paths, L).
    """
    L = model["L"]
    tau_max = dt * steps
    if tau_max == 0:
        tau_max = 1.0
        dt = tau_max / steps

    # Start from stationary distribution of OU process N(0, I)
    x = np.random.randn(num_paths, L)
    
    W1, b1, W2, b2 = model["W1"], model["b1"], model["W2"], model["b2"]
    
    # Reverse SDE integration (Euler-Maruyama solver)
    for i in range(steps):
        tau = tau_max - i * dt
        tau_vec = np.full((num_paths, 1), max(tau, 1e-3))
        
        # Predict score
        inputs = np.concatenate([x, tau_vec], axis=1)
        h1 = np.maximum(0, inputs @ W1 + b1)
        score = h1 @ W2 + b2
        
        # Reverse SDE for OU: dx = [-x - 2 * score] dtau_rev + sqrt(2) dW_rev
        drift = -x - 2.0 * score
        diffusion = np.sqrt(2.0)
        
        z = np.random.randn(num_paths, L)
        x = x + drift * dt + diffusion * np.sqrt(dt) * z
        
    return x

def compute_diffusion_var(paths: np.ndarray, confidence_level: float = 0.95) -> Tuple[float, float]:
    """
    Compute Value at Risk (VaR) and Conditional Value at Risk (CVaR)
    for a set of return paths.
    
    Args:
        paths: (num_paths, L) array of generated return paths.
        confidence_level: VaR confidence level (e.g., 0.95).
        
    Returns:
        Tuple[float, float]: (VaR, CVaR) represented as positive loss magnitudes.
    """
    # Total return over the path
    total_returns = np.sum(paths, axis=1)
    
    alpha = 1.0 - confidence_level
    # The alpha quantile is the cutoff for the worst (alpha*100)% returns
    quantile_return = float(np.percentile(total_returns, alpha * 100))
    
    # Expected shortfall is the average of returns worse than or equal to the quantile
    tail_returns = total_returns[total_returns <= quantile_return]
    cvar_return = float(np.mean(tail_returns)) if len(tail_returns) > 0 else quantile_return
    
    # Return positive loss values representing magnitude of loss
    return -quantile_return, -cvar_return
