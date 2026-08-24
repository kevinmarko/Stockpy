"""
Macro-Regime Guided Generative Diffusion Stress Engine (Phase 34).

Provides score-based generative diffusion modeling with Classifier-Free Guidance (CFG)
and reverse SDE Euler-Maruyama integration for non-linear crisis stress testing and
multi-quantile VaR/CVaR risk evaluation.
"""

import numpy as np
from typing import Any, Dict, List, Optional, Tuple, Union

REGIME_MAP: Dict[str, int] = {
    "unconditional": 0,
    "vol_shock": 1,
    "credit_freeze": 2,
    "stagflation": 3,
    "liquidity_squeeze": 4,
}

REGIME_ID_TO_NAME: Dict[int, str] = {v: k for k, v in REGIME_MAP.items()}


def _reverse_sde_drift(x: np.ndarray, score: np.ndarray) -> np.ndarray:
    """Drift term for the Euler-Maruyama discretization of the reverse-time
    OU SDE shared by ``generate_synthetic_crash_paths`` and
    ``generate_guided_crisis_paths`` (fixes a sign error present in both
    call sites through 2026-08 -- see
    ``docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md``).

    Forward process: ``dx = -x dtau + sqrt(2) dW`` (tau increasing, 0 ->
    tau_max). Anderson (1982)'s reverse-time SDE for a forward process
    ``dx = f(x,tau) dtau + g(tau) dW`` is
    ``dx = [f - g^2 * score] d(tau_bar) + g d(W_bar)``, where ``d(tau_bar)``
    is a NEGATIVE infinitesimal step -- time runs backward. Both generation
    loops decrease ``tau`` each iteration (``tau = tau_max - i*dt``) while
    taking a POSITIVE step ``dt`` (``x = x + drift*dt + ...``), i.e. they
    discretize with ``d(tau_bar) = -dt``. Substituting flips the bracketed
    term's sign:
        x_{i+1} = x_i + [-f(x_i) + g^2 * score(x_i, tau_i)] * dt
                       + g * sqrt(dt) * z
    For this OU process, ``f(x) = -x`` and ``g^2 = 2``, so
    ``-f + g^2 * score = x + 2 * score`` -- NOT ``-x - 2 * score`` (the
    literal negation, which was the bug: it diverges instead of denoising,
    since it reinforces rather than corrects the added Wiener noise).

    A single shared helper -- rather than the formula inlined separately at
    each call site -- is deliberate: two independently-inlined copies of
    this formula is exactly how the sign bug shipped undetected at one site
    without being caught by the other.
    """
    return x + 2.0 * score


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


def train_diffusion_model(
    historical_data: np.ndarray,
    epochs: int = 1000,
    lr: float = 1e-2,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """
    Train an unconditional score-based generative diffusion model on historical market paths.
    Uses an Ornstein-Uhlenbeck (OU) forward process.
    
    Args:
        historical_data: (N, L) numpy array of N historical paths of length L.
        epochs: Number of training epochs.
        lr: Learning rate for Adam optimizer.
        seed: Random seed for initialization reproducibility.
        
    Returns:
        dict: Trained model parameters containing W1, b1, W2, b2, L.
    """
    N, L = historical_data.shape
    
    with np.errstate(all="ignore"):
        # Simple MLP score network: 1 hidden layer
        hidden_dim = 64
        if seed is not None:
            np.random.seed(seed)
        W1 = (np.random.randn(L + 1, hidden_dim) * np.sqrt(2.0 / (L + 1))).astype(np.float64)
        b1 = np.zeros(hidden_dim, dtype=np.float64)
        W2 = (np.random.randn(hidden_dim, L) * np.sqrt(2.0 / hidden_dim)).astype(np.float64)
        b2 = np.zeros(L, dtype=np.float64)
        
        # Adam state
        m_W1, v_W1 = np.zeros_like(W1), np.zeros_like(W1)
        m_b1, v_b1 = np.zeros_like(b1), np.zeros_like(b1)
        m_W2, v_W2 = np.zeros_like(W2), np.zeros_like(W2)
        m_b2, v_b2 = np.zeros_like(b2), np.zeros_like(b2)
        
        beta1, beta2, epsilon = 0.9, 0.999, 1e-8
        batch_size = min(64, N)
        
        for epoch in range(1, epochs + 1):
            idx = np.random.randint(0, N, size=batch_size)
            x0 = historical_data[idx].astype(np.float64)
            
            # Sample tau in (0.01, 1.0)
            tau = np.random.uniform(0.01, 1.0, size=(batch_size, 1))
            z = np.random.randn(batch_size, L)
            
            # OU process marginal p(x_tau | x_0)
            mean = x0 * np.exp(-tau)
            var = np.maximum(1.0 - np.exp(-2.0 * tau), 1e-5)
            std = np.sqrt(var)
            x_tau = mean + std * z
            
            # True score for Gaussian marginal
            target_score = -z / std
            
            # Forward pass
            inputs = np.concatenate([np.clip(x_tau, -20.0, 20.0), tau], axis=1)
            h1 = inputs @ W1 + b1
            h1_relu = np.maximum(0, h1)
            pred_score = h1_relu @ W2 + b2
            
            # Gradients with clipping
            d_pred = 2.0 * (pred_score - target_score) / (batch_size * L)
            d_pred = np.clip(d_pred, -5.0, 5.0)
            
            d_W2 = h1_relu.T @ d_pred
            d_b2 = np.sum(d_pred, axis=0)
            
            d_h1_relu = d_pred @ W2.T
            d_h1 = d_h1_relu.copy()
            d_h1[h1 <= 0] = 0
            
            d_W1 = inputs.T @ d_h1
            d_b1 = np.sum(d_h1, axis=0)
            
            # Clip parameter gradients
            d_W1 = np.clip(d_W1, -2.0, 2.0)
            d_b1 = np.clip(d_b1, -2.0, 2.0)
            d_W2 = np.clip(d_W2, -2.0, 2.0)
            d_b2 = np.clip(d_b2, -2.0, 2.0)
            
            # Adam updates with weight decay
            m_W1 = beta1 * m_W1 + (1 - beta1) * d_W1
            v_W1 = beta2 * v_W1 + (1 - beta2) * (d_W1 ** 2)
            m_hat_W1 = m_W1 / (1 - beta1 ** epoch)
            v_hat_W1 = v_W1 / (1 - beta2 ** epoch)
            W1 = W1 * (1.0 - 1e-4) - lr * m_hat_W1 / (np.sqrt(v_hat_W1) + epsilon)
            
            m_b1 = beta1 * m_b1 + (1 - beta1) * d_b1
            v_b1 = beta2 * v_b1 + (1 - beta2) * (d_b1 ** 2)
            m_hat_b1 = m_b1 / (1 - beta1 ** epoch)
            v_hat_b1 = v_b1 / (1 - beta2 ** epoch)
            b1 = b1 - lr * m_hat_b1 / (np.sqrt(v_hat_b1) + epsilon)
            
            m_W2 = beta1 * m_W2 + (1 - beta1) * d_W2
            v_W2 = beta2 * v_W2 + (1 - beta2) * (d_W2 ** 2)
            m_hat_W2 = m_W2 / (1 - beta1 ** epoch)
            v_hat_W2 = v_W2 / (1 - beta2 ** epoch)
            W2 = W2 * (1.0 - 1e-4) - lr * m_hat_W2 / (np.sqrt(v_hat_W2) + epsilon)
            
            m_b2 = beta1 * m_b2 + (1 - beta1) * d_b2
            v_b2 = beta2 * v_b2 + (1 - beta2) * (d_b2 ** 2)
            m_hat_b2 = m_b2 / (1 - beta1 ** epoch)
            v_hat_b2 = v_b2 / (1 - beta2 ** epoch)
            b2 = b2 - lr * m_hat_b2 / (np.sqrt(v_hat_b2) + epsilon)
            
    return {"W1": W1, "b1": b1, "W2": W2, "b2": b2, "L": L}


def train_conditional_diffusion_model(
    historical_data: np.ndarray,
    regime_labels: Optional[np.ndarray] = None,
    num_classes: int = 5,
    epochs: int = 1000,
    lr: float = 1e-2,
    p_uncond: float = 0.15,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """
    Train a macro-regime conditioned score-based generative diffusion model on historical market paths
    with Classifier-Free Guidance (CFG) conditioning.
    
    Args:
        historical_data: (N, L) numpy array of N historical paths of length L.
        regime_labels: (N,) optional array of integer regime class indices [0, num_classes-1] or regime names.
        num_classes: Total number of regime classes (default 5 for REGIME_MAP).
        epochs: Number of training epochs.
        lr: Learning rate for Adam optimizer.
        p_uncond: Probability of dropping class conditioning to class 0 (unconditional) for CFG training.
        seed: Random seed for reproducibility.
        
    Returns:
        dict: Trained model parameters with W1, b1, W2, b2, L, num_classes.
    """
    N, L = historical_data.shape
    
    with np.errstate(all="ignore"):
        # Process regime labels
        if regime_labels is None:
            labels_int = np.zeros(N, dtype=int)
        else:
            labels_int = np.zeros(N, dtype=int)
            for i, lab in enumerate(regime_labels):
                if isinstance(lab, str):
                    labels_int[i] = REGIME_MAP.get(lab.lower(), 0)
                elif isinstance(lab, (int, np.integer)):
                    labels_int[i] = int(lab) % num_classes
                else:
                    labels_int[i] = 0
                    
        hidden_dim = 64
        in_dim = L + 1 + num_classes
        if seed is not None:
            np.random.seed(seed)
            
        W1 = (np.random.randn(in_dim, hidden_dim) * np.sqrt(2.0 / in_dim)).astype(np.float64)
        b1 = np.zeros(hidden_dim, dtype=np.float64)
        W2 = (np.random.randn(hidden_dim, L) * np.sqrt(2.0 / hidden_dim)).astype(np.float64)
        b2 = np.zeros(L, dtype=np.float64)
        
        # Adam state
        m_W1, v_W1 = np.zeros_like(W1), np.zeros_like(W1)
        m_b1, v_b1 = np.zeros_like(b1), np.zeros_like(b1)
        m_W2, v_W2 = np.zeros_like(W2), np.zeros_like(W2)
        m_b2, v_b2 = np.zeros_like(b2), np.zeros_like(b2)
        
        beta1, beta2, epsilon = 0.9, 0.999, 1e-8
        batch_size = min(64, N)
        
        for epoch in range(1, epochs + 1):
            idx = np.random.randint(0, N, size=batch_size)
            x0 = historical_data[idx].astype(np.float64)
            batch_labels = labels_int[idx].copy()
            
            # Classifier-free guidance training: drop conditioning to class 0 with probability p_uncond
            if p_uncond > 0:
                drop_mask = np.random.rand(batch_size) < p_uncond
                batch_labels[drop_mask] = 0
                
            # One-hot embedding: [batch_size, num_classes]
            c_embed = np.zeros((batch_size, num_classes), dtype=float)
            c_embed[np.arange(batch_size), batch_labels] = 1.0
            
            # Sample tau in (0.01, 1.0)
            tau = np.random.uniform(0.01, 1.0, size=(batch_size, 1))
            z = np.random.randn(batch_size, L)
            
            # OU process marginal p(x_tau | x_0)
            mean = x0 * np.exp(-tau)
            var = np.maximum(1.0 - np.exp(-2.0 * tau), 1e-5)
            std = np.sqrt(var)
            x_tau = mean + std * z
            
            # True score for Gaussian marginal
            target_score = -z / std
            
            # Forward pass: inputs = [x_tau, tau, c_embed]
            inputs = np.concatenate([np.clip(x_tau, -20.0, 20.0), tau, c_embed], axis=1)
            h1 = inputs @ W1 + b1
            h1_relu = np.maximum(0, h1)
            pred_score = h1_relu @ W2 + b2
            
            # Gradients with clipping
            d_pred = 2.0 * (pred_score - target_score) / (batch_size * L)
            d_pred = np.clip(d_pred, -5.0, 5.0)
            
            d_W2 = h1_relu.T @ d_pred
            d_b2 = np.sum(d_pred, axis=0)
            
            d_h1_relu = d_pred @ W2.T
            d_h1 = d_h1_relu.copy()
            d_h1[h1 <= 0] = 0
            
            d_W1 = inputs.T @ d_h1
            d_b1 = np.sum(d_h1, axis=0)
            
            # Clip parameter gradients
            d_W1 = np.clip(d_W1, -2.0, 2.0)
            d_b1 = np.clip(d_b1, -2.0, 2.0)
            d_W2 = np.clip(d_W2, -2.0, 2.0)
            d_b2 = np.clip(d_b2, -2.0, 2.0)
            
            # Adam updates with weight decay
            m_W1 = beta1 * m_W1 + (1 - beta1) * d_W1
            v_W1 = beta2 * v_W1 + (1 - beta2) * (d_W1 ** 2)
            m_hat_W1 = m_W1 / (1 - beta1 ** epoch)
            v_hat_W1 = v_W1 / (1 - beta2 ** epoch)
            W1 = W1 * (1.0 - 1e-4) - lr * m_hat_W1 / (np.sqrt(v_hat_W1) + epsilon)
            
            m_b1 = beta1 * m_b1 + (1 - beta1) * d_b1
            v_b1 = beta2 * v_b1 + (1 - beta2) * (d_b1 ** 2)
            m_hat_b1 = m_b1 / (1 - beta1 ** epoch)
            v_hat_b1 = v_b1 / (1 - beta2 ** epoch)
            b1 = b1 - lr * m_hat_b1 / (np.sqrt(v_hat_b1) + epsilon)
            
            m_W2 = beta1 * m_W2 + (1 - beta1) * d_W2
            v_W2 = beta2 * v_W2 + (1 - beta2) * (d_W2 ** 2)
            m_hat_W2 = m_W2 / (1 - beta1 ** epoch)
            v_hat_W2 = v_W2 / (1 - beta2 ** epoch)
            W2 = W2 * (1.0 - 1e-4) - lr * m_hat_W2 / (np.sqrt(v_hat_W2) + epsilon)
            
            m_b2 = beta1 * m_b2 + (1 - beta1) * d_b2
            v_b2 = beta2 * v_b2 + (1 - beta2) * (d_b2 ** 2)
            m_hat_b2 = m_b2 / (1 - beta1 ** epoch)
            v_hat_b2 = v_b2 / (1 - beta2 ** epoch)
            b2 = b2 - lr * m_hat_b2 / (np.sqrt(v_hat_b2) + epsilon)
            
    return {
        "W1": W1,
        "b1": b1,
        "W2": W2,
        "b2": b2,
        "L": L,
        "num_classes": num_classes,
    }


def generate_synthetic_crash_paths(
    model: Dict[str, Any],
    num_paths: int = 1000,
    steps: int = 100,
    dt: float = 0.01,
) -> np.ndarray:
    """
    Generate synthetic paths by solving the reverse SDE with Euler-Maruyama.
    Supports both unconditional models and conditional models (defaulting to unconditional).
    
    Args:
        model: Trained model parameters.
        num_paths: Number of synthetic paths to generate.
        steps: Number of integration steps.
        dt: Integration time step.
        
    Returns:
        np.ndarray: Generated synthetic paths of shape (num_paths, L).
    """
    L = model["L"]
    num_classes = model.get("num_classes", None)
    tau_max = dt * steps
    if tau_max == 0:
        tau_max = 1.0
        dt = tau_max / steps

    with np.errstate(all="ignore"):
        # Start from stationary distribution of OU process N(0, I)
        x = np.random.randn(num_paths, L)
        
        W1, b1, W2, b2 = model["W1"], model["b1"], model["W2"], model["b2"]
        is_conditional = num_classes is not None and W1.shape[0] == L + 1 + num_classes
        
        c_uncond = None
        if is_conditional:
            c_uncond = np.zeros((num_paths, num_classes), dtype=float)
            c_uncond[:, 0] = 1.0
            
        # Reverse SDE integration (Euler-Maruyama solver)
        for i in range(steps):
            tau = max(tau_max - i * dt, 1e-3)
            tau_vec = np.full((num_paths, 1), tau)
            x_in = np.clip(x, -20.0, 20.0)
            
            # Predict score
            if is_conditional:
                inputs = np.concatenate([x_in, tau_vec, c_uncond], axis=1)
            else:
                inputs = np.concatenate([x_in, tau_vec], axis=1)
                
            h1 = np.maximum(0, inputs @ W1 + b1)
            score = np.clip(h1 @ W2 + b2, -50.0, 50.0)
            
            # Reverse SDE for OU: dx = [x + 2 * score] dt + sqrt(2) dW
            # (see _reverse_sde_drift's docstring for the sign derivation)
            drift = _reverse_sde_drift(x, score)
            diffusion = np.sqrt(2.0)

            z = np.random.randn(num_paths, L)
            x = np.clip(x + drift * dt + diffusion * np.sqrt(dt) * z, -50.0, 50.0)

    return x


def generate_guided_crisis_paths(
    model: Dict[str, Any],
    regime: Union[str, int] = "vol_shock",
    guidance_scale: float = 2.0,
    num_paths: int = 1000,
    steps: int = 100,
    dt: float = 0.01,
) -> np.ndarray:
    """
    Generate synthetic crisis paths using classifier-free guided reverse SDE diffusion.
    
    Score combination:
        \\tilde{s}_\\theta(x, \\tau, c) = (1 + w) s_\\theta(x, \\tau, c) - w s_\\theta(x, \\tau, 0)
        
    Reverse SDE integration:
        dX_t = [X_t + 2 \\tilde{s}_\\theta(X_t, \\tau, c)] dt + \\sqrt{2} dW_t

    Args:
        model: Trained model dictionary with W1, b1, W2, b2, L, num_classes.
        regime: Target macro regime name (e.g. "vol_shock", "stagflation") or class index.
        guidance_scale: Classifier-free guidance weight w (w=0.0 is unguided conditional).
        num_paths: Number of synthetic paths to simulate.
        steps: Number of integration steps.
        dt: Step size for Euler-Maruyama discretization.
        
    Returns:
        np.ndarray: (num_paths, L) array of generated return paths.
    """
    L = model["L"]
    num_classes = model.get("num_classes", 5)
    W1, b1, W2, b2 = model["W1"], model["b1"], model["W2"], model["b2"]
    
    # Resolve regime class index
    if isinstance(regime, str):
        class_idx = REGIME_MAP.get(regime.lower(), 0)
    elif isinstance(regime, (int, np.integer)):
        class_idx = int(regime) % num_classes
    else:
        class_idx = 0
        
    # Check if model has conditional weights
    is_conditional = W1.shape[0] == (L + 1 + num_classes)
    
    tau_max = dt * steps
    if tau_max == 0:
        tau_max = 1.0
        dt = tau_max / steps
        
    with np.errstate(all="ignore"):
        # Initial state sampled from standard Gaussian N(0, I)
        x = np.random.randn(num_paths, L)
        
        if is_conditional:
            # Precompute one-hot embeddings for conditional class and unconditional baseline (class 0)
            c_embed_cond = np.zeros((num_paths, num_classes), dtype=float)
            c_embed_cond[:, class_idx] = 1.0
            
            c_embed_uncond = np.zeros((num_paths, num_classes), dtype=float)
            c_embed_uncond[:, 0] = 1.0
            
        for i in range(steps):
            tau = max(tau_max - i * dt, 1e-3)
            tau_vec = np.full((num_paths, 1), tau)
            x_in = np.clip(x, -20.0, 20.0)
            
            if is_conditional and guidance_scale > 0 and class_idx != 0:
                # Conditional forward pass
                inputs_cond = np.concatenate([x_in, tau_vec, c_embed_cond], axis=1)
                h1_cond = np.maximum(0, inputs_cond @ W1 + b1)
                score_cond = h1_cond @ W2 + b2
                
                # Unconditional forward pass (class 0)
                inputs_uncond = np.concatenate([x_in, tau_vec, c_embed_uncond], axis=1)
                h1_uncond = np.maximum(0, inputs_uncond @ W1 + b1)
                score_uncond = h1_uncond @ W2 + b2
                
                # Classifier-free guidance formula: (1 + w) * s(c) - w * s(0)
                score = (1.0 + guidance_scale) * score_cond - guidance_scale * score_uncond
            elif is_conditional:
                inputs = np.concatenate([x_in, tau_vec, c_embed_cond], axis=1)
                h1 = np.maximum(0, inputs @ W1 + b1)
                score = h1 @ W2 + b2
            else:
                inputs = np.concatenate([x_in, tau_vec], axis=1)
                h1 = np.maximum(0, inputs @ W1 + b1)
                score = h1 @ W2 + b2
                
            score = np.clip(score, -50.0, 50.0)
            
            # Reverse SDE: dx = [x + 2 * score] dt + sqrt(2) dW
            # (see _reverse_sde_drift's docstring for the sign derivation)
            drift = _reverse_sde_drift(x, score)
            diffusion = np.sqrt(2.0)

            z = np.random.randn(num_paths, L)
            x = np.clip(x + drift * dt + diffusion * np.sqrt(dt) * z, -50.0, 50.0)

    return x


def compute_diffusion_var(paths: np.ndarray, confidence_level: float = 0.95) -> Tuple[float, float]:
    """
    Compute Value at Risk (VaR) and Conditional Value at Risk (CVaR)
    for a set of return paths.
    
    Args:
        paths: (num_paths, L) or (num_paths,) array of generated return paths.
        confidence_level: VaR confidence level (e.g., 0.95).
        
    Returns:
        Tuple[float, float]: (VaR, CVaR) represented as positive loss magnitudes.
    """
    total_returns = np.sum(paths, axis=1) if paths.ndim > 1 else paths
    
    alpha = 1.0 - confidence_level
    quantile_return = float(np.percentile(total_returns, alpha * 100))
    
    tail_returns = total_returns[total_returns <= quantile_return]
    cvar_return = float(np.mean(tail_returns)) if len(tail_returns) > 0 else quantile_return
    
    return -quantile_return, -cvar_return


def compute_multi_quantile_var(
    paths: np.ndarray,
    confidence_levels: Optional[List[float]] = None,
) -> Dict[str, Tuple[float, float]]:
    """
    Compute multi-quantile VaR and CVaR metrics across multiple confidence levels.
    
    Args:
        paths: (num_paths, L) or (num_paths,) array of simulated return paths.
        confidence_levels: List of confidence levels (default [0.95, 0.99]).
        
    Returns:
        Dict[str, Tuple[float, float]]: Mapping of confidence label to (VaR, CVaR),
            e.g. {"95": (VaR_95, CVaR_95), "99": (VaR_99, CVaR_99)}.
    """
    if confidence_levels is None:
        confidence_levels = [0.95, 0.99]
        
    results: Dict[str, Tuple[float, float]] = {}
    for cl in confidence_levels:
        key = f"{int(round(cl * 100))}"
        results[key] = compute_diffusion_var(paths, confidence_level=cl)
        
    return results
