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

client = TestClient(control_api.app)


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
