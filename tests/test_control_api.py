"""
tests/test_control_api.py
==========================
Tests for the orchestrator Control API (``api/control_api.py``).

Mirrors ``tests/test_state_api.py``'s conventions (FastAPI ``TestClient``,
``mock.patch.object(settings, ...)`` for live-read settings, a dedicated
``TestCORS`` class documenting the import-time-capture caveat, and an
AST-based architectural guard test) but exercises a FAKE
``OrchestratorDaemon``-shaped object injected via ``control_api.set_daemon``
rather than a real one -- no real pipeline execution happens in these tests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest import mock
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.control_api as control_api
from desktop.daemon_runtime import (
    OrchestratorDaemon,
    RunRecord,
    RunState,
    TriggerOutcome,
    TriggerResult,
)

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(control_api.app, client=("127.0.0.1", 54123))


@pytest.fixture(autouse=True)
def _reset_daemon():
    """Ensure no daemon leaks between tests -- each test sets its own fake
    (or leaves it None) explicitly."""
    control_api.set_daemon(None)
    yield
    control_api.set_daemon(None)


class _FakeDaemon:
    """Real-shaped stand-in for desktop.daemon_runtime.OrchestratorDaemon.

    A bare MagicMock cannot do this job: every attribute on one is both
    callable AND truthy, so ``daemon.is_running()`` -- a @property called as a
    method -- passes silently here while raising
    ``TypeError: 'bool' object is not callable`` in production. That is
    exactly the POST /daemon/restart 500 this class exists to make
    unreproducible.

    ``mock.create_autospec(OrchestratorDaemon, instance=True)`` does NOT help
    either, despite the folklore: create_autospec explicitly drops the spec
    for data descriptors ("descriptors don't have a spec because we don't
    know what type they return") and returns a plain CALLABLE MagicMock for a
    property -- verified against this repo's Python 3.12, ``d.is_running()``
    succeeds instead of raising.

    So: METHODS are MagicMocks (every existing assert_called_once_with /
    assert_not_called assertion keeps working verbatim); PROPERTIES are real
    @property, set only through __init__.
    ``test_fake_daemon_mirrors_orchestrator_daemon_member_kinds`` pins this
    shape against the real class, so a future method<->property flip in
    daemon_runtime.py fails loudly here instead of silently in a handler.
    """

    def __init__(
        self,
        status=None,
        last_result=None,
        get_run_map=None,
        trigger_result=None,
        is_running=False,
    ):
        self.status = MagicMock(
            name="status",
            return_value=status
            or {
                "is_running": False,
                "current_run_id": None,
                "interval_seconds": 60,
                "last_run": None,
                "engines_warm": True,
                "started_at": None,
            },
        )
        _runs = get_run_map or {}
        self.get_run = MagicMock(name="get_run", side_effect=lambda run_id: _runs.get(run_id))
        self.trigger_run = MagicMock(name="trigger_run")
        if trigger_result is not None:
            self.trigger_run.return_value = trigger_result
        self.set_interval = MagicMock(name="set_interval")
        self.shutdown = MagicMock(name="shutdown")
        self._is_running = is_running
        self._last_result = last_result

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def last_result(self):
        return self._last_result


def _make_fake_daemon(status=None, last_result=None, get_run_map=None, trigger_result=None, is_running=False):
    """Build a real-shaped fake OrchestratorDaemon (see _FakeDaemon)."""
    return _FakeDaemon(
        status=status,
        last_result=last_result,
        get_run_map=get_run_map,
        trigger_result=trigger_result,
        is_running=is_running,
    )


def _make_run_record(run_id="run-1", state=RunState.SUCCEEDED, finished=True, reason="manual", error=None):
    started_at = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 7, 6, 12, 1, 0, tzinfo=timezone.utc) if finished else None
    duration = 60.0 if finished else None
    return RunRecord(
        run_id=run_id,
        state=state,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
        error=error,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_ok_with_both_tokens_configured():
    with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_reports_daemon_alive_false_when_no_daemon_set():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "daemon_alive": False}


def test_health_reports_daemon_alive_true_when_daemon_set():
    control_api.set_daemon(_make_fake_daemon())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["daemon_alive"] is True


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_fail_open_when_state_api_token_unset(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "STATE_API_TOKEN", None), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.get("/status")
        assert resp.status_code == 200

    def test_401_with_wrong_token_when_set(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            resp = client.get("/status", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid or missing bearer token"

    def test_401_with_missing_token_when_set(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            resp = client.get("/status")
        assert resp.status_code == 401

    def test_status_daemon_not_alive_shape(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/status")
        assert resp.status_code == 200
        assert resp.json() == {"daemon_alive": False}

    def test_status_field_mapping_from_fake_daemon(self):
        started_at = datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
        last_run = _make_run_record()
        daemon = _make_fake_daemon(
            status={
                "is_running": True,
                "current_run_id": "run-123",
                "interval_seconds": 60,
                "last_run": last_run,
                "engines_warm": True,
                "started_at": started_at,
            }
        )
        control_api.set_daemon(daemon)

        with mock.patch.object(settings, "STATE_API_TOKEN", None), \
             mock.patch.object(settings, "ADVISORY_ONLY", True), \
             mock.patch.object(settings, "DRY_RUN", False), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = True
            mock_ks_cls.return_value.reason.return_value = "manual pause"
            resp = client.get("/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["daemon_alive"] is True
        assert body["is_running"] is True
        assert body["current_run_id"] == "run-123"
        assert body["interval_seconds"] == 60
        assert body["engines_warm"] is True
        assert body["started_at"] == started_at.isoformat()
        assert body["last_run"]["run_id"] == "run-1"
        assert body["last_run"]["state"] == "succeeded"
        assert body["kill_switch_active"] is True
        assert body["kill_switch_reason"] == "manual pause"
        assert body["advisory_only"] is True
        assert body["dry_run"] is False

    def test_status_kill_switch_inactive_reason_is_none(self):
        daemon = _make_fake_daemon()
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "STATE_API_TOKEN", None), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.get("/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kill_switch_active"] is False
        assert body["kill_switch_reason"] is None


# ---------------------------------------------------------------------------
# POST /run
# ---------------------------------------------------------------------------


class TestTriggerRun:
    def test_403_when_command_token_unset(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", None):
            resp = client.post("/run")
        assert resp.status_code == 403
        assert "ORCHESTRATOR_DAEMON_TOKEN" in resp.json()["detail"]

    def test_401_with_wrong_token_when_set(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post("/run", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid or missing bearer token"

    def test_read_token_never_authorizes_post_run(self):
        """A caller presenting the correct READ token (but no/incorrect
        command token) must still be rejected on POST /run -- the read
        token must never substitute for the command token."""
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post("/run", headers={"Authorization": "Bearer read-tok"})
        assert resp.status_code == 401

    def test_202_and_run_id_on_success_with_correct_token(self):
        trigger_result = TriggerResult(outcome=TriggerOutcome.ACCEPTED, run_id="new-run-1")
        daemon = _make_fake_daemon(trigger_result=trigger_result)
        control_api.set_daemon(daemon)

        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.post("/run", headers={"Authorization": "Bearer cmd-tok"})

        assert resp.status_code == 202
        body = resp.json()
        assert body["run_id"] == "new-run-1"
        assert body["state"] == "queued"
        daemon.trigger_run.assert_called_once_with(reason="manual")

    def test_409_when_already_running(self):
        trigger_result = TriggerResult(outcome=TriggerOutcome.ALREADY_RUNNING, run_id="existing-run")
        daemon = _make_fake_daemon(trigger_result=trigger_result)
        control_api.set_daemon(daemon)

        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.post("/run", headers={"Authorization": "Bearer cmd-tok"})

        assert resp.status_code == 409
        assert resp.json()["detail"]["run_id"] == "existing-run"

    def test_423_when_kill_switch_active_and_trigger_run_not_called(self):
        daemon = _make_fake_daemon()
        control_api.set_daemon(daemon)

        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = True
            mock_ks_cls.return_value.reason.return_value = "manual halt"
            resp = client.post("/run", headers={"Authorization": "Bearer cmd-tok"})

        assert resp.status_code == 423
        assert resp.json()["detail"]["kill_switch_reason"] == "manual halt"
        # The check must short-circuit BEFORE trigger_run is ever called.
        daemon.trigger_run.assert_not_called()

    def test_503_when_no_daemon_set(self):
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post("/run", headers={"Authorization": "Bearer cmd-tok"})
        assert resp.status_code == 503

    def test_auth_rejected_before_any_daemon_or_kill_switch_check(self):
        """Even with no daemon set at all, a bad/missing command token must
        yield the auth failure (403/401), never a 503 -- proving the auth
        dependency runs first."""
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post("/run", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /interval
# ---------------------------------------------------------------------------


class TestSetInterval:
    def test_403_when_command_token_unset(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", None):
            resp = client.put("/interval", json={"interval_seconds": 300})
        assert resp.status_code == 403
        assert "ORCHESTRATOR_DAEMON_TOKEN" in resp.json()["detail"]

    def test_401_with_wrong_token_when_set(self):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": 300},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_read_token_never_authorizes_put_interval(self):
        """Same invariant as POST /run: the read token must never substitute
        for the command token."""
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": 300},
                headers={"Authorization": "Bearer read-tok"},
            )
        assert resp.status_code == 401

    def test_200_calls_daemon_set_interval_with_correct_value(self):
        daemon = _make_fake_daemon()
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": 300},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"interval_seconds": 300}
        daemon.set_interval.assert_called_once_with(300)

    def test_zero_is_accepted(self):
        daemon = _make_fake_daemon()
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": 0},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 200
        daemon.set_interval.assert_called_once_with(0)

    @pytest.mark.parametrize("bad_value", [-1, 1, 59, 86401])
    def test_out_of_range_values_are_422_and_daemon_never_called(self, bad_value):
        daemon = _make_fake_daemon()
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": bad_value},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 422
        daemon.set_interval.assert_not_called()

    def test_503_when_no_daemon_set(self):
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": 300},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 503

    def test_auth_rejected_before_any_daemon_check(self):
        """No daemon set at all, bad token -> still 401, never 503 -- proves
        the auth dependency runs before the handler body (which is where the
        503-on-no-daemon check lives)."""
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.put(
                "/interval", json={"interval_seconds": 300},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Token-never-logged assertions
# ---------------------------------------------------------------------------


class TestTokenNeverLogged:
    def test_read_token_never_appears_in_logs(self, caplog):
        control_api.set_daemon(_make_fake_daemon())
        with caplog.at_level(logging.DEBUG):
            with mock.patch.object(settings, "STATE_API_TOKEN", "super-secret-read"):
                client.get("/status", headers={"Authorization": "Bearer WRONG-value"})
        for record in caplog.records:
            assert "super-secret-read" not in record.getMessage()
            assert "WRONG-value" not in record.getMessage()

    def test_command_token_never_appears_in_logs(self, caplog):
        control_api.set_daemon(_make_fake_daemon())
        with caplog.at_level(logging.DEBUG):
            with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "super-secret-cmd"):
                client.post("/run", headers={"Authorization": "Bearer WRONG-value-2"})
        for record in caplog.records:
            assert "super-secret-cmd" not in record.getMessage()
            assert "WRONG-value-2" not in record.getMessage()


# ---------------------------------------------------------------------------
# GET /run/{run_id}/status
# ---------------------------------------------------------------------------


class TestRunStatus:
    def test_404_for_unknown_run_id(self):
        control_api.set_daemon(_make_fake_daemon(get_run_map={}))
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/unknown-id/status")
        assert resp.status_code == 404

    def test_200_running_record_finished_at_null(self):
        record = _make_run_record(run_id="run-running", state=RunState.RUNNING, finished=False)
        control_api.set_daemon(_make_fake_daemon(get_run_map={"run-running": record}))
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/run-running/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "running"
        assert body["finished_at"] is None

    def test_200_succeeded_record(self):
        record = _make_run_record(run_id="run-done", state=RunState.SUCCEEDED, finished=True)
        control_api.set_daemon(_make_fake_daemon(get_run_map={"run-done": record}))
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/run-done/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "succeeded"
        assert body["finished_at"] is not None
        assert body["duration_seconds"] == 60.0

    def test_503_when_no_daemon(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/any-id/status")
        assert resp.status_code == 503

    def test_401_when_read_token_wrong(self):
        control_api.set_daemon(_make_fake_daemon(get_run_map={}))
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/run/any-id/status", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /run/latest
# ---------------------------------------------------------------------------


class TestRunLatest:
    def test_404_when_no_completed_run_yet(self):
        control_api.set_daemon(_make_fake_daemon(last_result=None))
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/latest")
        assert resp.status_code == 404

    def test_200_when_last_result_present(self):
        record = _make_run_record(run_id="latest-run")
        control_api.set_daemon(_make_fake_daemon(last_result=record))
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/latest")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == "latest-run"

    def test_503_when_no_daemon(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/run/latest")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /runs/history -- durable run history (desktop/run_history_store.py),
# independent of the daemon's in-memory ring GET /status returns.
# ---------------------------------------------------------------------------


class TestRunsHistory:
    def test_returns_recent_runs_from_store(self, monkeypatch):
        rows = [{"run_id": "orch-1", "state": "succeeded"}]
        fake_store = MagicMock()
        fake_store.get_recent.return_value = rows
        monkeypatch.setattr(control_api, "RunHistoryStore", lambda *a, **k: fake_store)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/runs/history")
        assert resp.status_code == 200
        assert resp.json() == rows

    def test_works_without_a_daemon_attached(self, monkeypatch):
        """Unlike every other GET here, this endpoint has no daemon-not-
        attached branch -- the whole point is to keep serving history across
        a daemon restart, exactly when no daemon is attached yet."""
        fake_store = MagicMock()
        fake_store.get_recent.return_value = []
        monkeypatch.setattr(control_api, "RunHistoryStore", lambda *a, **k: fake_store)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/runs/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_degrades_to_empty_list_on_store_construction_failure(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(control_api, "RunHistoryStore", _boom)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/runs/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_is_clamped_to_200(self, monkeypatch):
        fake_store = MagicMock()
        fake_store.get_recent.return_value = []
        monkeypatch.setattr(control_api, "RunHistoryStore", lambda *a, **k: fake_store)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            client.get("/runs/history?limit=9999")
        fake_store.get_recent.assert_called_once_with(limit=200)

    def test_limit_below_one_is_clamped_to_one(self, monkeypatch):
        fake_store = MagicMock()
        fake_store.get_recent.return_value = []
        monkeypatch.setattr(control_api, "RunHistoryStore", lambda *a, **k: fake_store)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            client.get("/runs/history?limit=0")
        fake_store.get_recent.assert_called_once_with(limit=1)

    def test_401_when_read_token_wrong(self, monkeypatch):
        fake_store = MagicMock()
        fake_store.get_recent.return_value = []
        monkeypatch.setattr(control_api, "RunHistoryStore", lambda *a, **k: fake_store)
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/runs/history", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /pipeline/data and POST /pipeline/metrics (mode-scoped triggers)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,mode", [("/pipeline/data", "data"), ("/pipeline/metrics", "metrics")])
class TestPipelineModeEndpoints:
    def test_403_when_command_token_unset(self, path, mode):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", None):
            resp = client.post(path)
        assert resp.status_code == 403

    def test_401_with_wrong_token(self, path, mode):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(path, headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401

    def test_read_token_never_authorizes(self, path, mode):
        control_api.set_daemon(_make_fake_daemon())
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(path, headers={"Authorization": "Bearer read-tok"})
        assert resp.status_code == 401

    def test_202_with_mode_and_calls_trigger_run(self, path, mode):
        trigger_result = TriggerResult(outcome=TriggerOutcome.ACCEPTED, run_id="new-run-9")
        daemon = _make_fake_daemon(trigger_result=trigger_result)
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.post(path, headers={"Authorization": "Bearer cmd-tok"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["run_id"] == "new-run-9"
        assert body["state"] == "queued"
        assert body["mode"] == mode
        daemon.trigger_run.assert_called_once_with(reason="manual", mode=mode)

    def test_409_when_already_running(self, path, mode):
        trigger_result = TriggerResult(outcome=TriggerOutcome.ALREADY_RUNNING, run_id="existing")
        daemon = _make_fake_daemon(trigger_result=trigger_result)
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.post(path, headers={"Authorization": "Bearer cmd-tok"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["run_id"] == "existing"

    def test_423_when_kill_switch_active(self, path, mode):
        daemon = _make_fake_daemon()
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = True
            mock_ks_cls.return_value.reason.return_value = "halt"
            resp = client.post(path, headers={"Authorization": "Bearer cmd-tok"})
        assert resp.status_code == 423
        assert resp.json()["detail"]["kill_switch_reason"] == "halt"
        daemon.trigger_run.assert_not_called()

    def test_503_when_no_daemon(self, path, mode):
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(path, headers={"Authorization": "Bearer cmd-tok"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /status — run_history + mode (added to the frozen contract)
# ---------------------------------------------------------------------------


class TestStatusRunHistory:
    def test_status_includes_serialized_run_history_with_mode(self):
        last = _make_run_record(run_id="run-2")
        older = _make_run_record(run_id="run-1")
        daemon = _make_fake_daemon(
            status={
                "is_running": False,
                "current_run_id": None,
                "interval_seconds": 60,
                "last_run": last,
                "run_history": [last, older],  # most-recent-first
                "engines_warm": True,
                "started_at": None,
            }
        )
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "STATE_API_TOKEN", None), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.get("/status")
        assert resp.status_code == 200
        body = resp.json()
        assert [r["run_id"] for r in body["run_history"]] == ["run-2", "run-1"]
        # mode is serialized on every RunRecord (default "full" via _make_run_record).
        assert body["run_history"][0]["mode"] == "full"
        assert body["last_run"]["mode"] == "full"

    def test_status_run_history_defaults_to_empty_list(self):
        # A legacy/fake daemon status dict without the key must degrade to [].
        daemon = _make_fake_daemon()  # default status has no run_history key
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "STATE_API_TOKEN", None), \
             mock.patch.object(control_api, "GlobalKillSwitch") as mock_ks_cls:
            mock_ks_cls.return_value.is_active.return_value = False
            resp = client.get("/status")
        assert resp.status_code == 200
        assert resp.json()["run_history"] == []


# ---------------------------------------------------------------------------
# CORS policy
# ---------------------------------------------------------------------------
#
# NOTE (mirrors tests/test_state_api.py::TestCORS): CORSMiddleware captures
# settings.CORS_ALLOWED_ORIGINS at app-construction time (module import), so a
# per-test monkeypatch of settings would NOT retroactively change the
# middleware's allow-list. These tests assert against the REAL default origin
# without patching.


class TestCORS:
    def test_allowed_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_disallowed_origin_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://evil.example"

    def test_post_is_allowed_method(self):
        # Preflight (OPTIONS) request for a POST from an allowed origin.
        resp = client.options(
            "/run",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert resp.status_code == 200
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "POST" in allow_methods

    def test_put_is_allowed_method(self):
        # Preflight (OPTIONS) request for a PUT from an allowed origin --
        # CORSMiddleware captures allow_methods at app-construction time, so
        # this is a REAL preflight against the real app, not a monkeypatch.
        resp = client.options(
            "/interval",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        assert resp.status_code == 200
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "PUT" in allow_methods


# TestCORSLanTailscale (the LAN/Tailscale-origin reflection contract) lives
# in tests/test_cors_lan_tailscale_contract.py, shared byte-for-byte with
# data_api/metrics_api/pilots_api/state_api's identical versions of this test.


# ---------------------------------------------------------------------------
# POST /daemon/restart
# ---------------------------------------------------------------------------


class TestDaemonRestart:
    def test_409_while_a_run_is_active(self):
        daemon = _make_fake_daemon(is_running=True)
        control_api.set_daemon(daemon)
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(
                "/daemon/restart", headers={"Authorization": "Bearer cmd-tok"}
            )
        assert resp.status_code == 409

    def test_requires_command_token(self):
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post("/daemon/restart")
        assert resp.status_code == 401

    def test_200_with_a_daemon_attached_and_idle(self):
        """THE regression test for the daemon.is_running() 500.

        Every real deployment HAS a daemon attached
        (desktop/orchestrator_daemon.py calls control_api.set_daemon(daemon)
        right after daemon.start()), so this -- not the no-daemon case in
        test_schedules_a_real_process_exit_not_just_this_thread below -- is
        the path the webapp Settings screen's "Restart daemon" button
        (webapp/src/screens/Settings.tsx, PR #532) actually hits. With the
        bug present, ``daemon.is_running()`` raises
        ``TypeError: 'bool' object is not callable``; Starlette's TestClient
        default (raise_server_exceptions=True) re-raises it, so this test
        would ERROR with that TypeError rather than return any status code
        at all -- which is what a real client sees as a 500.
        """
        import time as _time

        control_api.set_daemon(_make_fake_daemon(is_running=False))
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api.os, "_exit") as fake_exit:
            resp = client.post(
                "/daemon/restart", headers={"Authorization": "Bearer cmd-tok"}
            )
            assert resp.status_code == 200
            assert resp.json()["restarting"] is True
            _time.sleep(0.7)
            fake_exit.assert_called_once_with(0)

    def test_409_does_not_arm_the_process_exit_timer(self):
        """A rejected restart that still killed the process 0.5s later would
        be the worst possible outcome of the 409 guard, so pin that the 409
        raises BEFORE threading.Timer is armed."""
        import time as _time

        control_api.set_daemon(_make_fake_daemon(is_running=True))
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api.os, "_exit") as fake_exit:
            resp = client.post(
                "/daemon/restart", headers={"Authorization": "Bearer cmd-tok"}
            )
            assert resp.status_code == 409
            assert "currently active" in resp.json()["detail"]
            _time.sleep(0.7)
            fake_exit.assert_not_called()

    def test_schedules_a_real_process_exit_not_just_this_thread(self):
        """os._exit() (an unconditional OS-level exit) is what actually
        fixes the bug this endpoint exists for -- a bare sys.exit() only
        terminates the CALLING thread when that thread isn't the main one,
        which is exactly how uvicorn is hosted inside
        desktop/orchestrator_daemon.py. Patches os._exit to a no-op so this
        test doesn't kill the test runner, and waits out the real
        threading.Timer delay to prove the call actually happens.

        Scope note: the autouse _reset_daemon fixture leaves
        control_api._daemon as None here, so this covers ONLY the
        no-daemon-attached branch of restart_daemon -- deliberately a 200,
        not a 503 like every other endpoint in this module, because this API
        is hosted INSIDE the daemon process, so "no daemon attached" still
        means this process can be exited and respawned. The daemon-attached
        branch (what every real deployment hits) is covered by
        test_200_with_a_daemon_attached_and_idle above; that gap is how the
        daemon.is_running() 500 shipped in the first place.
        """
        import time as _time

        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(control_api.os, "_exit") as fake_exit:
            resp = client.post(
                "/daemon/restart", headers={"Authorization": "Bearer cmd-tok"}
            )
            assert resp.status_code == 200
            assert resp.json()["restarting"] is True
            _time.sleep(0.7)
            fake_exit.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# Background job runner (JOBS_API_ENABLED) -- api/_jobs.py + the /jobs*
# routes on this API. Every launcher is monkeypatched to a fake RunHandle so
# no real subprocess (pytest, preflight_check.py, ...) is ever spawned by
# this test file itself.
# ---------------------------------------------------------------------------

import api._jobs as jobs_module
from shared.orchestrator_runner import StopOutcome


class _FakeHandle:
    """Stands in for shared.orchestrator_runner.RunHandle: only is_running(),
    returncode(), log_path, and backend are ever touched by api/_jobs.py."""

    def __init__(self, *, running=True, rc=None, backend="subprocess", log_path=None):
        self._running = running
        self._rc = rc
        self.backend = backend
        self.log_path = log_path or __import__("pathlib").Path("/tmp/_fake_job.log")

    def is_running(self):
        return self._running

    def returncode(self):
        return self._rc


@pytest.fixture(autouse=True)
def _reset_job_manager():
    jobs_module.job_manager._jobs.clear()
    yield
    jobs_module.job_manager._jobs.clear()


class TestJobsApi:
    def _enabled(self):
        return (
            mock.patch.object(settings, "JOBS_API_ENABLED", True),
            mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"),
        )

    def test_disabled_by_default_even_with_valid_token(self, monkeypatch):
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: _FakeHandle())
        with mock.patch.object(settings, "JOBS_API_ENABLED", False), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(
                "/jobs",
                json={"job_type": "preflight"},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 403

    def test_create_requires_command_token(self, monkeypatch):
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: _FakeHandle())
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post("/jobs", json={"job_type": "preflight"})
        assert resp.status_code == 401

    def test_create_unknown_job_type_is_400(self):
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(
                "/jobs",
                json={"job_type": "not_a_real_type"},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 400

    def test_validation_missing_params_is_400(self):
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(
                "/jobs",
                json={"job_type": "validation"},
                headers={"Authorization": "Bearer cmd-tok"},
            )
        assert resp.status_code == 400

    def test_single_flight_second_launch_is_409(self, monkeypatch):
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: _FakeHandle(running=True))
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            first = client.post("/jobs", json={"job_type": "preflight"}, headers=headers)
            assert first.status_code == 200
            second = client.post("/jobs", json={"job_type": "preflight"}, headers=headers)
        assert second.status_code == 409

    def test_cross_type_single_flight_train_jobs_conflict(self, monkeypatch):
        """TRAIN_LGBM and TRAIN_META share a single-flight group ("train")
        since both write to the same ML registry -- starting a TRAIN_META job
        while a TRAIN_LGBM job is still running must 409, even though they
        are different job_type values (unlike the plain same-type-only check
        every other job type still gets)."""
        monkeypatch.setattr(jobs_module, "launch_train_lgbm", lambda: _FakeHandle(running=True))
        monkeypatch.setattr(jobs_module, "launch_train_meta_labelers", lambda **kw: _FakeHandle(running=True))
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            first = client.post("/jobs", json={"job_type": "train_lgbm"}, headers=headers)
            assert first.status_code == 200
            second = client.post(
                "/jobs", json={"job_type": "train_meta", "params": {"signal": "timeseries_momentum"}},
                headers=headers,
            )
        assert second.status_code == 409

    def test_cross_type_single_flight_reverse_order_also_conflicts(self, monkeypatch):
        """Same as above with TRAIN_META launched first, TRAIN_LGBM second --
        the conflict must not be order-dependent."""
        monkeypatch.setattr(jobs_module, "launch_train_lgbm", lambda: _FakeHandle(running=True))
        monkeypatch.setattr(jobs_module, "launch_train_meta_labelers", lambda **kw: _FakeHandle(running=True))
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            first = client.post("/jobs", json={"job_type": "train_meta"}, headers=headers)
            assert first.status_code == 200
            second = client.post("/jobs", json={"job_type": "train_lgbm"}, headers=headers)
        assert second.status_code == 409

    def test_train_job_does_not_conflict_with_unrelated_job_type(self, monkeypatch):
        """A running TRAIN_LGBM job must not block an unrelated job type
        (e.g. preflight) -- the widened single-flight check is scoped to the
        "train" group only, not job launches in general."""
        monkeypatch.setattr(jobs_module, "launch_train_lgbm", lambda: _FakeHandle(running=True))
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: _FakeHandle(running=True))
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            first = client.post("/jobs", json={"job_type": "train_lgbm"}, headers=headers)
            assert first.status_code == 200
            second = client.post("/jobs", json={"job_type": "preflight"}, headers=headers)
        assert second.status_code == 200

    def test_status_reflects_completion(self, monkeypatch):
        handle = _FakeHandle(running=True)
        monkeypatch.setattr(jobs_module, "launch_pytest", lambda: handle)
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            created = client.post(
                "/jobs",
                json={"job_type": "pytest"},
                headers={"Authorization": "Bearer cmd-tok"},
            ).json()
            job_id = created["job_id"]
            assert created["status"] == "running"

            running = client.get(
                f"/jobs/{job_id}", headers={"Authorization": "Bearer cmd-tok"}
            ).json()
            assert running["status"] == "running"
            assert running["exit_code"] is None

            handle._running = False
            handle._rc = 0
            done = client.get(
                f"/jobs/{job_id}", headers={"Authorization": "Bearer cmd-tok"}
            ).json()
        assert done["status"] == "success"
        assert done["exit_code"] == 0

    def test_get_unknown_job_is_404(self):
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.get(
                "/jobs/does-not-exist", headers={"Authorization": "Bearer cmd-tok"}
            )
        assert resp.status_code == 404

    def test_cancel_unknown_job_is_404(self):
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            resp = client.post(
                "/jobs/does-not-exist/cancel", headers={"Authorization": "Bearer cmd-tok"}
            )
        assert resp.status_code == 404

    def test_cancel_daemon_backed_job_is_400_not_cancellable(self, monkeypatch):
        # backend="daemon" -- launch_orchestrator's ORCHESTRATOR_DAEMON_ENABLED
        # fast path. No local PID to signal; stop_run() itself refuses this.
        monkeypatch.setattr(
            jobs_module, "launch_orchestrator",
            lambda **kw: _FakeHandle(running=True, backend="daemon"),
        )
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            created = client.post(
                "/jobs", json={"job_type": "orchestrator"}, headers=headers
            ).json()
            assert created["cancellable"] is False
            resp = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)
        assert resp.status_code == 400

    def test_cancel_subprocess_backed_job_succeeds(self, monkeypatch):
        handle = _FakeHandle(running=True, backend="subprocess")
        monkeypatch.setattr(jobs_module, "launch_pytest", lambda: handle)
        monkeypatch.setattr(
            jobs_module, "stop_run_detailed",
            lambda h: StopOutcome(stopped=True, already_stopped=False),
        )
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            created = client.post(
                "/jobs", json={"job_type": "pytest"}, headers=headers
            ).json()
            resp = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"job_id": created["job_id"], "cancelled": True}

    def test_cancel_unconfirmed_stop_reports_false_not_success(self, monkeypatch):
        handle = _FakeHandle(running=True, backend="subprocess")
        monkeypatch.setattr(jobs_module, "launch_pytest", lambda: handle)
        monkeypatch.setattr(
            jobs_module, "stop_run_detailed",
            lambda h: StopOutcome(stopped=False, already_stopped=False),
        )
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            created = client.post(
                "/jobs", json={"job_type": "pytest"}, headers=headers
            ).json()
            resp = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is False

    @pytest.mark.parametrize(
        "job_type,launcher_name",
        [
            ("pytest", "launch_pytest"),
            ("gravity", "launch_gravity_audit"),
            ("preflight", "launch_preflight"),
            ("validation", "launch_validation_run"),
        ],
    )
    def test_cancel_completed_job_returns_false_and_preserves_status(
        self, job_type, launcher_name, monkeypatch
    ):
        # A completed job (running=False, rc=0) must not be marked cancelled
        # when a late cancel request arrives; status stays "success".
        handle = _FakeHandle(running=False, rc=0, backend="subprocess")
        monkeypatch.setattr(jobs_module, launcher_name, lambda *args, **kwargs: handle)
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            payload = {"job_type": job_type}
            if job_type == "validation":
                payload["params"] = {
                    "strategies": ["trend_following"],
                    "start": "2020-01-01",
                    "end": "2024-12-31",
                }
            created = client.post("/jobs", json=payload, headers=headers).json()
            resp = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)
            assert resp.status_code == 200
            assert resp.json() == {"job_id": created["job_id"], "cancelled": False}

            # Inspect job status to confirm it was not mutated to "cancelled"
            status_resp = client.get(
                f"/jobs/{created['job_id']}", headers={"Authorization": "Bearer cmd-tok"}
            )
            assert status_resp.status_code == 200
            data = status_resp.json()
            assert data["status"] == "success"
            assert data["exit_code"] == 0
            assert data["is_running"] is False
            assert data["job_type"] == job_type

    def test_double_cancel_running_job_returns_true_and_preserves_cancelled(self, monkeypatch):
        handle = _FakeHandle(running=True, rc=None, backend="subprocess")
        monkeypatch.setattr(jobs_module, "launch_pytest", lambda: handle)
        outcomes = iter([
            StopOutcome(stopped=True, already_stopped=False),  # 1st cancel: genuinely stops it
            StopOutcome(stopped=True, already_stopped=True),  # 2nd cancel: already dead
        ])
        monkeypatch.setattr(jobs_module, "stop_run_detailed", lambda h: next(outcomes))
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            created = client.post(
                "/jobs", json={"job_type": "pytest"}, headers=headers
            ).json()
            job_id = created["job_id"]
            first_resp = client.post(f"/jobs/{job_id}/cancel", headers=headers)
            assert first_resp.status_code == 200
            assert first_resp.json() == {"job_id": job_id, "cancelled": True}

            # Simulate process termination after cancellation
            handle._running = False
            handle._rc = -15

            # Second cancel on the same job (now running=False, cancelled=True) returns 200, cancelled=True
            second_resp = client.post(f"/jobs/{job_id}/cancel", headers=headers)
            assert second_resp.status_code == 200
            assert second_resp.json() == {"job_id": job_id, "cancelled": True}

            # Inspect job status to confirm it reports "cancelled", exit_code=-15, is_running=False
            status_resp = client.get(
                f"/jobs/{job_id}", headers={"Authorization": "Bearer cmd-tok"}
            )
            assert status_resp.status_code == 200
            data = status_resp.json()
            assert data["status"] == "cancelled"
            assert data["exit_code"] == -15
            assert data["is_running"] is False

    @pytest.mark.parametrize("rc", [0, 1])
    def test_cancel_race_with_natural_completion_returns_false_and_preserves_status(
        self, monkeypatch, rc
    ):
        # The process was running when cancel_job examined it, but had ALREADY
        # finished on its own -- with either a clean (rc=0, "success") or a
        # nonzero (rc=1, "failed") exit code -- by the time
        # stop_run_detailed() actually checked. Regression coverage for the
        # residual TOCTOU gap: a prior fix (guard 3, `returncode() == 0`)
        # only caught the rc==0 half of this race, so a job that raced to a
        # nonzero exit was still mislabeled "cancelled". stop_run_detailed()
        # reports already_stopped=True regardless of exit code, so cancel_job
        # must never flip rec.cancelled in EITHER case.
        handle = _FakeHandle(running=True, rc=rc, backend="subprocess")
        monkeypatch.setattr(jobs_module, "launch_pytest", lambda: handle)
        monkeypatch.setattr(
            jobs_module, "stop_run_detailed",
            lambda h: StopOutcome(stopped=True, already_stopped=True),
        )
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            created = client.post(
                "/jobs", json={"job_type": "pytest"}, headers=headers
            ).json()
            job_id = created["job_id"]
            resp = client.post(f"/jobs/{job_id}/cancel", headers=headers)
            assert resp.status_code == 200
            assert resp.json() == {"job_id": job_id, "cancelled": False}

            handle._running = False
            status_resp = client.get(
                f"/jobs/{job_id}", headers={"Authorization": "Bearer cmd-tok"}
            )
            assert status_resp.status_code == 200
            data = status_resp.json()
            assert data["status"] == ("success" if rc == 0 else "failed")
            assert data["status"] != "cancelled"
            assert data["exit_code"] == rc

    def test_stream_unknown_job_is_404(self):
        with mock.patch.object(settings, "JOBS_API_ENABLED", True):
            resp = client.get("/jobs/does-not-exist/stream")
        assert resp.status_code == 404

    def test_stream_requires_token_via_header_or_query(self, monkeypatch, tmp_path):
        log_path = tmp_path / "job.log"
        log_path.write_text("line one\n")
        handle = _FakeHandle(running=False, rc=0, log_path=log_path)
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: handle)
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            created = client.post(
                "/jobs",
                json={"job_type": "preflight"},
                headers={"Authorization": "Bearer cmd-tok"},
            ).json()
            job_id = created["job_id"]

            no_auth = client.get(f"/jobs/{job_id}/stream")
            assert no_auth.status_code == 401

            via_header = client.get(
                f"/jobs/{job_id}/stream", headers={"Authorization": "Bearer read-tok"}
            )
            assert via_header.status_code == 200

            via_query = client.get(f"/jobs/{job_id}/stream?token=read-tok")
            assert via_query.status_code == 200

    def test_stream_yields_log_content_via_offloaded_read(self, monkeypatch, tmp_path):
        """Content-level coverage for the asyncio.to_thread-backed
        ``_read_lines`` helper -- the prior tests only asserted status
        codes and never actually read the SSE body, so a regression in the
        offloaded-read/offset bookkeeping would have gone uncaught."""
        log_path = tmp_path / "job.log"
        log_path.write_text("line one\nline two\n")
        handle = _FakeHandle(running=False, rc=0, log_path=log_path)
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: handle)
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            created = client.post(
                "/jobs",
                json={"job_type": "preflight"},
                headers={"Authorization": "Bearer cmd-tok"},
            ).json()
            job_id = created["job_id"]

            resp = client.get(f"/jobs/{job_id}/stream?token=read-tok")
            assert resp.status_code == 200
            body = resp.text
            assert "id: 0\ndata: line one\n\n" in body
            assert "id: 0\ndata: line two\n\n" in body
            assert "event: end\ndata: Job completed with exit code 0\n\n" in body

            # Resuming from an offset that already covers "line one" (its
            # length + the trailing newline) must yield only "line two" --
            # proves current_offset is threaded through the to_thread call
            # correctly rather than always reading from 0.
            resume_offset = len("line one\n")
            resumed = client.get(
                f"/jobs/{job_id}/stream?token=read-tok&offset={resume_offset}"
            )
            assert resumed.status_code == 200
            resumed_body = resumed.text
            assert "line one" not in resumed_body
            assert f"id: {resume_offset}\ndata: line two\n\n" in resumed_body

    def test_stream_survives_non_missing_stat_error(self, monkeypatch, tmp_path):
        """A non-FileNotFoundError OSError from the size-check stat (a
        dropped network mount, a permission change) must degrade to "no new
        data this tick" and be logged -- not crash the SSE generator with an
        unhandled exception (which would surface as a broken/incomplete
        stream to the client instead of a clean, if quiet, response)."""
        log_path = tmp_path / "job.log"
        log_path.write_text("line one\n")
        handle = _FakeHandle(running=False, rc=0, log_path=log_path)
        monkeypatch.setattr(jobs_module, "launch_preflight", lambda: handle)
        monkeypatch.setattr(
            control_api.os,
            "stat",
            mock.Mock(side_effect=PermissionError("mocked: permission denied")),
        )
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"), \
             mock.patch.object(control_api.logger, "warning") as mock_warning:
            created = client.post(
                "/jobs",
                json={"job_type": "preflight"},
                headers={"Authorization": "Bearer cmd-tok"},
            ).json()
            job_id = created["job_id"]

            resp = client.get(f"/jobs/{job_id}/stream?token=read-tok")

        assert resp.status_code == 200
        body = resp.text
        # No log content was read (the stat failure suppressed the read),
        # but the stream still terminates cleanly with the end event.
        assert "line one" not in body
        assert "event: end\ndata: Job completed with exit code 0\n\n" in body
        assert mock_warning.called


# ---------------------------------------------------------------------------
# Architectural guard
# ---------------------------------------------------------------------------


def test_control_api_never_imports_pipeline_engines_directly():
    """Static guard: api/control_api.py must reach the pipeline ONLY through
    the daemon object -- never import the heavy pipeline engines directly.
    desktop.daemon_runtime and execution.kill_switch ARE allowed here (unlike
    api/state_api.py), since this module's whole purpose is to reach those
    two things.

    Only scans actual `import`/`from ... import` statements (via ast) so
    mentions in docstrings/comments don't false-positive."""
    import ast
    import pathlib

    src = pathlib.Path(control_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    forbidden_modules = {
        "main_orchestrator",
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "evaluation_engine",
    }
    overlap = imported_modules & forbidden_modules
    assert not overlap, f"api/control_api.py must not import {overlap}"


# ---------------------------------------------------------------------------
# Training-status broadcast wiring (POST /jobs -> /ws/training/status)
# ---------------------------------------------------------------------------


def test_no_duplicate_ws_jobs_route_exists():
    """This app must have exactly one job-log-streaming mechanism -- the
    pre-existing SSE ``GET /jobs/{job_id}/stream`` -- never a duplicate WS
    variant living under a ``/ws/jobs/`` path."""
    ws_jobs_paths = [
        route.path for route in control_api.app.routes
        if getattr(route, "path", "").startswith("/ws/jobs/")
    ]
    assert ws_jobs_paths == []


def _all_route_paths(app) -> set:
    """Recursively collect every route path served by *app*, unwrapping
    FastAPI's lazy ``_IncludedRouter`` mount wrapper (``app.include_router``
    no longer eagerly flattens a sub-router's routes into ``app.routes`` in
    the FastAPI/Starlette version this repo pins -- a top-level, un-recursed
    scan of ``app.routes`` silently sees a mounted sub-router's own routes
    as absent, which would make a route-bleed guard test pass for the wrong
    reason)."""
    paths: set = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            stack.extend(original_router.routes)
    return paths


def test_mounts_training_status_ws_route_but_not_the_unrelated_tick_route():
    """Route-bleed regression guard: this app is the ONE process that
    actually runs POST /jobs and the train_lgbm/train_meta job types, so it
    correctly serves /ws/training/status -- but must NOT also serve
    /ws/ticks/{symbol} (api/data_api.py's own live-market-tick-streaming
    capability, unrelated to this daemon process and with no test coverage
    in this context). Both routers used to be one shared ``ws_router`` that
    any mounting process pulled in whole; see api/ws_api.py's docstring."""
    paths = _all_route_paths(control_api.app)
    assert "/ws/training/status" in paths
    assert "/ws/ticks/{symbol}" not in paths


def test_create_job_exception_mapping_never_references_broadcast():
    """Pins that a training-status broadcast failure can never be
    misreported as an HTTP 400/409/403 about the job itself -- the
    broadcast call must live strictly AFTER the
    ``RuntimeError/ValueError/PermissionError`` exception-mapping block,
    never inside it."""
    import inspect
    import re

    source = inspect.getsource(control_api.create_job)

    # Isolate just the exception-mapping try/except block: from the first
    # `try:` immediately preceding `job_manager.start_job` through the last
    # `except PermissionError` clause's body.
    match = re.search(
        r"try:\s*\n\s*rec = job_manager\.start_job.*?except PermissionError as err:\s*\n\s*raise HTTPException\(status_code=403.*?\n",
        source,
        re.DOTALL,
    )
    assert match is not None, "could not locate the exception-mapping block in create_job's source"
    exception_mapping_block = match.group(0)

    assert "ws_api" not in exception_mapping_block
    assert "broadcast" not in exception_mapping_block


def test_daemon_properties_are_never_called_as_methods():
    """Static guard for the whole bug CLASS behind the POST /daemon/restart
    500. Derives the property set from the REAL OrchestratorDaemon (never a
    hardcoded list), then AST-scans api/control_api.py for a Call on one of
    those names against a daemon-ish Name receiver.

    Scoped to ast.Name receivers on purpose: shared.orchestrator_runner.
    RunHandle.is_running() IS a method (gui/orchestrator_runner.py), and its
    call sites here (`rec.handle.is_running()`) have an ast.Attribute
    receiver, so they are correctly never matched. Known limitation,
    accepted: a daemon aliased to some other local name would slip past --
    the real-shaped _FakeDaemon is the runtime half of this defence.
    """
    import ast
    import pathlib

    props = {
        name for name in dir(OrchestratorDaemon)
        if isinstance(getattr(OrchestratorDaemon, name, None), property)
    }
    assert {"is_running", "last_result"} <= props, (
        f"OrchestratorDaemon's property set changed: {props}"
    )

    receivers = {"daemon", "_daemon", "orchestrator_daemon"}
    src_path = pathlib.Path(control_api.__file__)
    offenders = [
        f"{src_path.name}:{node.lineno} {node.func.value.id}.{node.func.attr}()"
        for node in ast.walk(ast.parse(src_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in props
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in receivers
    ]
    assert not offenders, (
        "OrchestratorDaemon @property called as a method (drop the parens): "
        + ", ".join(offenders)
    )


def test_fake_daemon_mirrors_orchestrator_daemon_member_kinds():
    """Pins _FakeDaemon's shape against the real class, so a future
    method<->property flip in desktop/daemon_runtime.py fails HERE (loudly,
    in one place) rather than silently in a handler."""
    fake = _make_fake_daemon()
    for name in ("status", "get_run", "set_interval", "trigger_run", "shutdown"):
        real = getattr(OrchestratorDaemon, name)
        assert not isinstance(real, property), f"{name} became a property; update _FakeDaemon"
        assert callable(real) and callable(getattr(fake, name))

    for name in ("is_running", "last_result"):
        assert isinstance(getattr(OrchestratorDaemon, name), property), (
            f"{name} is no longer a property; update _FakeDaemon AND the AST guard"
        )
        assert isinstance(getattr(_FakeDaemon, name), property)

    loaded = _make_fake_daemon(is_running=True, last_result="rec")
    assert loaded.is_running is True
    assert loaded.last_result == "rec"
    with pytest.raises(TypeError):
        loaded.is_running()  # the production bug, faithfully reproduced by the fake


def test_list_jobs_returns_all_newest_first(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord
    class FakeH:
        def is_running(self): return True
        def returncode(self): return None
        
    job_manager._jobs.clear()
    
    r1 = JobRecord(job_id="j1", job_type=JobType.PREFLIGHT, handle=FakeH(), command_name=None, single_flight_key=None)
    r2 = JobRecord(job_id="j2", job_type=JobType.PYTEST, handle=FakeH(), command_name=None, single_flight_key=None)
    
    # ensure created_at ordering
    r1.created_at = "2020-01-01T00:00:00Z"
    r2.created_at = "2020-01-02T00:00:00Z"
    
    job_manager._jobs["j1"] = r1
    job_manager._jobs["j2"] = r2
    
    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs", headers={"Authorization": "Bearer cmd-tok"})
        
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["job_id"] == "j2"
    assert jobs[1]["job_id"] == "j1"

def test_list_jobs_active_only(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord
    class FakeH:
        def __init__(self, r): self._r = r
        def is_running(self): return self._r
        def returncode(self): return 0 if not self._r else None
        
    job_manager._jobs.clear()
    r1 = JobRecord(job_id="j1", job_type=JobType.PREFLIGHT, handle=FakeH(True), command_name=None, single_flight_key=None)
    r2 = JobRecord(job_id="j2", job_type=JobType.PYTEST, handle=FakeH(False), command_name=None, single_flight_key=None)
    
    job_manager._jobs["j1"] = r1
    job_manager._jobs["j2"] = r2
    
    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs?active_only=true", headers={"Authorization": "Bearer cmd-tok"})
        
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "j1"

def test_list_jobs_limit_clamps(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord
    class FakeH:
        def is_running(self): return True
        def returncode(self): return None
    
    job_manager._jobs.clear()
    for i in range(5):
        job_manager._jobs[f"j{i}"] = JobRecord(job_id=f"j{i}", job_type=JobType.PREFLIGHT, handle=FakeH(), command_name=None, single_flight_key=None)
        
    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs?limit=2", headers={"Authorization": "Bearer cmd-tok"})
        assert len(resp.json()["jobs"]) == 2

def test_post_jobs_conflict_returns_structured_409(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord
    class FakeH:
        def is_running(self): return True
        def returncode(self): return None
    
    job_manager._jobs.clear()
    monkeypatch.setattr("api._jobs.launch_preflight", lambda: FakeH())
    
    j1 = job_manager.start_job(JobType.PREFLIGHT, {})
    
    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.post("/jobs", json={"job_type": "preflight"}, headers={"Authorization": "Bearer cmd-tok"})
        
    assert resp.status_code == 409
    data = resp.json()
    assert data["detail"]["job_id"] == j1.job_id
    assert data["detail"]["job_type"] == JobType.PREFLIGHT.value

def test_get_jobs_respects_gating():
    # If JOBS_API_ENABLED=False, it should be 403
    with mock.patch.object(settings, "JOBS_API_ENABLED", False), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs", headers={"Authorization": "Bearer cmd-tok"})
        assert resp.status_code == 403

    # If STATE_API_TOKEN is set, request without token should be 401
    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        resp = client.get("/jobs")
        assert resp.status_code == 401


# --- Regression coverage for the handle=None "starting" window -------------
# JobManager.start_job places a JobRecord (handle=None) under its lock, releases
# the lock, launches the subprocess, then sets handle afterward -- deliberately,
# so start_job doesn't block the event loop on Popen. GET /jobs, GET /jobs/{id},
# and GET /jobs/{id}/stream previously touched rec.handle.is_running()/.log_path
# directly and would raise an unhandled AttributeError (-> a raw 500, since
# install_redacting_exception_handler only catches HTTPException) if hit during
# that window. These tests construct a JobRecord with handle=None directly to
# exercise exactly that window without needing a real launch race.

def test_list_jobs_includes_starting_job_without_crashing(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord

    job_manager._jobs.clear()
    job_manager._jobs["starting"] = JobRecord(
        job_id="starting", job_type=JobType.PREFLIGHT, handle=None,
        command_name=None, single_flight_key=None,
    )

    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs", headers={"Authorization": "Bearer cmd-tok"})

    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "starting"
    assert jobs[0]["status"] == "starting"
    assert jobs[0]["is_running"] is False


def test_list_jobs_active_only_excludes_starting_job_without_crashing(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord

    job_manager._jobs.clear()
    job_manager._jobs["starting"] = JobRecord(
        job_id="starting", job_type=JobType.PREFLIGHT, handle=None,
        command_name=None, single_flight_key=None,
    )

    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs?active_only=true", headers={"Authorization": "Bearer cmd-tok"})

    assert resp.status_code == 200
    assert resp.json()["jobs"] == []


def test_get_job_status_starting_job_without_crashing(monkeypatch):
    from api._jobs import job_manager, JobType, JobRecord

    job_manager._jobs.clear()
    job_manager._jobs["starting"] = JobRecord(
        job_id="starting", job_type=JobType.PREFLIGHT, handle=None,
        command_name=None, single_flight_key=None,
    )

    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
        resp = client.get("/jobs/starting", headers={"Authorization": "Bearer cmd-tok"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "starting"
    assert body["is_running"] is False
    assert body["exit_code"] is None


def test_stream_job_logs_ends_honestly_when_job_never_starts(monkeypatch):
    """A stream opened on a job that never gets a handle (start_job's launcher
    raised after the record was inserted, or a caller opened the stream in the
    genuine starting window and the job simply never progresses) must end with
    an honest event, never hang forever and never crash on rec.handle.log_path."""
    import api.control_api as control_api_module
    from api._jobs import job_manager, JobType, JobRecord

    job_manager._jobs.clear()
    job_manager._jobs["stuck"] = JobRecord(
        job_id="stuck", job_type=JobType.PREFLIGHT, handle=None,
        command_name=None, single_flight_key=None,
    )

    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
         mock.patch.object(control_api_module, "_JOB_START_WAIT_SECONDS", 0.2):
        with client.stream(
            "GET", "/jobs/stuck/stream",
            headers={"Authorization": "Bearer cmd-tok"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

    assert "did not start" in body
    assert "AttributeError" not in body
