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
from desktop.daemon_runtime import RunRecord, RunState, TriggerOutcome, TriggerResult

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


def _make_fake_daemon(status=None, last_result=None, get_run_map=None, trigger_result=None):
    """Build a MagicMock standing in for OrchestratorDaemon with the
    attributes/methods control_api.py actually touches."""
    daemon = MagicMock(name="fake_daemon")
    daemon.status.return_value = status or {
        "is_running": False,
        "current_run_id": None,
        "interval_seconds": 60,
        "last_run": None,
        "engines_warm": True,
        "started_at": None,
    }
    daemon.last_result = last_result
    get_run_map = get_run_map or {}
    daemon.get_run.side_effect = lambda run_id: get_run_map.get(run_id)
    if trigger_result is not None:
        daemon.trigger_run.return_value = trigger_result
    return daemon


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


class TestCORSLanTailscale:
    """LAN/Tailscale origins are allowed via api.cors.LAN_TAILSCALE_ORIGIN_REGEX
    (additive to the explicit CORS_ALLOWED_ORIGINS list above), scoped to the
    Pilots PWA dev server's port (5173)."""

    def test_lan_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://192.168.1.42:5173"

    def test_tailscale_range_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://100.101.102.5:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://100.101.102.5:5173"

    def test_lan_origin_wrong_port_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5174"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://192.168.1.42:5174"

    def test_public_ip_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://8.8.8.8:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://8.8.8.8:5173"


# ---------------------------------------------------------------------------
# POST /daemon/restart
# ---------------------------------------------------------------------------


class TestDaemonRestart:
    def test_409_while_a_run_is_active(self):
        daemon = _make_fake_daemon()
        daemon.is_running.return_value = True
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

    def test_schedules_a_real_process_exit_not_just_this_thread(self):
        """os._exit() (an unconditional OS-level exit) is what actually
        fixes the bug this endpoint exists for -- a bare sys.exit() only
        terminates the CALLING thread when that thread isn't the main one,
        which is exactly how uvicorn is hosted inside
        desktop/orchestrator_daemon.py. Patches os._exit to a no-op so this
        test doesn't kill the test runner, and waits out the real
        threading.Timer delay to prove the call actually happens."""
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


class _FakeHandle:
    """Stands in for gui.orchestrator_runner.RunHandle: only is_running(),
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
        monkeypatch.setattr(jobs_module, "stop_run", lambda h: True)
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
        monkeypatch.setattr(jobs_module, "stop_run", lambda h: False)
        with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
             mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            headers = {"Authorization": "Bearer cmd-tok"}
            created = client.post(
                "/jobs", json={"job_type": "pytest"}, headers=headers
            ).json()
            resp = client.post(f"/jobs/{created['job_id']}/cancel", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is False

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
