"""Standalone CNN-LSTM fit/predict worker -- safe to run in a fresh subprocess.

Import order is the entire reason this module exists -- see
docs/known_issues/cnn_lstm_tf_deadlock.md. TensorFlow and pyarrow each ship an
independently-compiled copy of the same Abseil sync primitive; whichever
library's Python-level init runs first in a given PROCESS wins that symbol,
and if pandas/pyarrow initialize first, the first real multi-threaded TF
eager op (a Conv1D/LSTM ``.fit()``, not a trivial op) deadlocks forever.
``forecasting_engine.py``'s own import reorder (tensorflow before pandas)
only protects a process where that module is the first thing to touch
pandas -- true in an isolated test script, false in this codebase's real
entry points (main.py / main_orchestrator.py / pipeline/production_steps.py
all import pandas well before forecasting_engine is ever reached).

This module is the payload run inside a genuinely fresh ``multiprocessing``
"spawn" worker process (see cnn_lstm_process_pool.py), where import order is
scoped per-process and therefore fully controllable regardless of what the
parent process already imported. To keep that guarantee:

* This is the module's OWN first import, before anything else (including
  stdlib modules that are safe on their own merits, kept this way anyway so
  nobody has to re-derive which stdlib imports are "safe" later).
* NEVER import pandas, forecasting_engine, or the ``forecasting`` package
  here -- ``forecasting/__init__.py`` eagerly imports
  ``forecasting.forecast_tracker``, which imports pandas, so importing
  anything under that package would silently reintroduce the exact ordering
  bug this module exists to avoid. This is why this file lives at the repo
  root (flat module convention) rather than inside forecasting/.
* All inputs/outputs are plain numpy arrays and JSON-safe primitives -- no
  DataFrame ever crosses the process boundary, so unpickling an argument can
  never trigger a pandas import either.
"""

try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential, load_model
    from tensorflow.keras.layers import Conv1D, LSTM, Dense, MaxPooling1D
    from tensorflow.keras.callbacks import EarlyStopping
    TENSORFLOW_AVAILABLE = True
except ImportError:
    TENSORFLOW_AVAILABLE = False

import numpy as np
from typing import Any, Dict, Optional, Tuple


def _purged_train_val_split(
    X_seq: np.ndarray,
    Y_seq: np.ndarray,
    lookback: int,
    val_fraction: float = 0.2,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Mirrors ForecastingEngine.purged_train_val_split (forecasting_engine.py)
    exactly -- pure numpy, duplicated rather than imported because this module
    must never import forecasting_engine (see module docstring). Purges the
    lookback-1 training windows whose raw row span overlaps the first
    validation window's span, instead of Keras's own unpurged
    validation_split=0.2, which would leave the last lookback-1 training
    windows overlapping the first validation windows almost entirely.
    """
    n_total = len(X_seq)
    n_val = max(1, int(round(n_total * val_fraction)))
    val_start = max(0, n_total - n_val)
    embargo = max(0, lookback - 1)
    train_end = val_start - embargo
    if train_end <= 0:
        train_end = val_start
    return X_seq[:train_end], Y_seq[:train_end], X_seq[val_start:], Y_seq[val_start:]

# Fixed seed for reproducible CNN-LSTM weight init / dropout / validation-split
# shuffling. Re-applied at the top of every fit call (not just once at import
# time) so reproducibility holds per-ticker, per-call -- not just for whichever
# ticker happens to train first in a given worker process.
CNN_LSTM_RANDOM_SEED = 42


def fit_predict_cnn_lstm(
    X_seq: np.ndarray,
    Y_seq: np.ndarray,
    last_window: np.ndarray,
    num_horizons: int,
    keras_save_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build, compile, fit, and predict the direct multi-step CNN-LSTM model.

    Mirrors ForecastingEngine.run_cnn_lstm_forecast's architecture exactly
    (Conv1D -> MaxPooling1D -> LSTM -> Dense, Adam/MSE, 50 epochs with
    EarlyStopping(patience=5) on a purged internal train/val split -- see
    _purged_train_val_split) so isolating this into a subprocess is
    behavior-preserving, not a second implementation to keep in sync by hand
    -- only WHERE it runs changes.

    Pure numpy in, JSON-safe dict out -- safe to submit to a
    ProcessPoolExecutor and pickle across the process boundary. Raises on
    failure; the caller (ForecastingEngine.run_cnn_lstm_forecast) already
    wraps this in a try/except that degrades to the zero-result sentinel
    (CONSTRAINT #6) -- this function does not need its own fallback.
    """
    if not TENSORFLOW_AVAILABLE:
        raise RuntimeError("tensorflow is not importable in this worker process")

    np.random.seed(CNN_LSTM_RANDOM_SEED)
    tf.random.set_seed(CNN_LSTM_RANDOM_SEED)

    _, time_steps, num_features = X_seq.shape
    model = Sequential([
        Conv1D(filters=32, kernel_size=3, activation='relu',
               input_shape=(time_steps, num_features)),
        MaxPooling1D(pool_size=2),
        LSTM(units=30, activation='tanh', return_sequences=False),
        Dense(units=num_horizons),
    ])
    model.compile(optimizer='adam', loss='mse')
    early_stop = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
    X_tr, Y_tr, X_val, Y_val = _purged_train_val_split(X_seq, Y_seq, time_steps)
    model.fit(
        X_tr, Y_tr,
        validation_data=(X_val, Y_val),
        epochs=50, batch_size=16, verbose=0,
        callbacks=[early_stop],
    )
    pred_scaled = model.predict(last_window, verbose=0)[0]

    saved = False
    if keras_save_path is not None:
        model.save(keras_save_path)
        saved = True

    return {"pred_scaled": [float(x) for x in pred_scaled], "saved": saved}


def load_predict_cnn_lstm(
    keras_path: str,
    last_window: np.ndarray,
    num_horizons: int,
) -> Dict[str, Any]:
    """Load a persisted .keras model and predict on ``last_window``.

    Mirrors the cached-model inference branch of
    ForecastingEngine.run_cnn_lstm_forecast. Raises (horizon-count mismatch,
    corrupt artifact, missing file) rather than degrading internally -- the
    caller already treats any exception here as "refit from scratch"
    (CONSTRAINT #6), matching the pre-isolation behavior exactly.
    """
    if not TENSORFLOW_AVAILABLE:
        raise RuntimeError("tensorflow is not importable in this worker process")

    model = load_model(keras_path)
    if model.output_shape[-1] != num_horizons:
        raise ValueError("cached model horizon count mismatch")
    pred_scaled = model.predict(last_window, verbose=0)[0]
    return {"pred_scaled": [float(x) for x in pred_scaled]}
