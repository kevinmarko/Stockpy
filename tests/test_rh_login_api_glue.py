"""Tests for api/_rh_login.py -- the glue between data.robinhood_login's
killable-subprocess login-job primitive and the Pilots API's brokerage
connect/refresh endpoints.

The important, otherwise-untested behavior here is start_connect_job's
background watcher thread: it must persist RH_USERNAME/RH_PASSWORD via
data.brokerage_credentials.write_rh_credentials if and only if the job
reaches state="succeeded", and never before, never on failure/timeout/
cancellation. tests/test_brokerage_connect.py mocks this whole module at
the FastAPI-endpoint level (fast, decoupled from real subprocess/DB
behavior) -- this file tests api/_rh_login.py's own real logic directly.
"""

from __future__ import annotations

import threading
import time

import api._rh_login as rh_login_mod


class _FakeJob:
    """Minimal stand-in for data.robinhood_login.LoginJobState -- only the
    attributes api/_rh_login.py's start_connect_job/serialize_job actually
    read. A real threading.Lock() so `with job._lock:` in serialize_job
    behaves identically to the real dataclass."""

    def __init__(self, job_id: str, mode: str, *, state: str = "running") -> None:
        self.job_id = job_id
        self.mode = mode
        self.phase = "starting"
        self.state = state
        self.error_code = None
        self.seconds_remaining = 180.0
        self._lock = threading.Lock()


def _wait_until(predicate, *, timeout: float = 3.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestStartConnectJobPersistsOnSuccess:
    def test_persists_credentials_once_the_job_succeeds(self, monkeypatch):
        job = _FakeJob("job-1", "connect", state="running")
        written = {}

        monkeypatch.setattr(rh_login_mod, "start_login", lambda mode, **kw: job)
        monkeypatch.setattr(rh_login_mod, "get_login_state", lambda job_id: job)
        monkeypatch.setattr(
            rh_login_mod.brokerage_credentials,
            "write_rh_credentials",
            lambda u, p: written.update(username=u, password=p),
        )

        result = rh_login_mod.start_connect_job("user@example.com", "hunter2")
        assert result is job

        # Simulate the real subprocess completing a bit later -- the watcher
        # thread should notice on its next poll and persist.
        job.state = "succeeded"

        assert _wait_until(lambda: bool(written))
        assert written == {"username": "user@example.com", "password": "hunter2"}

    def test_never_persists_on_failure(self, monkeypatch):
        job = _FakeJob("job-2", "connect", state="running")
        write_calls = {"count": 0}

        monkeypatch.setattr(rh_login_mod, "start_login", lambda mode, **kw: job)
        monkeypatch.setattr(rh_login_mod, "get_login_state", lambda job_id: job)
        monkeypatch.setattr(
            rh_login_mod.brokerage_credentials,
            "write_rh_credentials",
            lambda u, p: write_calls.__setitem__("count", write_calls["count"] + 1),
        )

        rh_login_mod.start_connect_job("user@example.com", "hunter2")
        job.state = "failed"
        job.error_code = "auth_failed"

        # Give the watcher thread ample opportunity to (incorrectly) act.
        time.sleep(1.5)
        assert write_calls["count"] == 0

    def test_never_persists_on_timeout_or_cancellation(self, monkeypatch):
        for terminal_state in ("timeout", "cancelled"):
            job = _FakeJob(f"job-{terminal_state}", "connect", state="running")
            write_calls = {"count": 0}

            monkeypatch.setattr(rh_login_mod, "start_login", lambda mode, **kw: job)
            monkeypatch.setattr(rh_login_mod, "get_login_state", lambda job_id: job)
            monkeypatch.setattr(
                rh_login_mod.brokerage_credentials,
                "write_rh_credentials",
                lambda u, p: write_calls.__setitem__("count", write_calls["count"] + 1),
            )

            rh_login_mod.start_connect_job("user@example.com", "hunter2")
            job.state = terminal_state
            time.sleep(0.8)
            assert write_calls["count"] == 0, f"unexpected persistence for state={terminal_state}"

    def test_persistence_failure_is_swallowed_never_crashes_watcher(self, monkeypatch):
        """A write_rh_credentials exception must not propagate out of the
        daemon watcher thread (there's no caller there to catch it) -- it's
        logged and dropped."""
        job = _FakeJob("job-3", "connect", state="succeeded")

        monkeypatch.setattr(rh_login_mod, "start_login", lambda mode, **kw: job)
        monkeypatch.setattr(rh_login_mod, "get_login_state", lambda job_id: job)

        def boom(u, p):
            raise RuntimeError("disk full")

        monkeypatch.setattr(rh_login_mod.brokerage_credentials, "write_rh_credentials", boom)

        # Must not raise here, and the thread must not crash the test process.
        rh_login_mod.start_connect_job("user@example.com", "hunter2")
        time.sleep(0.8)


class TestStartRefreshJob:
    def test_delegates_to_start_login_with_no_credentials(self, monkeypatch):
        captured = {}

        def fake_start_login(mode, **kwargs):
            captured["mode"] = mode
            captured["kwargs"] = kwargs
            return "job-obj"

        monkeypatch.setattr(rh_login_mod, "start_login", fake_start_login)

        result = rh_login_mod.start_refresh_job()
        assert result == "job-obj"
        assert captured["mode"] == "refresh"
        assert captured["kwargs"] == {}


class TestSerializeJob:
    def test_shape_reflects_credentials_and_snapshot_state(self, monkeypatch):
        job = _FakeJob("job-4", "connect", state="running")
        job.phase = "awaiting_approval"
        job.seconds_remaining = 42.5

        monkeypatch.setattr(rh_login_mod.brokerage_credentials, "rh_credentials_present", lambda: True)

        class _FakeStore:
            def latest_account_snapshot(self):
                return object()

        monkeypatch.setattr(rh_login_mod, "HistoricalStore", lambda readonly=True: _FakeStore())

        payload = rh_login_mod.serialize_job(job)
        assert payload == {
            "job_id": "job-4",
            "mode": "connect",
            "state": "running",
            "phase": "awaiting_approval",
            "error_code": None,
            "seconds_remaining": 42.5,
            "connected": True,
            "has_account_snapshot": True,
        }

    def test_degrades_snapshot_check_honestly_on_db_failure(self, monkeypatch):
        """Never raises -- CONSTRAINT #6. A DB error yields has_account_snapshot=False,
        not a 500 from the status endpoint."""
        job = _FakeJob("job-5", "refresh", state="succeeded")
        monkeypatch.setattr(rh_login_mod.brokerage_credentials, "rh_credentials_present", lambda: False)

        class _BoomStore:
            def latest_account_snapshot(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(rh_login_mod, "HistoricalStore", lambda readonly=True: _BoomStore())

        payload = rh_login_mod.serialize_job(job)
        assert payload["has_account_snapshot"] is False
        assert payload["connected"] is False
