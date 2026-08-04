"""Standalone CNN-LSTM fit/predict worker -- runs as its own ``__main__``
process (see cnn_lstm_process_pool.py), never imported as a library module by
the isolated code path.

Import order is the original reason this module exists -- see
docs/known_issues/cnn_lstm_tf_deadlock.md's Rounds 1-7. TensorFlow and
pyarrow each ship an independently-compiled copy of the same Abseil sync
primitive; whichever library's Python-level init runs first in a given
PROCESS wins that symbol, and if pandas/pyarrow initialize first, the first
real multi-threaded TF eager op (a Conv1D/LSTM ``.fit()``, not a trivial op)
deadlocks forever. ``forecasting_engine.py``'s own import reorder
(tensorflow before pandas) only protects a process where that module is the
first thing to touch pandas -- true in an isolated test script, false in
this codebase's real entry points (main.py / main_orchestrator.py /
pipeline/production_steps.py all import pandas well before
forecasting_engine is ever reached).

Round 8 (2026-08, see docs/known_issues/cnn_lstm_tf_deadlock.md) found a
SECOND, distinct deadlock that survives even genuine process isolation:
merely running this module's TF-touching code from inside a
``multiprocessing`` (``ProcessPoolExecutor`` OR a bare ``Process``) worker
process -- whether via a separate ``initializer=`` callback, implicit
unpickling of a function reference, or an explicit lazy ``import`` inside a
task -- reliably deadlocks the next real TF op that process runs, even with
pandas/pyarrow never imported in that process at all (ruling out the
Abseil ODR collision as the cause of THIS deadlock). The one pattern that
worked reliably across every repeated trial, including the real training
shape (epochs=50, hidden_dim=32): TensorFlow imported as top-level code of
the process's own ``__main__`` script -- i.e. a genuine, separate OS process
launched via ``subprocess.Popen`` running THIS FILE directly, not
``multiprocessing``'s spawn+pickle machinery. That is why
cnn_lstm_process_pool.py launches this module with
``subprocess.Popen([sys.executable, __file__])`` and this file's own
``if __name__ == "__main__":`` block (bottom of file) is the actual
long-lived worker entry point, communicating over its own stdin/stdout via a
small pickle-framed job protocol -- not ``multiprocessing`` at all.

To keep the (still load-bearing, Round 1-7) import-order guarantee:

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


def fit_predict_or_infer_lstm(
    X_seq: np.ndarray,
    Y_seq: Optional[np.ndarray],
    predict_X_seq: np.ndarray,
    hidden_dim: int,
    weights: Optional[list] = None,
) -> Dict[str, Any]:
    """Single-layer many-to-one LSTM regressor: ``LSTM(hidden_dim) -> Dense(1)``.

    Genuine backbone for ml.models.sf_garch_lstm.SFGarchLSTMModel -- reuses
    this module's existing TF-import-order isolation (see module docstring)
    rather than adding a second TF entry point, since the deadlock this
    module exists to avoid is a process-wide constraint, not specific to the
    CNN-LSTM forecaster.

    Two modes, one function (kept as one so the architecture-construction
    code can never drift out of sync between the two call sites):
      - ``weights is None`` (fit): trains from scratch on ``X_seq``/``Y_seq``
        via the same purged train/val split + EarlyStopping convention as
        fit_predict_cnn_lstm, then predicts on ``predict_X_seq`` and returns
        the trained weights (nested lists -- JSON/pickle safe) so the caller
        can persist them without ever holding a live Keras model in the
        parent process.
      - ``weights is not None`` (inference-only): skips training entirely,
        loads the given weights into a freshly-built identical architecture,
        and predicts on ``predict_X_seq``. Deterministic given fixed weights
        (no dropout/training-mode randomness), which is what makes a
        save-then-reload predict() round-trip reproduce exactly.

    ``X_seq``/``predict_X_seq`` shape: (n, sequence_length, n_features).
    ``Y_seq`` shape: (n,). Returns ``predictions`` (list[float], one per row
    of ``predict_X_seq``) and ``weights`` (the model's current weights,
    trained or passed-through, so a caller can always re-persist the latest
    state uniformly).
    """
    if not TENSORFLOW_AVAILABLE:
        raise RuntimeError("tensorflow is not importable in this worker process")

    np.random.seed(CNN_LSTM_RANDOM_SEED)
    tf.random.set_seed(CNN_LSTM_RANDOM_SEED)

    _, time_steps, num_features = X_seq.shape if weights is None else predict_X_seq.shape
    model = Sequential([
        LSTM(units=hidden_dim, activation='tanh', return_sequences=False,
             input_shape=(time_steps, num_features)),
        Dense(units=1),
    ])
    model.compile(optimizer='adam', loss='mse')

    if weights is None:
        if Y_seq is None:
            raise ValueError("Y_seq is required when weights is None (fit mode)")
        early_stop = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
        X_tr, Y_tr, X_val, Y_val = _purged_train_val_split(X_seq, Y_seq, time_steps)
        model.fit(
            X_tr, Y_tr,
            validation_data=(X_val, Y_val),
            epochs=50, batch_size=16, verbose=0,
            callbacks=[early_stop],
        )
    else:
        model.set_weights([np.asarray(w) for w in weights])

    preds = model.predict(predict_X_seq, verbose=0).reshape(-1)
    return {
        "predictions": [float(x) for x in preds],
        "weights": [w.tolist() for w in model.get_weights()],
    }


def _test_add(a: float, b: float) -> float:
    """TensorFlow-free helper dispatchable by name, for exercising the real
    subprocess/pipe mechanics in tests/test_cnn_lstm_process_pool.py without
    needing TensorFlow installed. Lives here (not in the test file) because
    the worker only ever resolves dispatchable functions from its own
    ``__main__`` namespace -- see this module's docstring (Round 8) for why
    a cross-module import inside the worker is unsafe for TF-touching code;
    it is unnecessary for this trivial one, but keeping every dispatchable
    name in one place avoids a second, inconsistent resolution path."""
    return a + b


def _test_sleep_and_return(seconds: float, value: Any) -> Any:
    """See ``_test_add``. Used to exercise ``_PopenWorker.call``'s timeout."""
    import time
    time.sleep(seconds)
    return value


def _test_raise_value_error(message: str) -> None:
    """See ``_test_add``. Used to exercise exception propagation from a
    still-healthy worker (as opposed to a broken/killed one)."""
    raise ValueError(message)


_DISPATCHABLE = {
    "fit_predict_cnn_lstm": fit_predict_cnn_lstm,
    "load_predict_cnn_lstm": load_predict_cnn_lstm,
    "fit_predict_or_infer_lstm": fit_predict_or_infer_lstm,
    "_test_add": _test_add,
    "_test_sleep_and_return": _test_sleep_and_return,
    "_test_raise_value_error": _test_raise_value_error,
}


def _run_worker_loop() -> None:
    """Persistent job loop for this module's ``subprocess.Popen``-launched
    worker process (see cnn_lstm_process_pool.py and this module's own
    docstring for why -- Round 8's real-op-in-``__main__`` requirement).

    Protocol: one ``(func_name, args)`` job in via stdin, one
    ``(ok, result_or_exception)`` response out via stdout, both pickle-framed
    (``pickle.dump``/``pickle.load`` on a shared stream naturally delimit a
    sequence of objects -- no extra length-prefixing needed). Loops until
    stdin hits EOF (the parent closed its write end -- pool shutdown) or a
    malformed job is received (also treated as EOF: a pickle stream can't be
    resynchronized after a framing error, so continuing risks silently
    misattributing a later response to the wrong job -- better to exit and
    let the parent detect the dead worker and spawn a fresh one).

    Never writes anything else to stdout -- that would corrupt the pickle
    framing. TensorFlow's own native/absl logging goes to stderr by default
    and is left untouched.
    """
    import pickle
    import sys

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        try:
            job = pickle.load(stdin)
        except EOFError:
            break
        except Exception:  # noqa: BLE001 -- unrecoverable framing error, stop
            break

        func_name, args = job
        func = _DISPATCHABLE.get(func_name)
        try:
            if func is None:
                raise ValueError(f"unknown function {func_name!r} requested of cnn_lstm_worker")
            result = func(*args)
            response = (True, result)
        except Exception as exc:  # noqa: BLE001 -- report the failure back, never crash the loop
            response = (False, exc)

        try:
            pickle.dump(response, stdout)
        except Exception:  # noqa: BLE001 -- response itself failed to pickle (rare, exotic
            # exception state); fall back to a definitely-picklable substitute so the
            # parent still gets a timely answer instead of a silent timeout.
            import traceback
            ok, payload = response
            fallback_exc = RuntimeError(
                f"{type(payload).__name__ if not ok else 'result'} failed to pickle: "
                f"{traceback.format_exc()}"
            )
            pickle.dump((False, fallback_exc), stdout)
        stdout.flush()


if __name__ == "__main__":
    _run_worker_loop()
