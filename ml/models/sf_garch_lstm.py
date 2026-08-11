"""SF-GARCH-LSTM Predictive Model (Phase 5).

Real, three-component implementation of the named architecture:

1. **GARCH** -- a genuine GJR-GARCH(1,1) (Glosten-Jagannathan-Runkle 1993)
   fit via the ``arch`` library (already a required dependency -- see
   ``technical_options_engine.py::estimate_gjr_garch_volatility``, whose
   ``arch_model(..., vol='GARCH', p=1, o=1, q=1, dist='t')`` call this
   mirrors exactly). The fitted ``mu``/``omega``/``alpha[1]``/``gamma[1]``/
   ``beta[1]`` parameters are then applied through this module's OWN
   one-step-ahead recursive formula (``_gjr_garch_conditional_vol``), used
   identically at both fit and predict time, so the volatility feature a
   row sees during training is computed the same causal way it will be at
   inference -- no train/serve skew, and no lookahead (each sigma^2[t] is a
   forecast made from information available before observing return[t]).
2. **Sentiment Factor (SF)** -- an optional third input channel: when the
   caller's ``X`` includes a ``sentiment_score`` column (e.g. from
   ``signals/news_catalyst.py::score_headlines``'s real FinBERT scores,
   computed upstream -- this module does no NLP itself), it is folded into
   the sequence window alongside returns/GARCH-vol. Absent that column,
   the model runs on returns + GARCH-vol only -- degrading the feature set,
   never fabricating a sentiment reading.
3. **LSTM** -- a genuine single-layer ``LSTM(hidden_dim) -> Dense(1)``
   sequence regressor over ``sequence_length``-row windows
   (``cnn_lstm_worker.fit_predict_or_infer_lstm``), run through this
   codebase's existing subprocess-isolated worker pool
   (``cnn_lstm_process_pool.py``) rather than in-process TensorFlow --
   REQUIRED, not optional, here: this module is routinely imported into a
   process that has already imported pandas (e.g. this file's own test
   module), so the "import tensorflow before pandas" guard
   ``forecasting_engine.py`` uses is provably ineffective in that case (see
   ``docs/known_issues/cnn_lstm_tf_deadlock.md`` and ``cnn_lstm_worker.py``'s
   module docstring) -- only a genuinely separate OS process sidesteps the
   constraint.

Graceful degradation (CONSTRAINT #4/#6 -- never fabricate, never crash):
TensorFlow is an optional heavy dependency (``requirements-optional.txt``).
When the subprocess call fails for ANY reason (TensorFlow not installed,
pool/timeout failure, too little history for a meaningful train/val split),
``fit``/``predict`` fall back to the SAME closed-form ridge regression this
module used to run unconditionally -- now an honestly-documented fallback
tier, not the whole model. ``arch`` failing to converge degrades the GARCH
feature to a causal rolling standard deviation instead (never a constant).

Remaining, explicitly out of scope: this has NOT been registered in
``ml/registry.yaml`` or wired into any production caller -- doing so needs a
real ``validation/harness.py`` run against genuine market data (this
repo's sandboxed dev/CI environment has no live-market network access), not
a fabricated DSR/PBO/Sharpe entry. Until that run happens, treat this as a
real, testable architecture with no proven live track record yet.
"""

from typing import Optional, Dict, Any
from pathlib import Path
import logging

import numpy as np
import pandas as pd

from ml.models.base import Model

logger = logging.getLogger("SFGarchLSTMModel")

try:
    from arch import arch_model  # type: ignore
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False

# Shared with cnn_lstm_worker.py's own CNN-LSTM path -- one seed for every
# TF fit this codebase performs, not a per-model reinvention.
_LSTM_RANDOM_SEED = 42

# Below this many rows there isn't enough history for a purged train/val
# split (cnn_lstm_worker._purged_train_val_split) to leave a non-trivial
# training set -- skip straight to the ridge-only fallback rather than
# attempting a doomed LSTM fit.
_MIN_ROWS_FOR_LSTM = 30


def _gjr_garch_conditional_vol(
    returns_scaled: np.ndarray, mu: float, omega: float, alpha: float, gamma: float, beta: float,
) -> np.ndarray:
    """One-step-ahead GJR-GARCH(1,1) conditional variance path.

    sigma^2_t = omega + alpha*eps_{t-1}^2 + gamma*I(eps_{t-1}<0)*eps_{t-1}^2 + beta*sigma^2_{t-1}

    Genuinely causal: sigma^2[t] is computed BEFORE ``returns_scaled[t]`` is
    folded into the recursion (via ``prev_eps``/``prev_sigma2``), so it only
    ever depends on information available up to t-1 -- identical whether
    called during training feature-building or at predict time on unseen
    rows. Recursion warm-starts from the model's own unconditional variance
    (a standard, side-effect-free GARCH convention), not a persisted
    cross-call state, so predict() never depends on what fit() last saw.
    """
    n = len(returns_scaled)
    sigma2 = np.empty(n, dtype=float)
    denom = 1.0 - alpha - gamma / 2.0 - beta
    uncond_var = omega / denom if denom > 1e-6 else max(omega, 1e-6)
    if not np.isfinite(uncond_var) or uncond_var <= 0:
        uncond_var = max(omega, 1e-6)

    prev_sigma2 = float(uncond_var)
    prev_eps = 0.0
    for t in range(n):
        indicator = 1.0 if prev_eps < 0 else 0.0
        sigma2[t] = omega + alpha * prev_eps ** 2 + gamma * indicator * prev_eps ** 2 + beta * prev_sigma2
        prev_eps = float(returns_scaled[t]) - mu
        prev_sigma2 = sigma2[t]
    return sigma2


def _rolling_std_fallback(returns: pd.Series, window: int = 20) -> np.ndarray:
    """Causal volatility proxy used only when ``arch`` is unavailable or the
    GJR-GARCH fit fails to converge -- never a fabricated constant."""
    vol = returns.rolling(window, min_periods=2).std()
    vol = vol.bfill().fillna(returns.std() if returns.std() > 0 else 1e-6)
    return vol.to_numpy(dtype=float)


def _build_feature_matrix(X: pd.DataFrame, garch_params: Optional[Dict[str, float]]) -> np.ndarray:
    """[returns, garch_or_rolling_vol(, sentiment_score)] -- same function
    used at fit and predict time so features are computed identically."""
    returns = (X["returns"] if "returns" in X.columns else X.iloc[:, 0]).fillna(0.0)

    if garch_params is not None:
        vol = _gjr_garch_conditional_vol(
            (returns * 100.0).to_numpy(dtype=float),
            garch_params["mu"], garch_params["omega"],
            garch_params["alpha"], garch_params["gamma"], garch_params["beta"],
        )
        vol = np.sqrt(np.maximum(vol, 0.0)) / 100.0
    else:
        vol = _rolling_std_fallback(returns)

    channels = [returns.to_numpy(dtype=float), vol]
    if "sentiment_score" in X.columns:
        channels.append(X["sentiment_score"].fillna(0.0).to_numpy(dtype=float))
    return np.column_stack(channels)


def _make_sequences(features: np.ndarray, sequence_length: int) -> np.ndarray:
    """Many-to-one sliding windows: row i's window is features[i-L+1 : i+1]
    for i >= L-1 -- uses only current/past rows, never future ones."""
    n = len(features)
    if n < sequence_length:
        return np.empty((0, sequence_length, features.shape[1]))
    windows = np.stack([
        features[i - sequence_length + 1: i + 1] for i in range(sequence_length - 1, n)
    ])
    return windows


class SFGarchLSTMModel(Model):
    """Real GJR-GARCH + optional sentiment-factor + LSTM sequence regressor
    -- see module docstring for the exact architecture and its documented
    ridge-regression fallback tier."""

    def __init__(self, sequence_length: int = 10, hidden_dim: int = 32):
        self.sequence_length = sequence_length
        self.hidden_dim = hidden_dim
        self.garch_params: Optional[Dict[str, float]] = None
        self.lstm_weights: Optional[list] = None
        self.ridge_weights: Optional[np.ndarray] = None
        self.is_fitted = False

    def _fit_garch(self, returns: pd.Series) -> None:
        if not ARCH_AVAILABLE or len(returns) < 30:
            self.garch_params = None
            return
        try:
            model = arch_model(returns * 100.0, vol="GARCH", p=1, o=1, q=1, dist="t")
            res = model.fit(update_freq=0, disp="off")
            self.garch_params = {
                "mu": float(res.params["mu"]),
                "omega": float(res.params["omega"]),
                "alpha": float(res.params["alpha[1]"]),
                "gamma": float(res.params["gamma[1]"]),
                "beta": float(res.params["beta[1]"]),
            }
        except Exception as exc:  # noqa: BLE001 -- degrade, never crash (CONSTRAINT #6)
            logger.debug("SFGarchLSTMModel: GJR-GARCH fit failed (%s); using rolling-std fallback.", exc)
            self.garch_params = None

    def fit(self, X: pd.DataFrame, y: pd.Series, t1: Optional[pd.Series] = None) -> "SFGarchLSTMModel":
        if len(X) < self.sequence_length:
            raise ValueError(f"Insufficient history ({len(X)}) for sequence length {self.sequence_length}")

        returns = X["returns"] if "returns" in X.columns else X.iloc[:, 0]
        self._fit_garch(returns.fillna(0.0))

        features = _build_feature_matrix(X, self.garch_params)
        clean_y = y.fillna(0.0).to_numpy(dtype=float)

        # Ridge fallback tier -- always fit; used for TF-unavailable degrade
        # and for the sequence_length-1 head rows no window can cover.
        clean_X = np.nan_to_num(features, nan=0.0)
        reg = 1e-4 * np.eye(clean_X.shape[1])
        self.ridge_weights = np.linalg.pinv(clean_X.T @ clean_X + reg) @ clean_X.T @ clean_y

        self.lstm_weights = None
        if len(X) >= _MIN_ROWS_FOR_LSTM:
            X_seq = _make_sequences(features, self.sequence_length)
            Y_seq = clean_y[self.sequence_length - 1:]
            try:
                from cnn_lstm_process_pool import run_in_subprocess
                from cnn_lstm_worker import fit_predict_or_infer_lstm
                from settings import settings
                result = run_in_subprocess(
                    fit_predict_or_infer_lstm,
                    (X_seq, Y_seq, X_seq[-1:], self.hidden_dim, None),
                    timeout_seconds=settings.CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS,
                    max_workers=settings.CNN_LSTM_PROCESS_POOL_WORKERS,
                )
                self.lstm_weights = result["weights"]
            except Exception as exc:  # noqa: BLE001 -- degrade to ridge (CONSTRAINT #6)
                logger.debug("SFGarchLSTMModel: LSTM fit unavailable (%s); using ridge fallback.", exc)
                self.lstm_weights = None

        self.is_fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if not self.is_fitted or self.ridge_weights is None:
            return np.zeros(len(X))

        features = np.nan_to_num(_build_feature_matrix(X, self.garch_params), nan=0.0)
        ridge_preds = features @ self.ridge_weights
        n = len(X)
        head = self.sequence_length - 1

        if self.lstm_weights is None or n <= head:
            return np.asarray(ridge_preds, dtype=float)

        X_seq = _make_sequences(features, self.sequence_length)
        preds = np.array(ridge_preds, dtype=float)
        try:
            from cnn_lstm_process_pool import run_in_subprocess
            from cnn_lstm_worker import fit_predict_or_infer_lstm
            from settings import settings
            result = run_in_subprocess(
                fit_predict_or_infer_lstm,
                (None, None, X_seq, self.hidden_dim, self.lstm_weights),
                timeout_seconds=settings.CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS,
                max_workers=settings.CNN_LSTM_PROCESS_POOL_WORKERS,
            )
            preds[head:] = result["predictions"]
        except Exception as exc:  # noqa: BLE001 -- degrade to ridge (CONSTRAINT #6)
            logger.debug("SFGarchLSTMModel: LSTM inference unavailable (%s); using ridge fallback.", exc)
        return preds


if __name__ == "__main__":
    pass
