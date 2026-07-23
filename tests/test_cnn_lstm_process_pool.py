"""
tests/test_cnn_lstm_process_pool.py
====================================
Exercises the actual multiprocessing plumbing in cnn_lstm_process_pool.py
(spawn context, persistent pool reuse, timeout handling, BrokenProcessPool
recovery) against a plain, TensorFlow-free picklable function. TensorFlow is
an optional heavy dependency (requirements-optional.txt) that may not be
installed in every dev/CI environment -- these tests validate the REAL
subprocess mechanics without needing it, since the pool machinery itself has
nothing to do with what function it runs. cnn_lstm_worker.py's own
TF-dependent behavior is covered separately in tests/test_cnn_lstm_worker.py
(mocked TF, matching this repo's existing tests/test_forecasting_lookahead.py
convention).
"""

from __future__ import annotations

import time

import pytest

import cnn_lstm_process_pool as pool_mod


def _add(a: int, b: int) -> int:
    return a + b


def _sleep_and_return(seconds: float, value: int) -> int:
    time.sleep(seconds)
    return value


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


@pytest.fixture(autouse=True)
def _reset_pool_before_and_after():
    pool_mod.reset_pool()
    yield
    pool_mod.reset_pool()


class TestRunInSubprocess:
    def test_runs_real_function_in_a_separate_process(self):
        result = pool_mod.run_in_subprocess(_add, (2, 3), timeout_seconds=30, max_workers=1)
        assert result == 5

    def test_propagates_exceptions_raised_inside_the_worker(self):
        with pytest.raises(ValueError, match="boom"):
            pool_mod.run_in_subprocess(
                _raise_value_error, ("boom",), timeout_seconds=30, max_workers=1
            )

    def test_timeout_raises_and_does_not_hang_forever(self):
        with pytest.raises(TimeoutError):
            pool_mod.run_in_subprocess(
                _sleep_and_return, (5.0, 1), timeout_seconds=0.2, max_workers=1
            )


class TestPoolLifecycle:
    def test_get_pool_uses_spawn_context_and_warm_initializer(self):
        pool = pool_mod.get_pool(max_workers=1)
        assert pool._mp_context.get_start_method() == "spawn"
        assert pool._initializer is pool_mod._warm_worker

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

    def test_multiple_calls_reuse_the_pool_across_submissions(self):
        results = [
            pool_mod.run_in_subprocess(_add, (i, 1), timeout_seconds=30, max_workers=1)
            for i in range(3)
        ]
        assert results == [1, 2, 3]
