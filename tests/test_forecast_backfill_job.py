"""
tests/test_forecast_backfill_job.py
====================================
Tests for ml/forecast_backfill_job.py's launcher primitives (start_job,
get_job_state, get_active_job_id, cancel_job, serialize_job) using a STUB
worker script (tests/fixtures/forecast_backfill_worker_stub.py) in place of
the real ml/forecast_backfill_worker.py -- no real data fetching, feature
engineering, or model training happens anywhere in this file.

Patch point
-----------
ml.forecast_backfill_job.start_job builds its subprocess.Popen argv as
``[sys.executable, "-m", "ml.forecast_backfill_worker", "--params-fd", ...]``.
Mirrors tests/test_robinhood_login.py's ``_PopenProxy`` technique exactly:
rebind the `subprocess` NAME inside ml.forecast_backfill_job's own module
namespace to a thin proxy that forwards every subprocess.* attribute to the
real module EXCEPT Popen, which rewrites the "-m ml.forecast_backfill_worker"
argv pair to the stub script's path before delegating to the real
subprocess.Popen.

Speed
-----
FORECAST_BACKFILL_DEADLINE_SECONDS is monkeypatched to a sub-2-second value
(matching the house convention of patching the live `settings.settings`
singleton directly -- see tests/test_robinhood_login.py) so the whole file
runs in a few seconds, never anywhere near the real (settings.py-defined)
production default.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

import ml.forecast_backfill_job as forecast_backfill_job

_STUB_PATH = Path(__file__).parent / "fixtures" / "forecast_backfill_worker_stub.py"


class _PopenProxy:
    """Forwards every ``subprocess.*`` attribute to the real module except
    ``Popen``, which redirects the "-m ml.forecast_backfill_worker" argv
    pair to the stub script -- see module docstring above."""

    def __init__(self, real_module):
        self._real = real_module

    def __getattr__(self, name):
        return getattr(self._real, name)

    def Popen(self, argv, *args, **kwargs):
        assert argv[1:3] == ["-m", "ml.forecast_backfill_worker"], (
            f"unexpected argv shape -- patch point may have drifted: {argv}"
        )
        new_argv = [argv[0], str(_STUB_PATH)] + list(argv[3:])
        return self._real.Popen(new_argv, *args, **kwargs)


@pytest.fixture(autouse=True)
def _stub_worker(monkeypatch):
    """Redirect ml.forecast_backfill_job's own subprocess.Popen calls to
    launch the stub script instead of the real worker module."""
    monkeypatch.setattr(forecast_backfill_job, "subprocess", _PopenProxy(subprocess))


@pytest.fixture(autouse=True)
def _fast_deadline(monkeypatch):
    """A tiny deadline so a hung/wedged worker is reaped in ~1-2s instead of
    the real (settings.py-defined) production default."""
    monkeypatch.setattr("settings.settings.FORECAST_BACKFILL_DEADLINE_SECONDS", 1.5)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """ml.forecast_backfill_job's _jobs/_active_job_id are module-level
    globals -- reset them before AND after every test so job ids/single-flight
    state never leaks across tests (matching this module's own dead-letter/
    never-shared-mutable-state discipline elsewhere)."""
    forecast_backfill_job._jobs.clear()
    forecast_backfill_job._active_job_id = None
    yield
    forecast_backfill_job._jobs.clear()
    forecast_backfill_job._active_job_id = None


def _set_behavior(monkeypatch, behavior: str) -> None:
    monkeypatch.setenv("BACKFILL_STUB_BEHAVIOR", behavior)


def _wait_until_terminal(job, timeout: float = 10.0) -> None:
    """Poll a job's state until it leaves 'running', or fail the test."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with job._lock:
            if job.state != "running":
                return
        time.sleep(0.05)
    pytest.fail(f"job {job.job_id} did not reach a terminal state within {timeout}s")


# ---------------------------------------------------------------------------
# Successful job / phase progression
# ---------------------------------------------------------------------------


class TestStartJobSuccess:
    def test_start_job_returns_running_state_immediately(self, monkeypatch):
        """start_job must return WITHOUT waiting for the subprocess to
        finish -- the whole point of the async job design."""
        _set_behavior(monkeypatch, "hang")
        job = forecast_backfill_job.start_job({"tickers": ["AAPL"]})
        assert job is not None
        assert job.state == "running"
        assert job.job_id.startswith("backfill-")
        forecast_backfill_job.cancel_job(job.job_id)  # tidy up the hung child

    def test_job_progresses_through_all_seven_phases_to_succeeded(self, monkeypatch):
        _set_behavior(monkeypatch, "success")
        job = forecast_backfill_job.start_job({"tickers": ["AAPL", "MSFT"]})
        _wait_until_terminal(job)

        assert job.state == "succeeded"
        assert job.error is None
        assert job.error_type is None
        assert job.phase == "exporting"
        assert job.step == 7
        assert job.total_steps == 7
        assert job.summary == {"status": "completed", "total_rows": 3}
        assert job.sample_rows == 3

    def test_get_job_state_returns_the_same_job(self, monkeypatch):
        _set_behavior(monkeypatch, "success")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job)
        assert forecast_backfill_job.get_job_state(job.job_id) is job

    def test_get_job_state_unknown_id_returns_none(self):
        assert forecast_backfill_job.get_job_state("no-such-job") is None

    def test_params_cross_the_pipe_intact(self, monkeypatch, tmp_path):
        """The dict passed to start_job() must arrive at the worker verbatim
        (JSON round-trip) -- this is what would carry
        ForecastBackfillRunRequest.model_dump() to the real worker."""
        _set_behavior(monkeypatch, "echo_params")
        echo_path = tmp_path / "echoed_params.json"
        monkeypatch.setenv("STUB_ECHO_PATH", str(echo_path))

        params = {
            "tickers": ["AAPL", "MSFT"],
            "horizons": [10, 30],
            "strategy_ids": ["timeseries_momentum"],
            "theta_c": 0.6,
            "use_fmp": False,
        }
        job = forecast_backfill_job.start_job(params)
        _wait_until_terminal(job)

        assert job.state == "succeeded"
        echoed = json.loads(echo_path.read_text(encoding="utf-8"))
        assert echoed == params


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------


class TestFailureReporting:
    def test_value_error_result_is_reported_with_its_type(self, monkeypatch):
        _set_behavior(monkeypatch, "value_error")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job)

        assert job.state == "failed"
        assert job.error_type == "value_error"
        assert "technical features" in (job.error or "")

    def test_unexpected_error_result_is_reported_with_its_type(self, monkeypatch):
        _set_behavior(monkeypatch, "unexpected")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job)

        assert job.state == "failed"
        assert job.error_type == "unexpected"
        assert job.error == "boom"

    def test_worker_exit_without_result_is_an_honest_failure(self, monkeypatch):
        """EOF (clean child exit) with no terminal 'result' event ever
        observed must not leave the job stuck 'running' forever
        (CONSTRAINT #6 -- dead-letter, never silently hang)."""
        _set_behavior(monkeypatch, "no_result")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job)

        assert job.state == "failed"
        assert job.error == "Forecast backfill worker exited without reporting a result."
        assert job.error_type == "unexpected"


# ---------------------------------------------------------------------------
# Single-flight
# ---------------------------------------------------------------------------


class TestSingleFlight:
    def test_start_job_returns_none_while_one_is_already_running(self, monkeypatch):
        _set_behavior(monkeypatch, "hang")
        first = forecast_backfill_job.start_job({"tickers": ["AAPL"]})
        assert first is not None

        second = forecast_backfill_job.start_job({"tickers": ["MSFT"]})
        assert second is None
        assert forecast_backfill_job.get_active_job_id() == first.job_id

        forecast_backfill_job.cancel_job(first.job_id)

    def test_active_job_id_clears_on_completion_and_allows_a_new_run(self, monkeypatch):
        _set_behavior(monkeypatch, "success")
        first = forecast_backfill_job.start_job({})
        _wait_until_terminal(first)
        assert forecast_backfill_job.get_active_job_id() is None

        second = forecast_backfill_job.start_job({})
        assert second is not None
        assert second.job_id != first.job_id
        _wait_until_terminal(second)

    def test_get_active_job_id_is_none_when_nothing_has_run(self):
        assert forecast_backfill_job.get_active_job_id() is None

    def test_concurrent_start_job_calls_do_not_both_win(self, monkeypatch):
        """Genuinely concurrent start_job() calls, racing via real threads,
        must never both return a non-None job -- regression test for the
        TOCTOU window where `_active_job_id` was set inside one
        `_jobs_lock` critical section but the job's `_jobs` dict entry was
        only inserted later, in a SEPARATE critical section, AFTER
        subprocess.Popen() returned. A concurrent caller could observe the
        marker already set but the dict entry still missing, conclude it
        was stale, clear it, and claim the slot itself -- letting two
        training subprocesses run at once.

        Unlike test_start_job_returns_none_while_one_is_already_running
        above (which calls start_job(), waits for it to FULLY return, THEN
        calls it again -- never exercising the race window), this uses real
        threads plus a barrier so both calls genuinely overlap, plus an
        injected delay around subprocess.Popen() to widen whatever window
        the buggy code left between releasing the initial lock and
        re-acquiring it to insert into `_jobs`. With the fix (the marker
        and the dict insertion happening inside ONE critical section,
        before Popen is ever called), a second start_job() call blocks on
        the lock until the first is already fully registered and returns
        None immediately -- no window survives regardless of how slow
        Popen is, so this is not a timing-sensitive/flaky assertion."""
        _set_behavior(monkeypatch, "hang")

        real_popen = forecast_backfill_job.subprocess.Popen

        def _slow_popen(argv, *args, **kwargs):
            # Widens the (buggy code's) gap between the initial _jobs_lock
            # critical section and the later re-acquisition that used to
            # insert into _jobs, so a real race is reliably exercised
            # rather than depending on incidental thread scheduling.
            time.sleep(0.3)
            return real_popen(argv, *args, **kwargs)

        monkeypatch.setattr(forecast_backfill_job.subprocess, "Popen", _slow_popen)

        barrier = threading.Barrier(2)
        results: list = [None, None]
        errors: list = [None, None]

        def _call(idx: int, tickers: list) -> None:
            try:
                barrier.wait(timeout=5)
                results[idx] = forecast_backfill_job.start_job({"tickers": tickers})
            except Exception as exc:  # noqa: BLE001 - surface via `errors`, never lose the thread's failure
                errors[idx] = exc

        t1 = threading.Thread(target=_call, args=(0, ["AAPL"]))
        t2 = threading.Thread(target=_call, args=(1, ["MSFT"]))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert errors == [None, None], f"start_job() raised in a worker thread: {errors}"
        assert not t1.is_alive() and not t2.is_alive()

        winners = [r for r in results if r is not None]
        assert len(winners) == 1, (
            f"expected exactly one concurrent start_job() call to win the single-flight "
            f"slot, got {results}"
        )
        winner = winners[0]

        # Exactly one job ever tracked as active/running -- not two.
        assert forecast_backfill_job.get_active_job_id() == winner.job_id
        assert len(forecast_backfill_job._jobs) == 1
        assert winner.state == "running"

        forecast_backfill_job.cancel_job(winner.job_id)  # tidy up the hung child


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancelJob:
    def test_cancel_running_job_kills_it_and_marks_cancelled(self, monkeypatch):
        _set_behavior(monkeypatch, "hang")
        job = forecast_backfill_job.start_job({})
        assert job.state == "running"

        stopped = forecast_backfill_job.cancel_job(job.job_id)
        assert stopped is True
        assert job.state == "cancelled"
        assert job.error_type == "cancelled"
        assert forecast_backfill_job.get_active_job_id() is None

    def test_cancel_already_terminal_job_is_a_no_op_returning_true(self, monkeypatch):
        _set_behavior(monkeypatch, "success")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job)
        assert job.state == "succeeded"

        stopped = forecast_backfill_job.cancel_job(job.job_id)
        assert stopped is True
        assert job.state == "succeeded"  # unchanged, not clobbered to "cancelled"

    def test_cancel_unknown_job_raises_keyerror(self):
        with pytest.raises(KeyError):
            forecast_backfill_job.cancel_job("no-such-job")


# ---------------------------------------------------------------------------
# Deadline enforcement
# ---------------------------------------------------------------------------


class TestDeadlineEnforcement:
    def test_wedged_worker_is_killed_and_marked_timeout(self, monkeypatch):
        """Mirrors tests/test_robinhood_login.py's deadline-timeout coverage
        for data/robinhood_login.py's _enforce_deadline: a worker that never
        reports a terminal result is SIGTERM/SIGKILLed once
        FORECAST_BACKFILL_DEADLINE_SECONDS elapses, and the job is reported
        as 'timeout', not left 'running' forever."""
        _set_behavior(monkeypatch, "hang")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job, timeout=10.0)

        assert job.state == "timeout"
        assert job.error_type == "timeout"
        assert job._process.poll() is not None  # confirmed dead, not merely abandoned
        assert forecast_backfill_job.get_active_job_id() is None

    def test_partial_summary_survives_into_timeout_state_when_progress_was_observed(self, monkeypatch):
        """A 'progress' event received (and recorded onto
        job.partial_summary by _drain_events) BEFORE the deadline
        SIGTERM/SIGKILLs the worker must survive into the terminal
        'timeout' state -- _enforce_deadline never touches
        partial_summary, so whatever _drain_events last recorded is
        exactly what's still there after the kill. This is the whole point
        of Fix 3's events-channel half: a SIGKILL can land between
        filesystem writes, but the parent's last-received event survives
        it."""
        _set_behavior(monkeypatch, "hang_with_progress")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job, timeout=10.0)

        assert job.state == "timeout"
        assert job.partial_summary == {
            "trained": ["timeseries_momentum_10d"],
            "metrics_so_far": {
                "timeseries_momentum_10d": {
                    "accuracy": 0.55,
                    "auc": 0.58,
                    "n_train": 120,
                }
            },
        }

    def test_partial_summary_is_none_when_no_progress_event_was_observed(self, monkeypatch):
        """A worker that hangs before ever emitting a 'progress' event
        (e.g. stuck in an early phase, before any step-5 combo finishes)
        must leave partial_summary at its None default once timed out --
        the same honest 'nothing was saved' posture the filesystem side
        (_write_partial_export) guarantees."""
        _set_behavior(monkeypatch, "hang")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job, timeout=10.0)

        assert job.state == "timeout"
        assert job.partial_summary is None


# ---------------------------------------------------------------------------
# serialize_job
# ---------------------------------------------------------------------------


class TestSerializeJob:
    def test_serialize_job_shape(self, monkeypatch):
        _set_behavior(monkeypatch, "success")
        job = forecast_backfill_job.start_job({})
        _wait_until_terminal(job)

        payload = forecast_backfill_job.serialize_job(job)
        assert payload["job_id"] == job.job_id
        assert payload["state"] == "succeeded"
        assert payload["phase"] == "exporting"
        assert payload["step"] == 7
        assert payload["total_steps"] == 7
        assert payload["error"] is None
        assert payload["error_type"] is None
        assert payload["summary"] == {"status": "completed", "total_rows": 3}
        assert payload["sample_rows"] == 3
        assert payload["partial_summary"] is None  # the 'success' stub never emits a 'progress' event
        assert isinstance(payload["seconds_remaining"], float)

        # JSON-safe -- must round-trip through json.dumps without a TypeError,
        # exactly as it will when FastAPI serializes it for a real response.
        json.dumps(payload)

    def test_serialize_job_reflects_partial_summary_after_a_progress_event(self, monkeypatch):
        """serialize_job() must surface whatever partial_summary a
        'progress' event populated, even for a still-'running' job (not
        only a terminal one) -- this is what lets a poller show real
        interim progress while a run is still in flight."""
        _set_behavior(monkeypatch, "hang_with_progress")
        job = forecast_backfill_job.start_job({})
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline and job.partial_summary is None:
                time.sleep(0.05)

            payload = forecast_backfill_job.serialize_job(job)
            assert payload["state"] == "running"
            assert payload["partial_summary"] == {
                "trained": ["timeseries_momentum_10d"],
                "metrics_so_far": {
                    "timeseries_momentum_10d": {
                        "accuracy": 0.55,
                        "auc": 0.58,
                        "n_train": 120,
                    }
                },
            }
            json.dumps(payload)
        finally:
            forecast_backfill_job.cancel_job(job.job_id)  # tidy up the hung child
