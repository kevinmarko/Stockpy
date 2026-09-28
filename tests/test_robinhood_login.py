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
        self.popen_calls = getattr(self, "popen_calls", 0) + 1
        new_argv = [argv[0], str(_STUB_PATH)] + list(argv[3:])
        return self._real.Popen(new_argv, *args, **kwargs)


@pytest.fixture(autouse=True)
def _stub_worker(monkeypatch):
    """Redirect data.robinhood_login's own subprocess.Popen calls to launch
    the stub script instead of the real worker module. Returns the proxy so
    a test can count how many workers were actually spawned."""
    proxy = _PopenProxy(subprocess)
    monkeypatch.setattr(robinhood_login, "subprocess", proxy)
    return proxy


@pytest.fixture(autouse=True)
def _reset_single_flight():
    """Each test starts with no in-flight job (the single-flight guard is
    module state), and any job a test leaves running is killed afterwards so
    it can't leak into the next test."""
    robinhood_login._active_job = None
    yield
    job = robinhood_login._active_job
    if job is not None:
        with job._lock:
            if job.state == "running":
                robinhood_login._kill_process_group(job._process)
                job.state = "cancelled"
    robinhood_login._active_job = None


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


# ---------------------------------------------------------------------------
# Single-flight -- at most one running login job (one approval prompt) per
# process. See data/robinhood_login.py's module docstring.
# ---------------------------------------------------------------------------

def _wait_until_awaiting(job, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with job._lock:
            if job.phase != "starting":
                return
        time.sleep(0.05)
    pytest.fail("child never left the 'starting' phase")


class TestSingleFlight:
    @pytest.fixture(autouse=True)
    def _long_deadline(self, monkeypatch):
        monkeypatch.setattr("settings.settings.RH_LOGIN_DEADLINE_SECONDS", 30.0)
        monkeypatch.setattr("settings.settings.RH_LOGIN_STARTUP_SECONDS", 10.0)

    def test_refresh_while_refresh_running_joins_the_running_job(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        first = robinhood_login.start_login("refresh")
        _wait_until_awaiting(first)

        second = robinhood_login.start_login("refresh")

        assert second is first
        assert _stub_worker.popen_calls == 1

    def test_connect_while_refresh_running_is_refused(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        running = robinhood_login.start_login("refresh")

        with pytest.raises(robinhood_login.RobinhoodLoginInProgress) as exc_info:
            robinhood_login.start_login("connect", username="u@example.com", password="pw")

        assert exc_info.value.job is running
        assert exc_info.value.requested_mode == "connect"
        assert "u@example.com" not in str(exc_info.value)
        assert _stub_worker.popen_calls == 1

    def test_refresh_while_connect_running_is_refused(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        running = robinhood_login.start_login("connect", username="u@example.com", password="pw")

        with pytest.raises(robinhood_login.RobinhoodLoginInProgress) as exc_info:
            robinhood_login.start_login("refresh")

        assert exc_info.value.job is running
        assert _stub_worker.popen_calls == 1

    def test_connect_while_connect_running_is_refused(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        robinhood_login.start_login("connect", username="a@example.com", password="pw1")

        with pytest.raises(robinhood_login.RobinhoodLoginInProgress):
            robinhood_login.start_login("connect", username="b@example.com", password="pw2")
        assert _stub_worker.popen_calls == 1

    def test_in_progress_is_a_login_failed_subclass(self) -> None:
        assert issubclass(
            robinhood_login.RobinhoodLoginInProgress, robinhood_login.RobinhoodLoginFailed
        )

    def test_new_job_starts_once_the_previous_one_is_terminal(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "success")
        first = robinhood_login.start_login("refresh")
        _wait_until_terminal(first)

        second = robinhood_login.start_login("refresh")

        assert second is not first
        assert second.job_id != first.job_id
        assert _stub_worker.popen_calls == 2
        _wait_until_terminal(second)

    def test_cancelled_job_frees_the_slot(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        first = robinhood_login.start_login("refresh")
        _wait_until_awaiting(first)
        robinhood_login.cancel_login(first.job_id)

        _set_behavior(monkeypatch, "success")
        second = robinhood_login.start_login("connect", username="u@example.com", password="pw")
        assert second is not first
        _wait_until_terminal(second)
        assert second.state == "succeeded"

    def test_concurrent_refresh_callers_spawn_exactly_one_worker(self, monkeypatch, _stub_worker) -> None:
        import threading

        _set_behavior(monkeypatch, "hang_after_started")
        barrier = threading.Barrier(8)
        results: list = []
        errors: list = []

        def _call() -> None:
            barrier.wait()
            try:
                results.append(robinhood_login.start_login("refresh"))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=_call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors
        assert len(results) == 8
        assert len({id(j) for j in results}) == 1
        assert _stub_worker.popen_calls == 1

    def test_login_blocking_waits_on_the_existing_refresh_job(self, monkeypatch, _stub_worker) -> None:
        monkeypatch.setenv("STUB_DELAY_SECONDS", "1.0")
        _set_behavior(monkeypatch, "delayed_success")
        running = robinhood_login.start_login("refresh")
        _wait_until_awaiting(running)

        robinhood_login.login_blocking("refresh", poll_interval=0.05)  # must not raise

        assert running.state == "succeeded"
        assert _stub_worker.popen_calls == 1

    def test_login_blocking_raises_in_progress_when_a_connect_is_running(self, monkeypatch, _stub_worker) -> None:
        _set_behavior(monkeypatch, "hang_after_started")
        robinhood_login.start_login("connect", username="u@example.com", password="pw")

        with pytest.raises(robinhood_login.RobinhoodLoginInProgress):
            robinhood_login.login_blocking("refresh", poll_interval=0.05)
        assert _stub_worker.popen_calls == 1


# ---------------------------------------------------------------------------
# Cross-process single-flight (fcntl.flock on <OUTPUT_DIR>/robinhood_login.lock)
# ---------------------------------------------------------------------------

_HOLDER_PATH = Path(__file__).parent / "fixtures" / "robinhood_login_lock_holder.py"


def _flock_from_another_description(lock_dir: Path, owner: dict):
    """Hold the lock on a SEPARATE open file description -- to flock that is
    indistinguishable from another process -- and write an owner sidecar."""
    import fcntl
    import os

    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_dir / robinhood_login.LOCK_FILENAME), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (lock_dir / robinhood_login.OWNER_FILENAME).write_text(json.dumps(owner), encoding="utf-8")
    return fd


def _start_holder(lock_dir: Path, action: str, behavior: str):
    """Start tests/fixtures/robinhood_login_lock_holder.py as a real second
    process; returns (Popen, first-line info)."""
    import os
    import sys

    env = dict(os.environ, STUB_LOGIN_BEHAVIOR=behavior)
    proc = subprocess.Popen(
        [sys.executable, str(_HOLDER_PATH), str(lock_dir), action],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    line = proc.stdout.readline()
    if not line:
        proc.kill()
        _, err = proc.communicate(timeout=10)
        pytest.fail(f"lock-holder helper failed to start: {err[-2000:]}")
    return proc, json.loads(line)


def _kill_pgid(pid: int) -> None:
    import os
    import signal

    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _close_holder(holder, info) -> None:
    if holder.poll() is None:
        holder.kill()
        holder.wait(timeout=10)
    _kill_pgid(info["worker_pid"])
    for stream in (holder.stdout, holder.stderr):
        stream.close()


class TestCrossProcessLock:
    @pytest.fixture(autouse=True)
    def _long_deadline(self, monkeypatch):
        monkeypatch.setattr("settings.settings.RH_LOGIN_DEADLINE_SECONDS", 30.0)
        monkeypatch.setattr("settings.settings.RH_LOGIN_STARTUP_SECONDS", 10.0)

    def test_lock_file_lives_in_output_dir_by_default(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(robinhood_login, "_lock_dir_override", None)
        monkeypatch.setattr("settings.settings.OUTPUT_DIR", tmp_path)
        assert robinhood_login._lock_dir() == tmp_path

    def test_lock_held_elsewhere_refuses_any_mode_with_owner_and_no_worker(
        self, monkeypatch, _stub_worker
    ) -> None:
        import os

        lock_dir = robinhood_login._lock_dir()
        owner = {"pid": 424242, "job_id": "rhlogin-other", "mode": "refresh",
                 "started_at": "2026-09-28T12:40:00+00:00", "extra": "not-a-diagnostic-field"}
        fd = _flock_from_another_description(lock_dir, owner)
        try:
            for mode, kwargs in (
                ("refresh", {}),
                ("connect", {"username": "u@example.com", "password": "s3cret"}),
            ):
                with pytest.raises(robinhood_login.RobinhoodLoginInProgress) as exc_info:
                    robinhood_login.start_login(mode, **kwargs)
                exc = exc_info.value
                assert exc.job is None
                assert exc.requested_mode == mode
                assert exc.owner == {k: owner[k] for k in ("pid", "job_id", "mode", "started_at")}
                message = str(exc)
                assert "424242" in message and "rhlogin-other" in message
                assert "another process" in message
                for secret in ("not-a-diagnostic-field", "u@example.com", "s3cret"):
                    assert secret not in message
            assert getattr(_stub_worker, "popen_calls", 0) == 0
            assert robinhood_login._active_job is None
        finally:
            os.close(fd)

    def test_owner_sidecar_written_while_held_and_removed_on_release(
        self, monkeypatch, _stub_worker
    ) -> None:
        import os

        _set_behavior(monkeypatch, "hang_after_started")
        job = robinhood_login.start_login("refresh")
        owner = robinhood_login.read_lock_owner()
        assert owner["pid"] == os.getpid()
        assert owner["job_id"] == job.job_id
        assert owner["mode"] == "refresh"
        assert owner["started_at"]
        assert job._xlock_fd is not None

        robinhood_login.cancel_login(job.job_id)
        deadline = time.time() + 5
        while job._xlock_fd is not None and time.time() < deadline:
            time.sleep(0.05)
        assert job._xlock_fd is None, "the deadline thread must release the lock once terminal"
        assert not (robinhood_login._lock_dir() / robinhood_login.OWNER_FILENAME).exists()

    def test_launch_failure_releases_the_lock(self, monkeypatch) -> None:
        import fcntl
        import os

        class _Boom:
            def __getattr__(self, name):
                return getattr(subprocess, name)

            def Popen(self, *a, **k):  # noqa: N802
                raise OSError("spawn failed")

        monkeypatch.setattr(robinhood_login, "subprocess", _Boom())
        with pytest.raises(OSError):
            robinhood_login.start_login("refresh")
        # Nothing holds the lock now: a fresh open file description can take it.
        fd = os.open(str(robinhood_login._lock_dir() / robinhood_login.LOCK_FILENAME), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_no_fcntl_falls_back_to_per_process_with_one_warning(
        self, monkeypatch, _stub_worker, caplog
    ) -> None:
        monkeypatch.setattr(robinhood_login, "_fcntl", None)
        monkeypatch.setattr(robinhood_login, "_warned_no_xlock", False)
        _set_behavior(monkeypatch, "success")
        caplog.set_level("WARNING", logger="data.robinhood_login")

        first = robinhood_login.start_login("refresh")
        _wait_until_terminal(first)
        second = robinhood_login.start_login("refresh")
        _wait_until_terminal(second)

        assert first.state == second.state == "succeeded"
        assert sum(
            "cross-process login lock unavailable" in r.getMessage() for r in caplog.records
        ) == 1

    def test_second_real_process_is_refused_until_the_first_is_killed(
        self, monkeypatch, _stub_worker
    ) -> None:
        import signal

        lock_dir = robinhood_login._lock_dir()
        holder, info = _start_holder(lock_dir, "hold", "hang_after_started")
        try:
            assert info["pid"] == holder.pid
            for mode in ("refresh", "connect"):
                with pytest.raises(robinhood_login.RobinhoodLoginInProgress) as exc_info:
                    robinhood_login.start_login(mode, username="u@example.com", password="pw")
                assert exc_info.value.job is None
                assert exc_info.value.owner["pid"] == holder.pid
                assert exc_info.value.owner["job_id"] == info["job_id"]
            assert getattr(_stub_worker, "popen_calls", 0) == 0

            holder.send_signal(signal.SIGKILL)
            holder.wait(timeout=10)

            # The kernel dropped the lock with the dead process, even though
            # its orphaned stub worker is still alive (the worker does not
            # inherit the lock fd). A stale sidecar is left behind and is
            # simply overwritten by the next holder.
            _set_behavior(monkeypatch, "success")
            job = robinhood_login.start_login("refresh")
            assert _stub_worker.popen_calls == 1
            assert robinhood_login.read_lock_owner()["job_id"] == job.job_id
            _wait_until_terminal(job)
            assert job.state == "succeeded"
        finally:
            _close_holder(holder, info)

    def test_lock_frees_when_the_first_process_finishes_its_job_and_exits(
        self, monkeypatch, _stub_worker
    ) -> None:
        lock_dir = robinhood_login._lock_dir()
        holder, info = _start_holder(lock_dir, "finish", "success")
        try:
            assert holder.stdout.readline().strip() == "released"
            assert holder.wait(timeout=15) == 0
            assert not (lock_dir / robinhood_login.OWNER_FILENAME).exists()

            _set_behavior(monkeypatch, "success")
            job = robinhood_login.start_login("refresh")
            _wait_until_terminal(job)
            assert job.state == "succeeded"
        finally:
            _close_holder(holder, info)
