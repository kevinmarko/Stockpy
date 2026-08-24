"""InvestYo Quant Platform — Transformer Volatility Forecaster.

Implements a Temporal Fusion Transformer (TFT) architecture in pure NumPy/SciPy
for multi-horizon point volatility prediction.
"""

import logging

import numpy as np
import pandas as pd
from scipy.special import softmax
from scipy.optimize import minimize
from typing import Dict, List, Optional, Tuple, Any, Union

logger = logging.getLogger(__name__)

# Fixed seed for reproducible TFT attention/gating weight init (see
# build_tft_model). Mirrors cnn_lstm_worker.py's CNN_LSTM_RANDOM_SEED
# convention -- this "ELM" (Extreme Learning Machine) design freezes
# attention/gating as random projections forever after init and only fits
# the output layer, so without a fixed seed two back-to-back model builds
# would produce numerically different forecasts against unchanged input.
TFT_RANDOM_SEED = 42


def pinball_loss(
    y_true: Union[np.ndarray, List[float], float],
    y_pred: Union[np.ndarray, List[float], float],
    alpha: float,
) -> float:
    """
    Computes the mean pinball (quantile) loss for target quantile alpha in (0, 1).
    
    L_alpha(y, y_hat) = max(alpha * (y - y_hat), (alpha - 1) * (y - y_hat))
    """
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    e = y_t - y_p
    loss = np.maximum(alpha * e, (alpha - 1.0) * e)
    return float(np.mean(loss))


def build_tft_model(
    seq_len: int,
    d_model: int,
    num_heads: int,
    horizons: List[int],
    quantiles: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Initializes weights for the NumPy-based Transformer/TFT model.
    Supports standard output heads and quantile output heads.
    """
    assert d_model % num_heads == 0, "d_model must be divisible by num_heads"

    # Reproducible weight init (mirrors cnn_lstm_worker.py's CNN_LSTM_RANDOM_SEED
    # precedent) -- these weights are frozen forever after this call, so without
    # a fixed seed two back-to-back builds would diverge for no reason.
    np.random.seed(TFT_RANDOM_SEED)

    qs = quantiles if quantiles is not None else [0.10, 0.50, 0.90]
    
    # Initialize weights
    weights: Dict[str, Any] = {
        'W_q': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        'W_k': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        'W_v': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        'W_o': np.random.randn(d_model, d_model) / np.sqrt(d_model),
        # Gating network weights (GLU)
        'W_gate1': np.random.randn(d_model, d_model * 2) / np.sqrt(d_model),
        'b_gate1': np.zeros(d_model * 2),
        'W_gate2': np.random.randn(d_model, d_model * 2) / np.sqrt(d_model),
        'b_gate2': np.zeros(d_model * 2),
        # Output layers for each horizon (point prediction)
        'W_out': np.random.randn(d_model, len(horizons)) / np.sqrt(d_model),
        'b_out': np.zeros(len(horizons)),
        # Quantile output heads
        'quantile_weights': {
            q: (np.random.randn(d_model, len(horizons)) / np.sqrt(d_model), np.zeros(len(horizons)))
            for q in qs
        },
    }
    
    model = {
        'weights': weights,
        'hyperparameters': {
            'seq_len': seq_len,
            'd_model': d_model,
            'num_heads': num_heads,
            'horizons': horizons,
            'quantiles': qs,
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

def attention_forward(X: np.ndarray, weights: Dict[str, np.ndarray], num_heads: int, causal: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """
    Multi-head attention forward pass with causal lower-triangular masking.
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
    
    if causal and seq_len > 1:
        # Lower-triangular causal mask: mask upper triangle (j > i) with -1e9
        mask = np.triu(np.ones((seq_len, seq_len), dtype=bool), k=1)
        scores = np.where(mask[None, None, :, :], -1e9, scores)

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
    Full forward pass including TFT gating and attention (point predictions).
    """
    weights = model['weights']
    hyperparams = model['hyperparameters']
    
    # 1. Gating
    gated_X = glu_forward(X, weights['W_gate1'], weights['b_gate1'])
    
    # 2. Attention
    attn_out, attn_weights = attention_forward(gated_X, weights, hyperparams['num_heads'])
    
    # 3. Residual & Layer Norm (Simplified, residual)
    X_res = X + attn_out
    
    # 4. Another gating
    final_features = glu_forward(X_res, weights['W_gate2'], weights['b_gate2'])
    
    # Extract the last sequence step features for forecasting
    last_step_features = final_features[:, -1, :] # (batch_size, d_model)
    
    # 5. Output predictions
    preds = last_step_features @ weights['W_out'] + weights['b_out'] # (batch_size, len(horizons))
    
    return preds, attn_weights


def fit_quantile_output_weights(
    H: np.ndarray,
    y: np.ndarray,
    quantiles: List[float] = [0.10, 0.50, 0.90],
) -> Dict[float, Tuple[np.ndarray, np.ndarray]]:
    """
    Fits output weights (W_out^(alpha), b_out^(alpha)) for each quantile alpha in quantiles
    using pinball loss minimization initialized from Ridge regression.
    
    Args:
        H: Feature representations from the TFT network (batch_size, d_model).
        y: Realized volatility targets (batch_size, n_horizons).
        quantiles: List of target quantiles (e.g. [0.10, 0.50, 0.90]).
        
    Returns:
        Dict mapping each quantile alpha -> (W_alpha, b_alpha),
        where W_alpha has shape (d_model, n_horizons) and b_alpha has shape (n_horizons,).
    """
    N, d_model = H.shape
    if y.ndim == 1:
        y = y.reshape(-1, 1)
    n_horizons = y.shape[1]
    
    # Ridge regression initialization for the base point predictions
    H_bias = np.hstack([H, np.ones((N, 1))])
    lambda_reg = 1e-3
    I_bias = np.eye(d_model + 1)
    I_bias[-1, -1] = 0.0 # Don't regularize bias
    
    try:
        w_ridge_all = np.linalg.solve(H_bias.T @ H_bias + lambda_reg * I_bias, H_bias.T @ y)
    except np.linalg.LinAlgError:
        w_ridge_all = np.linalg.pinv(H_bias.T @ H_bias + lambda_reg * I_bias) @ (H_bias.T @ y)
        
    fitted_quantiles: Dict[float, Tuple[np.ndarray, np.ndarray]] = {}
    
    for alpha in quantiles:
        W_alpha = np.zeros((d_model, n_horizons))
        b_alpha = np.zeros(n_horizons)
        
        for j in range(n_horizons):
            y_j = y[:, j]
            w_0 = w_ridge_all[:-1, j].copy()
            b_0 = float(w_ridge_all[-1, j])
            
            # Intercept shift based on residual quantile
            r_0 = y_j - (H @ w_0 + b_0)
            b_init = b_0 + float(np.quantile(r_0, alpha))
            theta_0 = np.append(w_0, b_init)
            
            def loss_and_grad(theta: np.ndarray) -> Tuple[float, np.ndarray]:
                w = theta[:-1]
                b = theta[-1]
                y_pred = H @ w + b
                e = y_j - y_pred
                p_loss = np.mean(np.maximum(alpha * e, (alpha - 1.0) * e))
                reg = 0.5 * 1e-4 * np.sum(w ** 2)
                
                # Subgradient
                grad_pred = -alpha + (e < 0).astype(float)
                grad_w = (H.T @ grad_pred) / N + 1e-4 * w
                grad_b = np.sum(grad_pred) / N
                grad = np.append(grad_w, grad_b)
                return p_loss + reg, grad
                
            try:
                res = minimize(
                    loss_and_grad,
                    theta_0,
                    method="L-BFGS-B",
                    jac=True,
                    options={"maxiter": 150, "ftol": 1e-7, "gtol": 1e-5},
                )
                init_loss = loss_and_grad(theta_0)[0]
                if res.success or res.fun <= init_loss:
                    theta_opt = res.x
                else:
                    theta_opt = theta_0
            except Exception as exc:
                logger.warning(
                    "fit_quantile_output_weights: L-BFGS-B optimization failed for quantile "
                    "alpha=%s, falling back to Ridge seed: %s",
                    alpha,
                    exc,
                )
                theta_opt = theta_0
                
            W_alpha[:, j] = theta_opt[:-1]
            b_alpha[j] = theta_opt[-1]
            
        fitted_quantiles[alpha] = (W_alpha, b_alpha)
        
    return fitted_quantiles


def train_vol_forecaster(
    X_train: np.ndarray,
    y_train: np.ndarray,
    seq_len: int,
    d_model: int,
    num_heads: int,
    horizons: List[int],
) -> Dict[str, Any]:
    """
    Trains the forecaster. Since this is pure numpy, we use an ELM (Extreme Learning Machine) approach:
    Freeze attention and gating weights as random projections, and fit the output layer via Ridge Regression.
    """
    model = build_tft_model(seq_len, d_model, num_heads, horizons)
    weights = model['weights']
    hyperparams = model['hyperparameters']
    
    # Forward pass to get last_step_features for all training data
    gated_X = glu_forward(X_train, weights['W_gate1'], weights['b_gate1'])
    attn_out, _ = attention_forward(gated_X, weights, hyperparams['num_heads'])
    X_res = X_train + attn_out
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
        W_out_bias = np.linalg.pinv(H_bias.T @ H_bias + lambda_reg * I_bias) @ (H_bias.T @ y_train)
        
    weights['W_out'] = W_out_bias[:-1, :]
    weights['b_out'] = W_out_bias[-1, :]
    
    return model


def train_quantile_vol_forecaster(
    X_train: np.ndarray,
    y_train: np.ndarray,
    seq_len: int,
    d_model: int,
    num_heads: int,
    horizons: List[int],
    quantiles: List[float] = [0.10, 0.50, 0.90],
) -> Dict[str, Any]:
    """
    Trains the multi-quantile TFT volatility forecaster.
    Fits point predictions (Ridge) and quantile output weights via pinball loss minimization.
    """
    model = build_tft_model(seq_len, d_model, num_heads, horizons, quantiles=quantiles)
    weights = model['weights']
    hyperparams = model['hyperparameters']
    
    # Forward pass to get last_step_features for all training data
    gated_X = glu_forward(X_train, weights['W_gate1'], weights['b_gate1'])
    attn_out, _ = attention_forward(gated_X, weights, hyperparams['num_heads'])
    X_res = X_train + attn_out
    final_features = glu_forward(X_res, weights['W_gate2'], weights['b_gate2'])
    H = final_features[:, -1, :] # (batch_size, d_model)
    
    # Fit point predictions (Ridge)
    lambda_reg = 1e-3
    H_bias = np.hstack([H, np.ones((H.shape[0], 1))])
    I_bias = np.eye(H_bias.shape[1])
    I_bias[-1, -1] = 0.0
    try:
        W_out_bias = np.linalg.solve(H_bias.T @ H_bias + lambda_reg * I_bias, H_bias.T @ y_train)
    except np.linalg.LinAlgError:
        W_out_bias = np.linalg.pinv(H_bias.T @ H_bias + lambda_reg * I_bias) @ (H_bias.T @ y_train)
    weights['W_out'] = W_out_bias[:-1, :]
    weights['b_out'] = W_out_bias[-1, :]
    
    # Fit quantile output heads
    quantile_weights = fit_quantile_output_weights(H, y_train, quantiles=quantiles)
    weights['quantile_weights'] = quantile_weights
    
    return model


def predict_multi_horizon_vol(X: np.ndarray, model: Dict[str, Any]) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    """
    Predicts multi-horizon volatility (point forecasts).
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


def predict_quantile_vol_cone(
    X: np.ndarray,
    model: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, np.ndarray]], np.ndarray]:
    """
    Predicts multi-horizon volatility quantile cone.
    
    Returns:
        forecasts: Dict mapping horizon label (e.g., '1d', '5d', '21d', '60d') to
                   a dictionary of quantile predictions (e.g., {'q10': ..., 'q50': ..., 'q90': ...}).
        attention_weights: The attention matrix from the forward pass.
    """
    weights = model['weights']
    hyperparams = model['hyperparameters']
    horizons = hyperparams['horizons']
    quantiles = hyperparams.get('quantiles', [0.10, 0.50, 0.90])
    
    # Forward pass to extract representation
    gated_X = glu_forward(X, weights['W_gate1'], weights['b_gate1'])
    attn_out, attn_weights = attention_forward(gated_X, weights, hyperparams['num_heads'])
    X_res = X + attn_out
    final_features = glu_forward(X_res, weights['W_gate2'], weights['b_gate2'])
    H = final_features[:, -1, :] # (batch_size, d_model)
    
    batch_size = X.shape[0]
    sorted_quantiles = sorted(quantiles)
    
    quantile_weights = weights.get('quantile_weights')
    raw_q_preds: Dict[float, np.ndarray] = {}
    
    if quantile_weights is not None:
        for q in sorted_quantiles:
            if q in quantile_weights:
                W_q, b_q = quantile_weights[q]
                raw_q_preds[q] = H @ W_q + b_q
            else:
                W_out = weights.get('W_out')
                b_out = weights.get('b_out')
                if W_out is not None:
                    point = H @ W_out + b_out
                    offset = (q - 0.5) * 0.5 * np.maximum(point, 0.05)
                    raw_q_preds[q] = point + offset
                else:
                    raw_q_preds[q] = np.zeros((batch_size, len(horizons)))
    else:
        W_out = weights.get('W_out', np.zeros((H.shape[1], len(horizons))))
        b_out = weights.get('b_out', np.zeros(len(horizons)))
        point = H @ W_out + b_out
        for q in sorted_quantiles:
            offset = (q - 0.5) * 0.5 * np.maximum(point, 0.05)
            raw_q_preds[q] = point + offset

    # Enforce quantile monotonicity: q10 <= q50 <= q90
    # Rearrangement operator: sort along the quantile axis
    q_matrix = np.stack([raw_q_preds[q] for q in sorted_quantiles], axis=-1)
    q_matrix_sorted = np.sort(q_matrix, axis=-1)
    q_matrix_sorted = np.maximum(q_matrix_sorted, 0.0) # Volatility must be non-negative
    
    forecasts: Dict[str, Dict[str, np.ndarray]] = {}
    for h_idx, h in enumerate(horizons):
        label = f"{h}d"
        forecasts[label] = {}
        for q_idx, q in enumerate(sorted_quantiles):
            q_label = f"q{int(round(q * 100))}"
            forecasts[label][q_label] = q_matrix_sorted[:, h_idx, q_idx]
            
    return forecasts, attn_weights


# ---------------------------------------------------------------------------
# Real, causal input construction (2026-08, closes audit finding F7).
# api/pilots_api.py::get_transformer_forecast previously fed this model
# np.random.randn(...) noise as "market history" -- these two functions
# replace that with a real OHLCV-derived, no-lookahead feature/window
# pipeline. Kept in this module (not api/pilots_api.py) so the causal
# invariant is directly unit-testable in tests/test_transformer_vol_forecaster.py
# without reaching into API-layer internals.
# ---------------------------------------------------------------------------

def _safe_zscore(value: pd.Series, mean: pd.Series, std: pd.Series) -> pd.Series:
    """Rolling z-score with this codebase's degenerate-std guard convention
    (CLAUDE.md: "any ratio that divides by a computed standard deviation ...
    must guard with < 1e-12, never an exact == 0 check"). A std within
    1e-12 of zero (a flat/near-constant window, e.g. a thinly-traded or
    placeholder-volume series) means "no real deviation" -- the z-score is
    defined as 0.0 there rather than exploding toward +/-inf or collapsing
    the whole feature to NaN forever. Warm-up rows (``mean`` still NaN
    because the rolling window isn't full yet) stay NaN, unchanged."""
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (value - mean) / std
    degenerate = std.abs() < 1e-12
    z = z.where(~degenerate, 0.0)
    return z.where(mean.notna())


_MACRO_DAILY_CADENCE_MAX_GAP_DAYS = 5.0  # tolerates weekends/holidays in a business-daily series


def _detect_low_frequency_macro_column(raw_series: pd.Series) -> bool:
    """True when *raw_series*'s own (non-NaN) observation cadence is a periodic
    aggregate release (weekly/monthly/quarterly), not business-daily.

    FRED daily/business-day series (VIX, yield-curve slope, credit-spread OAS
    variants, breakeven inflation) are published same-day or next-business-day
    -- contemporaneous use is correct and already how this codebase treats them
    (see regime/hmm_regime.py::build_feature_matrix's own docstring: "the
    regime classifier legitimately uses today's own close"). Periodic
    aggregate releases (e.g. UNRATE, a monthly-average FEDFUNDS series) are
    dated as the FIRST day of the period they summarize but are not actually
    published until roughly one full period later -- see
    scripts/refresh_validations.py::_reconstruct_macro_regime_series's
    existing "Lag UNRATE by 1 month" convention, which _align_macro_causal
    generalizes below (detected by cadence, not a hardcoded per-series id
    list, so a future lower-frequency series added to a caller's macro
    request list is lagged correctly by default instead of leaking).
    """
    obs = raw_series.dropna()
    if len(obs) < 2:
        return False
    gaps = obs.index.to_series().diff().dropna().dt.days
    if gaps.empty:
        return False
    return float(gaps.median()) > _MACRO_DAILY_CADENCE_MAX_GAP_DAYS


def _align_macro_causal(bars_index: pd.Index, macro_df: pd.DataFrame) -> pd.DataFrame:
    """
    Causally aligns macro series onto bars_index without lookahead.
    Any observation at date > t cannot affect the row at date t.

    Publication-lag handling: this function receives macro_df as a
    multi-column DataFrame whose index is the UNION across heterogeneous
    native cadences (e.g. a daily VIX column padded with NaN on the same
    union index as a monthly FEDFUNDS column -- callers build it via
    ``pd.DataFrame({sid: series, ...})`` from per-series
    ``HistoricalStore.get_macro()`` results, which omit dates with no real
    observation rather than padding NaN). Treating a low-frequency series'
    nominal FRED date as its *availability* date would leak a value up to
    ~1 period before it was actually published -- a real lookahead bug for
    e.g. a monthly UNRATE-style or FEDFUNDS-style aggregate.

    Each column is therefore handled independently: ``_detect_low_frequency_macro_column``
    inspects that column's own dropna'd native-cadence series, and a
    low-frequency column is shifted by one native observation
    (``.sort_index().shift(1)``) BEFORE the union/ffill step -- the exact
    generalization of scripts/refresh_validations.py::_reconstruct_macro_regime_series's
    existing "Lag UNRATE by 1 month" convention (a plain positional
    ``.shift(1)`` on a series with one row per native period moves each
    period's real value to the NEXT period's date-slot, i.e. exactly a
    1-period lag, before an as-of merge picks it up -- no calendar-day
    arithmetic needed). A business-daily-cadence column (VIX, yield-curve
    slope, credit-spread OAS variants, breakeven inflation) gets no
    additional shift and stays contemporaneously available, as before.
    """
    m_df = macro_df.copy()
    if not isinstance(m_df.index, pd.DatetimeIndex):
        date_col = next((c for c in ["Date", "date", "DATE", "datetime", "timestamp"] if c in m_df.columns), None)
        if date_col is not None:
            m_df[date_col] = pd.to_datetime(m_df[date_col])
            m_df = m_df.set_index(date_col)
        else:
            m_df.index = pd.to_datetime(m_df.index)

    bars_dt = pd.to_datetime(bars_index)
    macro_dt = pd.to_datetime(m_df.index)
    m_df.index = macro_dt
    macro_sorted = m_df.sort_index()

    # Per-column: detect cadence and apply the publication lag on that
    # column's own native (dropna'd) series, then union each column's
    # (possibly-lagged) index with bars_dt for the final reindex+ffill step.
    lagged_columns: Dict[str, pd.Series] = {}
    full_dt = bars_dt
    for col in macro_sorted.columns:
        native_series = macro_sorted[col].dropna()
        if _detect_low_frequency_macro_column(native_series):
            lagged_series = native_series.sort_index().shift(1)
        else:
            lagged_series = native_series
        lagged_columns[col] = lagged_series
        full_dt = full_dt.union(lagged_series.index)

    full_dt = full_dt.sort_values()
    macro_reindexed = pd.DataFrame(index=full_dt)
    for col, lagged_series in lagged_columns.items():
        macro_reindexed[col] = lagged_series.reindex(full_dt)
    macro_reindexed = macro_reindexed.ffill().reindex(bars_dt)
    macro_reindexed.index = bars_index
    return macro_reindexed


def build_causal_vol_features(
    bars: pd.DataFrame,
    macro_df: Optional[pd.DataFrame] = None,
    d_model: int = 32,
) -> pd.DataFrame:
    """
    Builds a real, causal (no-lookahead) per-day feature matrix from OHLCV
    bars and optional exogenous macro indicators. Every column is a rolling/shift-based
    statistic computed using only data up to and including that row's own date --
    never a value derived from a later date.

    When macro_df is provided, causal macro features (vix, yield_slope_10y_2y, hy_oas, fed_funds)
    are aligned and normalized with rolling _safe_zscore.

    The model architecture's ``d_model`` is fixed (e.g. 32), but this feature set
    may derive fewer signals than d_model. Remaining columns are explicit, deterministic
    ZERO padding (_pad_i, never random noise) -- documented here in the spirit of
    CONSTRAINT #4: an unused model input slot is inert (0.0), not a fabricated data point.

    Returns a DataFrame indexed the same as ``bars``, with exactly
    ``d_model`` float columns. Rows near the start of the series are NaN
    (rolling-window warm-up) -- callers should ``dropna()`` before use.
    """
    close = bars["Close"].astype(float)
    open_ = bars["Open"].astype(float) if "Open" in bars.columns else close
    high = bars["High"].astype(float) if "High" in bars.columns else close
    low = bars["Low"].astype(float) if "Low" in bars.columns else close
    volume = bars["Volume"].astype(float) if "Volume" in bars.columns else pd.Series(0.0, index=bars.index)

    ret_1d = close.pct_change()
    feats = pd.DataFrame(index=bars.index)
    feats["ret_1d"] = ret_1d
    feats["ret_5d"] = close.pct_change(5)
    feats["rvol_5"] = ret_1d.rolling(5).std() * np.sqrt(252)
    feats["rvol_10"] = ret_1d.rolling(10).std() * np.sqrt(252)
    feats["rvol_20"] = ret_1d.rolling(20).std() * np.sqrt(252)
    feats["rvol_60"] = ret_1d.rolling(60).std() * np.sqrt(252)
    feats["price_z_10"] = _safe_zscore(close, close.rolling(10).mean(), close.rolling(10).std())
    feats["price_z_20"] = _safe_zscore(close, close.rolling(20).mean(), close.rolling(20).std())
    feats["volume_z_20"] = _safe_zscore(volume, volume.rolling(20).mean(), volume.rolling(20).std())
    feats["hl_range"] = (high - low) / close
    feats["hl_range_mean_10"] = feats["hl_range"].rolling(10).mean()
    feats["oc_range"] = (close - open_) / open_.replace(0.0, np.nan)
    feats["skew_20"] = ret_1d.rolling(20).skew()
    feats["kurt_20"] = ret_1d.rolling(20).kurt()
    feats["momentum_10"] = close / close.shift(10) - 1.0
    feats["momentum_20"] = close / close.shift(20) - 1.0

    idx = bars.index if isinstance(bars.index, pd.DatetimeIndex) else pd.to_datetime(bars.index)
    dow = idx.dayofweek
    for i, name in enumerate(["dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri"]):
        feats[name] = (dow == i).astype(float)

    if macro_df is not None and not macro_df.empty:
        macro_aligned = _align_macro_causal(bars.index, macro_df)
        macro_aliases = {
            "vix": ["vix", "vixcls", "vix_close", "^vix"],
            "yield_slope_10y_2y": ["yield_slope_10y_2y", "t10y2y", "yield_curve_slope", "slope_10y_2y", "yield_slope", "10y2y"],
            "hy_oas": ["hy_oas", "bamlh0a0hym2", "credit_spread", "hy_spread", "oas"],
            "fed_funds": ["fed_funds", "fedfunds", "dff", "fed_funds_rate", "fed_rate"],
        }
        for col in macro_aligned.columns:
            s = macro_aligned[col].astype(float)
            col_str = str(col).lower()
            feats[f"macro_{col_str}"] = _safe_zscore(s, s.rolling(20).mean(), s.rolling(20).std())
            # Also populate canonical aliases if not already present
            for canon_name, aliases in macro_aliases.items():
                if col_str in aliases and f"macro_{canon_name}" not in feats.columns:
                    feats[f"macro_{canon_name}"] = feats[f"macro_{col_str}"]

    n_real = feats.shape[1]
    if n_real > d_model:
        # Defensive: shouldn't happen with the fixed feature list above, but
        # never silently truncate real features to fit -- fail loudly instead.
        raise ValueError(
            f"build_causal_vol_features produced {n_real} real features, more than "
            f"d_model={d_model}; increase d_model or trim the feature list."
        )
    for pad_i in range(n_real, d_model):
        feats[f"_pad_{pad_i}"] = 0.0

    return feats


def build_training_windows(
    feat_matrix: np.ndarray,
    close_arr: np.ndarray,
    seq_len: int,
    horizons: List[int],
    stride: int = 1,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Builds supervised (X, y) training windows from an already-causal feature
    matrix.

    A window ending at row index ``end_idx`` uses feature rows
    ``[end_idx - seq_len + 1, end_idx]`` as its INPUT (X) -- entirely data at
    or before ``end_idx``. Its label (y) for horizon ``h`` is the REAL
    realized annualized volatility of the ``h`` daily returns strictly AFTER
    ``end_idx`` (``close_arr[end_idx+1] .. close_arr[end_idx+h]``), never
    including ``end_idx`` itself. Using a known FUTURE outcome as a
    supervised-learning label is standard practice and not the same thing as
    a lookahead leak into the model's own INPUT features -- at live inference
    time only a window with no future data available is ever used (see the
    caller in api/pilots_api.py, which passes the single most-recent window
    to ``predict_multi_horizon_vol`` and never a windows returned here).

    A window whose full future horizon isn't available in ``close_arr`` is
    excluded entirely, never padded with a fabricated value.

    Returns
    -------
    (X_train, y_train, end_indices) -- ``end_indices[k]`` is the row index of
    ``X_train[k]``'s last (most recent) day. Useful for a causal-perturbation
    test: mutating ``feat_matrix``/``close_arr`` strictly AFTER
    ``end_indices[k]`` must never change ``X_train[k]``.
    """
    n = feat_matrix.shape[0]
    max_horizon = max(horizons)
    first_end = seq_len - 1
    last_end = n - 1 - max_horizon

    ret_1d = np.diff(close_arr) / close_arr[:-1]  # ret_1d[i] = return from day i to day i+1

    X_list: List[np.ndarray] = []
    y_list: List[List[float]] = []
    end_indices: List[int] = []
    for end_idx in range(first_end, last_end + 1, max(1, stride)):
        window = feat_matrix[end_idx - seq_len + 1: end_idx + 1]
        y_row = []
        valid_row = True
        for h in horizons:
            future_rets = ret_1d[end_idx: end_idx + h]
            if len(future_rets) != h:
                valid_row = False
                break
            y_row.append(float(np.std(future_rets, ddof=0) * np.sqrt(252)))
        if not valid_row:
            continue
        X_list.append(window)
        y_list.append(y_row)
        end_indices.append(end_idx)

    if not X_list:
        return (
            np.zeros((0, seq_len, feat_matrix.shape[1])),
            np.zeros((0, len(horizons))),
            np.zeros((0,), dtype=int),
        )
    return np.stack(X_list, axis=0), np.array(y_list), np.array(end_indices, dtype=int)
