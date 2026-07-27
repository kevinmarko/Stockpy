"""Persistent multiprocessing worker pool for isolated CNN-LSTM execution.

Fix for the CNN-LSTM/TensorFlow deadlock (issue #381,
docs/known_issues/cnn_lstm_tf_deadlock.md, "Round 4"/"Round 5"): the deadlock
is triggered by process-wide import order (TensorFlow initializing after
pandas/pyarrow), and that constraint operates at PROCESS scope -- a module
importing tensorflow first cannot undo an ordering already set by something
else earlier in the same process. Running the actual TF-touching work in a
genuinely separate OS process sidesteps the constraint entirely, because each
process gets its own independent import order.

``spawn`` (never the platform default -- ``fork`` on Linux) is required for
correctness here, not merely a style choice: ``fork()`` clones the parent's
already-initialized memory, including whatever process-wide state already
"lost" the pandas-vs-tensorflow import race, which would silently defeat the
entire fix. ``spawn`` starts a genuinely fresh interpreter that re-imports
everything from scratch, so cnn_lstm_worker.py's own tensorflow-first import
governs regardless of what the parent process already imported.

Workers are persistent (one warm pool, reused across tickers and cycles)
because starting a fresh interpreter and importing TensorFlow is expensive
(multi-second); paying that cost once per worker instead of once per ticker
matters given pipeline/production_steps.py fans CNN-LSTM calls out across the
whole symbol universe.
"""

import logging
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("CNNLSTMProcessPool")

_lock = threading.Lock()
_pool: Optional[ProcessPoolExecutor] = None
_pool_workers: Optional[int] = None


def _warm_worker() -> None:
    """ProcessPoolExecutor initializer: import the worker module (and, with
    it, tensorflow) as the very first thing each worker process does --
    before any task is ever unpickled -- rather than relying on the target
    callable's own module-resolution order during unpickling. Explicit is
    safer than implicit for a guarantee this load-bearing."""
    import cnn_lstm_worker  # noqa: F401


def get_pool(max_workers: int) -> ProcessPoolExecutor:
    """Return the shared pool, (re)creating it if the worker count changed."""
    global _pool, _pool_workers
    with _lock:
        if _pool is not None and _pool_workers == max_workers:
            return _pool
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
        ctx = multiprocessing.get_context("spawn")
        _pool = ProcessPoolExecutor(
            max_workers=max_workers, mp_context=ctx, initializer=_warm_worker
        )
        _pool_workers = max_workers
        return _pool


def reset_pool() -> None:
    """Tear down the current pool, if any. Called after a BrokenProcessPool
    so the next call gets a fresh pool instead of repeatedly failing against
    a dead one; also usable directly by tests/shutdown hooks."""
    global _pool, _pool_workers
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
        _pool_workers = None


def run_in_subprocess(
    func: Callable[..., Any],
    args: Tuple[Any, ...],
    timeout_seconds: float,
    max_workers: int,
) -> Any:
    """Submit ``func(*args)`` to the persistent pool and block for the result.

    Raises on any failure (timeout, BrokenProcessPool, an exception raised
    inside the worker) -- this module never fabricates a result. Callers are
    expected to already have a dead-letter-safe fallback around this call
    (ForecastingEngine.run_cnn_lstm_forecast's existing outer try/except
    degrades to the zero-result sentinel, per CONSTRAINT #6).
    """
    pool = get_pool(max_workers)
    future = pool.submit(func, *args)
    try:
        return future.result(timeout=timeout_seconds)
    except BrokenProcessPool:
        reset_pool()
        raise
