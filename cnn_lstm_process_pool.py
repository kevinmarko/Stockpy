"""Persistent worker pool for isolated CNN-LSTM execution.

Fix for the CNN-LSTM/TensorFlow deadlock (issue #381,
docs/known_issues/cnn_lstm_tf_deadlock.md, "Round 4"/"Round 5"): the deadlock
is triggered by process-wide import order (TensorFlow initializing after
pandas/pyarrow), and that constraint operates at PROCESS scope -- a module
importing tensorflow first cannot undo an ordering already set by something
else earlier in the same process. Running the actual TF-touching work in a
genuinely separate OS process sidesteps the constraint entirely, because each
process gets its own independent import order.

Workers are launched with ``subprocess.Popen``, not ``multiprocessing`` --
this is load-bearing, not a style choice. Round 8 (2026-08, see the
known-issues doc) found a SECOND, distinct deadlock: running TensorFlow work
inside a ``multiprocessing``-managed worker process (``ProcessPoolExecutor``
with or without an ``initializer=``, or a bare ``multiprocessing.Process``)
reliably deadlocks the next real TF op that process runs, REGARDLESS of
import order, warm-up ops, or whether pandas/pyarrow were ever imported in
that process at all -- ruling out the Round 1-7 Abseil ODR collision as the
cause of this one. The one pattern that worked reliably across every
repeated trial, including the real training shape (50 epochs,
hidden_dim=32): TensorFlow imported as top-level code of the process's own
``__main__`` script. ``subprocess.Popen([sys.executable, "cnn_lstm_worker.py"])``
gives each worker exactly that -- a genuine OS process where
cnn_lstm_worker.py IS ``__main__``, communicating over its own stdin/stdout
via a small pickle-framed job protocol (cnn_lstm_worker._run_worker_loop)
instead of ``multiprocessing``'s spawn+pickle machinery.

Workers are persistent (one warm pool, reused across tickers and cycles)
because starting a fresh interpreter and importing TensorFlow is expensive
(multi-second); paying that cost once per worker instead of once per ticker
matters given pipeline/production_steps.py fans CNN-LSTM calls out across the
whole symbol universe.
"""

import atexit
import logging
import os
import pickle
import queue
import subprocess
import sys
import threading
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("CNNLSTMProcessPool")

_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cnn_lstm_worker.py")


class BrokenWorkerPool(RuntimeError):
    """Raised when a worker process dies or its pipe breaks -- distinct from
    an exception the submitted function itself raised (which propagates with
    its own real type; the worker that reported it is still healthy and
    stays in the pool)."""


class _PopenWorker:
    """One persistent ``cnn_lstm_worker.py`` subprocess. Serializes access
    with its own lock so a single worker only ever processes one job at a
    time (the pickle stream has no per-job identifier -- see
    cnn_lstm_worker._run_worker_loop's docstring -- so two concurrent jobs on
    the same worker would race on which response belongs to which call)."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, _WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # inherit -- TensorFlow's own logging goes to stderr by default;
            # don't swallow it, and don't share stdout's pickle-framed channel with it.
        )
        self._lock = threading.Lock()

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def call(self, func_name: str, args: Tuple[Any, ...], timeout_seconds: float) -> Any:
        """Run ``func_name(*args)`` in this worker and return its result.

        Raises the worker's own exception type directly if the submitted
        function failed (the worker is still healthy, still reusable).
        Raises ``BrokenWorkerPool``/``TimeoutError`` if the worker itself is
        unresponsive or dead -- callers must treat the worker as unusable
        afterward (see _WorkerPool.call, which kills and replaces it): a
        timed-out job may still complete later and write a stale response
        into the pipe, which would otherwise be misattributed to the next
        job sent to the same worker.
        """
        with self._lock:
            if not self.is_alive():
                raise BrokenWorkerPool("worker process is not running")
            try:
                pickle.dump((func_name, args), self.proc.stdin)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise BrokenWorkerPool(f"worker pipe broken on send: {exc}") from exc

            result_box: dict = {}

            def _reader() -> None:
                try:
                    # Bandit B301: this deserializes the WORKER SUBPROCESS'S own
                    # stdout pipe -- a local, same-user, same-trust-boundary
                    # process this parent itself spawned (see the module
                    # docstring), never network/attacker-controlled data.
                    result_box["value"] = pickle.load(self.proc.stdout)  # nosec B301
                except (EOFError, OSError) as exc:
                    result_box["error"] = exc

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()
            reader_thread.join(timeout=timeout_seconds)

            if reader_thread.is_alive():
                raise TimeoutError(f"worker did not respond within {timeout_seconds}s")
            if "error" in result_box:
                raise BrokenWorkerPool(f"worker pipe broken on receive: {result_box['error']}")

            ok, payload = result_box["value"]
            if ok:
                return payload
            raise payload

    def terminate(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 -- best-effort cleanup, never let this raise
            pass


class _WorkerPool:
    """A fixed-size set of persistent ``_PopenWorker``s, dispatched via a
    bounded queue so concurrent callers (pipeline/production_steps.py's
    per-ticker ThreadPoolExecutor fan-out) block for a free worker instead of
    oversubscribing the configured worker count."""

    def __init__(self, max_workers: int) -> None:
        self._max_workers = max_workers
        self._available: "queue.Queue[_PopenWorker]" = queue.Queue()
        for _ in range(max_workers):
            self._available.put(_PopenWorker())

    def call(self, func_name: str, args: Tuple[Any, ...], timeout_seconds: float) -> Any:
        worker = self._available.get()
        try:
            result = worker.call(func_name, args, timeout_seconds)
        except (BrokenWorkerPool, TimeoutError):
            # This worker may still be mid-job (a stale response could land
            # later and corrupt the next call's framing) -- kill it outright
            # and replace it with a fresh one rather than trust it again.
            worker.terminate()
            self._available.put(_PopenWorker())
            raise
        except Exception:
            # The submitted function itself raised -- the worker answered
            # correctly and is still healthy, so it goes back in the pool.
            self._available.put(worker)
            raise
        else:
            self._available.put(worker)
            return result

    def shutdown(self) -> None:
        # Drain whatever workers are currently available and kill them.
        # Workers checked out mid-call (another thread's in-flight job) are
        # not reachable from here; they terminate on their own once that
        # call returns or times out, same as before this rewrite.
        while True:
            try:
                worker = self._available.get_nowait()
            except queue.Empty:
                break
            worker.terminate()


_lock = threading.Lock()
_pool: Optional[_WorkerPool] = None
_pool_workers: Optional[int] = None


def get_pool(max_workers: int) -> _WorkerPool:
    """Return the shared pool, (re)creating it if the worker count changed."""
    global _pool, _pool_workers
    with _lock:
        if _pool is not None and _pool_workers == max_workers:
            return _pool
        if _pool is not None:
            _pool.shutdown()
        _pool = _WorkerPool(max_workers)
        _pool_workers = max_workers
        return _pool


def reset_pool() -> None:
    """Tear down the current pool, if any. Called after a BrokenWorkerPool
    so the next call gets a fresh pool instead of repeatedly failing against
    a dead one; also usable directly by tests/shutdown hooks."""
    global _pool, _pool_workers
    with _lock:
        if _pool is not None:
            _pool.shutdown()
        _pool = None
        _pool_workers = None


atexit.register(reset_pool)


def run_in_subprocess(
    func: Callable[..., Any],
    args: Tuple[Any, ...],
    timeout_seconds: float,
    max_workers: int,
) -> Any:
    """Run ``func(*args)`` in the persistent worker pool and block for the
    result.

    ``func`` must be one of the functions ``cnn_lstm_worker.py`` itself
    dispatches by name (see its ``_DISPATCHABLE`` table) -- only its
    ``__name__`` crosses the process boundary, resolved against that fixed
    table inside the worker's own ``__main__`` namespace, never via a fresh
    cross-module import inside the worker (that reintroduces the Round 8
    deadlock -- see this module's docstring).

    Raises on any failure (timeout, BrokenWorkerPool, an exception raised
    inside the worker) -- this module never fabricates a result. Callers are
    expected to already have a dead-letter-safe fallback around this call
    (ForecastingEngine.run_cnn_lstm_forecast's existing outer try/except
    degrades to the zero-result sentinel, per CONSTRAINT #6).

    A single dead/unresponsive worker is transparently killed and replaced
    by ``_WorkerPool.call`` itself -- the next call to this function reaches
    a fresh worker without needing ``reset_pool()``. ``reset_pool()`` stays
    available for tests/shutdown hooks that want to tear the whole pool down
    explicitly.
    """
    pool = get_pool(max_workers)
    return pool.call(func.__name__, args, timeout_seconds)
