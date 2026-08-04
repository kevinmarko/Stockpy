"""
tests/test_cnn_lstm_process_pool.py
====================================
Exercises the actual subprocess.Popen plumbing in cnn_lstm_process_pool.py
(persistent worker reuse across calls, timeout handling, dead-worker
recovery) against plain, TensorFlow-free dispatchable helpers
(cnn_lstm_worker._test_add and friends). TensorFlow is an optional heavy
dependency (requirements-optional.txt) that may not be installed in every
dev/CI environment -- these tests validate the REAL subprocess mechanics
without needing it, since the pool machinery itself has nothing to do with
what function it runs. The helpers live in cnn_lstm_worker.py itself (not
here) because the worker only ever resolves dispatchable functions from its
own __main__ namespace -- see that module's docstring (Round 8 of
docs/known_issues/cnn_lstm_tf_deadlock.md) for why a cross-module import
inside the worker is unsafe for TF-touching code. cnn_lstm_worker.py's own
TF-dependent behavior is covered separately in tests/test_cnn_lstm_worker.py
(mocked TF, matching this repo's existing tests/test_forecasting_lookahead.py
convention).

Round 8 background: an earlier version of this module used
multiprocessing.ProcessPoolExecutor. That was replaced entirely after
discovering it reliably deadlocks the first real TensorFlow op a worker
process runs, regardless of import order or warm-up -- see
cnn_lstm_process_pool.py's own module docstring for the full writeup. These
tests exercise the subprocess.Popen-based replacement.
"""

from __future__ import annotations

import numpy as np
import pytest

import cnn_lstm_process_pool as pool_mod
from cnn_lstm_worker import (
    _test_add,
    _test_raise_value_error,
    _test_sleep_and_return,
    fit_predict_cnn_lstm,
    fit_predict_or_infer_lstm,
)


@pytest.fixture(autouse=True)
def _reset_pool_before_and_after():
    pool_mod.reset_pool()
    yield
    pool_mod.reset_pool()


class TestRunInSubprocess:
    def test_runs_real_function_in_a_separate_process(self):
        result = pool_mod.run_in_subprocess(_test_add, (2, 3), timeout_seconds=30, max_workers=1)
        assert result == 5

    def test_propagates_exceptions_raised_inside_the_worker(self):
        with pytest.raises(ValueError, match="boom"):
            pool_mod.run_in_subprocess(
                _test_raise_value_error, ("boom",), timeout_seconds=30, max_workers=1
            )

    def test_timeout_raises_and_does_not_hang_forever(self):
        with pytest.raises(TimeoutError):
            pool_mod.run_in_subprocess(
                _test_sleep_and_return, (5.0, 1), timeout_seconds=0.2, max_workers=1
            )

    def test_worker_survives_a_normal_exception_and_is_reused(self):
        """A ValueError raised BY the submitted function must not be treated
        as a broken worker -- the same underlying worker process should
        still answer the next call (see _WorkerPool.call's except-clause
        split between BrokenWorkerPool/TimeoutError and any other
        exception)."""
        pool = pool_mod.get_pool(max_workers=1)
        worker = pool._available.get()
        pool._available.put(worker)
        pid_before = worker.proc.pid

        with pytest.raises(ValueError):
            pool_mod.run_in_subprocess(
                _test_raise_value_error, ("boom",), timeout_seconds=30, max_workers=1
            )

        worker_after = pool._available.get()
        pool._available.put(worker_after)
        assert worker_after.proc.pid == pid_before

    def test_worker_is_replaced_after_a_timeout(self):
        """The opposite of the case above: a timeout means the worker may
        still be mid-job with a response that could land later and corrupt
        the next call's framing -- it must be killed and replaced, not
        reused."""
        pool = pool_mod.get_pool(max_workers=1)
        worker = pool._available.get()
        pool._available.put(worker)
        pid_before = worker.proc.pid

        with pytest.raises(TimeoutError):
            pool_mod.run_in_subprocess(
                _test_sleep_and_return, (5.0, 1), timeout_seconds=0.2, max_workers=1
            )

        worker_after = pool._available.get()
        pool._available.put(worker_after)
        assert worker_after.proc.pid != pid_before
        # The pool must still be usable after the replacement.
        result = pool_mod.run_in_subprocess(_test_add, (1, 1), timeout_seconds=30, max_workers=1)
        assert result == 2


class TestPoolLifecycle:
    def test_get_pool_launches_real_subprocess_workers(self):
        pool = pool_mod.get_pool(max_workers=1)
        assert isinstance(pool, pool_mod._WorkerPool)
        worker = pool._available.get()
        pool._available.put(worker)
        assert isinstance(worker, pool_mod._PopenWorker)
        assert worker.is_alive()

    def test_get_pool_reuses_the_same_pool_for_the_same_worker_count(self):
        pool_a = pool_mod.get_pool(max_workers=2)
        pool_b = pool_mod.get_pool(max_workers=2)
        assert pool_a is pool_b

    def test_get_pool_recreates_when_worker_count_changes(self):
        pool_a = pool_mod.get_pool(max_workers=1)
        pool_b = pool_mod.get_pool(max_workers=2)
        assert pool_a is not pool_b

    def test_reset_pool_forces_a_fresh_pool_on_next_use(self):
        pool_a = pool_mod.get_pool(max_workers=1)
        pool_mod.reset_pool()
        pool_b = pool_mod.get_pool(max_workers=1)
        assert pool_a is not pool_b

    def test_reset_pool_kills_the_underlying_worker_process(self):
        pool = pool_mod.get_pool(max_workers=1)
        worker = pool._available.get()
        pool._available.put(worker)
        pool_mod.reset_pool()
        assert not worker.is_alive()

    def test_multiple_calls_reuse_the_pool_across_submissions(self):
        results = [
            pool_mod.run_in_subprocess(_test_add, (i, 1), timeout_seconds=30, max_workers=1)
            for i in range(3)
        ]
        assert results == [1, 2, 3]

    def test_persistent_worker_is_not_respawned_between_calls(self):
        """The whole point of a persistent pool: the same OS process answers
        repeated calls, rather than paying a fresh-interpreter-plus-import
        cost every time."""
        pool = pool_mod.get_pool(max_workers=1)
        worker = pool._available.get()
        pool._available.put(worker)
        pid_before = worker.proc.pid

        pool_mod.run_in_subprocess(_test_add, (1, 1), timeout_seconds=30, max_workers=1)
        pool_mod.run_in_subprocess(_test_add, (2, 2), timeout_seconds=30, max_workers=1)

        worker_after = pool._available.get()
        pool._available.put(worker_after)
        assert worker_after.proc.pid == pid_before


class TestRealTensorFlowThroughThePool:
    """Regression coverage for the actual gap that let Round 8's deadlock
    ship undetected: every other test touching cnn_lstm_worker.py's TF
    functions either mocks TensorFlow entirely (tests/test_cnn_lstm_worker.py)
    or mocks run_in_subprocess itself (tests/test_cnn_lstm_isolation_dispatch.py)
    -- nothing exercised a REAL TensorFlow op through the REAL persistent
    pool. These tests do exactly that, with a bounded timeout so a
    regression here fails fast instead of hanging the suite. TensorFlow is
    an optional heavy dependency (requirements-optional.txt) NOT installed
    by CI or the base ./setup.sh -- unlike the rest of this file these tests
    are not TensorFlow-free by design, so each one skips via
    pytest.importorskip when it genuinely isn't available, rather than
    failing (the worker subprocess uses the same sys.executable as this
    test process, so that's an accurate proxy for whether it'll have
    TensorFlow too)."""

    def test_fit_predict_cnn_lstm_completes_through_the_real_pool(self):
        pytest.importorskip("tensorflow")
        rng = np.random.RandomState(0)
        n_samples, lookback, n_features, n_horizons = 40, 10, 3, 4
        X_seq = rng.rand(n_samples, lookback, n_features)
        Y_seq = rng.rand(n_samples, n_horizons)
        last_window = rng.rand(1, lookback, n_features)

        result = pool_mod.run_in_subprocess(
            fit_predict_cnn_lstm,
            (X_seq, Y_seq, last_window, n_horizons),
            timeout_seconds=60,
            max_workers=1,
        )

        assert len(result["pred_scaled"]) == n_horizons
        assert all(np.isfinite(x) for x in result["pred_scaled"])
        assert result["saved"] is False

    def test_fit_predict_or_infer_lstm_completes_through_the_real_pool(self):
        """Backbone for ml.models.sf_garch_lstm.SFGarchLSTMModel -- also
        covered end-to-end by tests/test_phase5_models.py::
        test_sf_garch_lstm_smoke, but that test wasn't written as pool-
        mechanism regression coverage; this one is, and isolates the pool
        call from SFGarchLSTMModel's own GARCH-fitting logic."""
        pytest.importorskip("tensorflow")
        rng = np.random.RandomState(0)
        n_samples, seq_len, n_features = 40, 10, 2
        X_seq = rng.rand(n_samples, seq_len, n_features)
        Y_seq = rng.rand(n_samples)
        predict_X_seq = rng.rand(5, seq_len, n_features)

        result = pool_mod.run_in_subprocess(
            fit_predict_or_infer_lstm,
            (X_seq, Y_seq, predict_X_seq, 8, None),
            timeout_seconds=60,
            max_workers=1,
        )

        assert len(result["predictions"]) == 5
        assert all(np.isfinite(x) for x in result["predictions"])
        assert len(result["weights"]) > 0
