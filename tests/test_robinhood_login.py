"""
tests/test_robinhood_login.py
==============================
Tests for data/robinhood_login.py's launcher primitives (start_login,
get_login_state, cancel_login, login_blocking) using a STUB worker script
(tests/fixtures/robinhood_login_worker_stub.py) in place of the real
data/robinhood_login_worker.py -- no real Robinhood network calls happen
anywhere in this file.

Patch point
-----------
data.robinhood_login.start_login builds its subprocess.Popen argv as
``[sys.executable, "-m", "data.robinhood_login_worker", "--mode", ...]``.
Rather than reach into that argv-construction code, this file rebinds the
`subprocess` NAME inside data.robinhood_login's own module namespace to a
thin proxy (_PopenProxy) that forwards every subprocess.* attribute to the
real module EXCEPT Popen, which rewrites the "-m data.robinhood_login_worker"
argv pair to the stub script's path before delegating to the real
subprocess.Popen. This is scoped to ONLY data.robinhood_login's own
subprocess.Popen(...) call site -- unlike monkeypatching the real
`subprocess` module's Popen attribute directly, every other in-process caller
of subprocess.Popen (pytest's own internals included) is unaffected.

Speed
-----
RH_LOGIN_DEADLINE_SECONDS / RH_LOGIN_GRACE_SECONDS / RH_LOGIN_STARTUP_SECONDS
are monkeypatched to sub-2-second values (matching the house convention of
patching the live `settings.settings` singleton directly -- see
tests/test_robinhood_portfolio.py's ROBINHOOD_AUTO_REFRESH_ENABLED patches)
so the whole file runs in a few seconds, never anywhere near the real
180s/5s/30s production defaults.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

import data.robinhood_login as robinhood_login

_STUB_PATH = Path(__file__).parent / "fixtures" / "robinhood_login_worker_stub.py"


class _PopenProxy:
    """Forwards every ``subprocess.*`` attribute to the real module except
    ``Popen``, which redirects the "-m data.robinhood_login_worker" argv
    pair to the stub script -- see module docstring above for why this
    (rather than patching the real ``subprocess`` module) is the seam used.
    """

    def __init__(self, real_module):
        self._real = real_module

    def __getattr__(self, name):
        return getattr(self._real, name)

    def Popen(self, argv, *args, **kwargs):
        assert argv[1:3] == ["-m", "data.robinhood_login_worker"], (
            f"unexpected argv shape -- patch point may have drifted: {argv}"
        )
        new_argv = [argv[0], str(_STUB_PATH)] + list(argv[3:])
        return self._real.Popen(new_argv, *args, **kwargs)


@pytest.fixture(autouse=True)
def _stub_worker(monkeypatch):
    """Redirect data.robinhood_login's own subprocess.Popen calls to launch
    the stub script instead of the real worker module."""
    monkeypatch.setattr(robinhood_login, "subprocess", _PopenProxy(subprocess))


@pytest.fixture(autouse=True)
def _fast_deadlines(monkeypatch):
    """Tiny deadlines so a hung/never-started child is reaped in ~1-2s
    instead of the real 180s/30s/5s production defaults."""
    monkeypatch.setattr("settings.settings.RH_LOGIN_DEADLINE_SECONDS", 1.5)
    monkeypatch.setattr("settings.settings.RH_LOGIN_GRACE_SECONDS", 0.5)
    monkeypatch.setattr("settings.settings.RH_LOGIN_STARTUP_SECONDS", 1.0)


def _set_behavior(monkeypatch, behavior: str) -> None:
    monkeypatch.setenv("STUB_LOGIN_BEHAVIOR", behavior)


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
# Successful job
# ---------------------------------------------------------------------------

class TestStartLoginSuccess:
    def test_successful_job_reaches_succeeded(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "success")

        job = robinhood_login.start_login("connect", username="u@example.com", password="pw")
        _wait_until_terminal(job)

        assert job.state == "succeeded"
        assert job.error_code is None
        assert job.phase == "done"

    def test_get_login_state_returns_the_same_job(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "success")

        job = robinhood_login.start_login("refresh")
        _wait_until_terminal(job)

        fetched = robinhood_login.get_login_state(job.job_id)
        assert fetched is job

    def test_get_login_state_unknown_job_id_returns_none(self) -> None:
        assert robinhood_login.get_login_state("not-a-real-job-id") is None


# ---------------------------------------------------------------------------
# Timeout -- a hung child that already emitted 'started' is killed on its
# deadline and reported distinctly from a child that never started at all.
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_hung_child_after_started_is_killed_and_times_out(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "hang_after_started")

        job = robinhood_login.start_login("refresh")
        _wait_until_terminal(job, timeout=10.0)

        assert job.state == "timeout"
        assert job.error_code == "timeout"


class TestChildStartFailed:
    def test_no_started_event_reports_child_start_failed(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "hang_no_started")

        job = robinhood_login.start_login("refresh")
        _wait_until_terminal(job, timeout=10.0)

        assert job.state == "failed"
        assert job.error_code == "child_start_failed"


# ---------------------------------------------------------------------------
# Failure exit -- the worker reports a clean failure result (not a hang)
# ---------------------------------------------------------------------------

class TestFailureExit:
    def test_failure_result_event_reaches_failed(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "fail")

        job = robinhood_login.start_login("connect", username="u@example.com", password="pw")
        _wait_until_terminal(job)

        assert job.state == "failed"
        assert job.error_code == "auth_failed"


# ---------------------------------------------------------------------------
# cancel_login
# ---------------------------------------------------------------------------

class TestCancelLogin:
    def test_cancel_terminates_running_job_early(self, monkeypatch) -> None:
        # A generous deadline so the background deadline-enforcer never
        # races cancel_login() for who kills the process first -- this test
        # is specifically about cancel_login()'s own kill path.
        monkeypatch.setattr("settings.settings.RH_LOGIN_DEADLINE_SECONDS", 30.0)
        _set_behavior(monkeypatch, "hang_after_started")

        job = robinhood_login.start_login("refresh")
        # Wait for the child to genuinely be running (past the 'started'
        # event), not still in the 'starting' phase.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with job._lock:
                if job.phase != "starting":
                    break
            time.sleep(0.05)
        else:
            pytest.fail("child never reported 'started'")

        result = robinhood_login.cancel_login(job.job_id)

        assert result is True
        assert job.state == "cancelled"
        assert job.error_code == "cancelled"

    def test_cancel_unknown_job_id_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            robinhood_login.cancel_login("not-a-real-job-id")

    def test_cancel_already_terminal_job_is_a_noop_true(self, monkeypatch) -> None:
        """Calling cancel_login on a job that already succeeded returns True
        without altering its terminal state (mirrors _kill_process_group's
        own "already exited" success case)."""
        _set_behavior(monkeypatch, "success")

        job = robinhood_login.start_login("connect", username="u@example.com", password="pw")
        _wait_until_terminal(job)
        assert job.state == "succeeded"

        result = robinhood_login.cancel_login(job.job_id)

        assert result is True
        assert job.state == "succeeded"  # unchanged


# ---------------------------------------------------------------------------
# Credentials round-trip through the anonymous pipe
# ---------------------------------------------------------------------------

class TestCredentialsRoundTrip:
    def test_credentials_cross_the_pipe_intact(self, monkeypatch, tmp_path) -> None:
        """The stub echoes back exactly what it read off --creds-fd, written
        to a side-channel file (never the events stream, never a log) so
        this test can assert on it directly -- an equality assertion on a
        synthetic secret is the accepted convention in this repo's
        credential tests (see
        tests/test_brokerage_connect.py::test_write_rh_credentials_never_logs_values
        for the logging-side version of the same rule)."""
        echo_path = tmp_path / "echoed_creds.json"
        monkeypatch.setenv("STUB_ECHO_PATH", str(echo_path))
        _set_behavior(monkeypatch, "echo_creds")

        job = robinhood_login.start_login(
            "connect",
            username="round-trip-user@example.com",
            password="round-trip-pw-123",
        )
        _wait_until_terminal(job)
        assert job.state == "succeeded"

        echoed = json.loads(echo_path.read_text(encoding="utf-8"))
        assert echoed["username"] == "round-trip-user@example.com"
        assert echoed["password"] == "round-trip-pw-123"

    def test_refresh_mode_sends_blank_line_not_credentials(self, monkeypatch, tmp_path) -> None:
        """mode='refresh' carries no candidate credentials -- start_login
        writes a single blank line, telling the (real) worker to use
        whatever is already configured in .env instead."""
        echo_path = tmp_path / "echoed_creds.json"
        monkeypatch.setenv("STUB_ECHO_PATH", str(echo_path))
        _set_behavior(monkeypatch, "echo_creds")

        job = robinhood_login.start_login("refresh")
        _wait_until_terminal(job)
        assert job.state == "succeeded"

        assert echo_path.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# login_blocking -- the synchronous wrapper used by
# data.robinhood_portfolio._fetch_live_snapshot
# ---------------------------------------------------------------------------

class TestLoginBlocking:
    def test_login_blocking_returns_on_success(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "success")
        robinhood_login.login_blocking("refresh", poll_interval=0.05)  # must not raise

    def test_login_blocking_raises_timeout_on_hang(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        with pytest.raises(robinhood_login.RobinhoodLoginTimeout):
            robinhood_login.login_blocking("refresh", poll_interval=0.05)

    def test_login_blocking_raises_failed_on_failure(self, monkeypatch) -> None:
        _set_behavior(monkeypatch, "fail")
        with pytest.raises(robinhood_login.RobinhoodLoginFailed):
            robinhood_login.login_blocking(
                "connect", username="u@example.com", password="pw", poll_interval=0.05
            )
