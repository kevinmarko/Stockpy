"""Fully offline unit tests for gui/daemon_client.py.

Every test monkeypatches ``urllib.request.urlopen`` (patched at
``gui.daemon_client.urllib.request.urlopen`` since the module imports
``urllib.request`` and calls it via that qualified path) to return canned
responses or raise canned exceptions -- no real network I/O, no real daemon
process. Mirrors the "patch the network call, assert the function degrades
correctly" house style used in tests/test_market_data.py's TestYFinanceProvider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

import gui.daemon_client as daemon_client
from gui.daemon_client import TriggerResponse
from settings import settings


# ---------------------------------------------------------------------------
# Helpers for building canned urlopen return values / exceptions.
# ---------------------------------------------------------------------------


def _make_response(status: int, body: dict | bytes | None):
    """Build a context-manager-compatible fake response object as returned
    by urllib.request.urlopen(...)."""
    if body is None:
        raw = b""
    elif isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        raw = json.dumps(body).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _http_error(code: int, body: dict | bytes | None):
    if body is None:
        raw = b""
    elif isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        raw = json.dumps(body).encode("utf-8")
    exc = urllib.error.HTTPError(
        url="http://127.0.0.1:8601/x", code=code, msg="err", hdrs=None, fp=None
    )
    exc.read = MagicMock(return_value=raw)
    return exc


@pytest.fixture(autouse=True)
def _reset_settings():
    """Ensure ORCHESTRATOR_API_PORT / ORCHESTRATOR_DAEMON_TOKEN don't leak
    between tests."""
    orig_port = settings.ORCHESTRATOR_API_PORT
    orig_token = settings.ORCHESTRATOR_DAEMON_TOKEN
    yield
    settings.ORCHESTRATOR_API_PORT = orig_port
    settings.ORCHESTRATOR_DAEMON_TOKEN = orig_token


# ---------------------------------------------------------------------------
# daemon_available()
# ---------------------------------------------------------------------------


class TestDaemonAvailable:
    def test_true_on_healthy_response(self):
        resp = _make_response(200, {"status": "ok", "daemon_alive": True})
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            assert daemon_client.daemon_available() is True

    def test_false_on_connection_refused(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            # Called with no surrounding try/except -- must not raise.
            assert daemon_client.daemon_available() is False

    def test_false_on_timeout(self):
        import socket

        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            assert daemon_client.daemon_available() is False

    def test_false_on_malformed_json(self):
        resp = _make_response(200, b"not json{{{")
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            assert daemon_client.daemon_available() is False

    def test_false_when_daemon_alive_false(self):
        resp = _make_response(200, {"status": "ok", "daemon_alive": False})
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            assert daemon_client.daemon_available() is False

    def test_false_on_non_200_status(self):
        resp = _make_response(500, {"status": "error"})
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            assert daemon_client.daemon_available() is False

    def test_no_auth_header_sent(self):
        """Contract: /health requires no auth even when a token is configured."""
        settings.ORCHESTRATOR_DAEMON_TOKEN = "secret-token"
        resp = _make_response(200, {"status": "ok", "daemon_alive": True})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.daemon_available()
        assert "Authorization" not in captured["req"].headers
        assert "authorization" not in captured["req"].headers


# ---------------------------------------------------------------------------
# get_status()
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_returns_parsed_dict_on_200(self):
        payload = {
            "daemon_alive": True,
            "is_running": False,
            "current_run_id": None,
            "interval_seconds": 0,
            "engines_warm": True,
        }
        resp = _make_response(200, payload)
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            result = daemon_client.get_status()
        assert result == payload

    def test_none_on_connection_refused(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert daemon_client.get_status() is None

    def test_none_on_timeout(self):
        import socket

        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            assert daemon_client.get_status() is None

    def test_none_on_malformed_json(self):
        resp = _make_response(200, b"{not valid json")
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            assert daemon_client.get_status() is None

    def test_none_on_401(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(401, {"detail": "Invalid or missing bearer token"}),
        ):
            assert daemon_client.get_status() is None


# ---------------------------------------------------------------------------
# trigger_run()
# ---------------------------------------------------------------------------


class TestTriggerRun:
    def test_ok_on_202(self):
        resp = _make_response(202, {"run_id": "run-123", "state": "queued"})
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            result = daemon_client.trigger_run()
        assert result == TriggerResponse(
            ok=True, run_id="run-123", state="queued", error=None
        )

    def test_already_running_on_409(self):
        body = {"detail": "A run is already in flight.", "run_id": "existing-run-1"}
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(409, body),
        ):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "already_running"
        assert result.existing_run_id == "existing-run-1"

    def test_kill_switch_active_on_423(self):
        body = {
            "detail": "Kill switch active — pipeline triggering is paused.",
            "kill_switch_reason": "manual pause by operator",
        }
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(423, body),
        ):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "kill_switch_active"
        assert result.kill_switch_reason == "manual pause by operator"

    def test_unauthorized_on_401(self):
        body = {"detail": "Invalid or missing bearer token"}
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(401, body),
        ):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "unauthorized"

    def test_command_disabled_on_403(self):
        body = {
            "detail": "Command endpoint disabled: ORCHESTRATOR_DAEMON_TOKEN not configured."
        }
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(403, body),
        ):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "command_disabled"

    def test_unavailable_on_503(self):
        body = {"detail": "Daemon not available."}
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(503, body),
        ):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "unavailable"

    def test_network_error_on_connection_failure(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            # No surrounding try/except: must not raise.
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "network_error"

    def test_network_error_on_timeout(self):
        import socket

        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "network_error"

    def test_never_raises_on_malformed_json_success_body(self):
        resp = _make_response(202, b"not json at all")
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            result = daemon_client.trigger_run()
        assert result.ok is False
        assert result.error == "network_error"

    def test_uses_post_method(self):
        resp = _make_response(202, {"run_id": "r1", "state": "queued"})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.trigger_run()
        assert captured["req"].get_method() == "POST"


# ---------------------------------------------------------------------------
# get_run_status() / get_latest_run()
# ---------------------------------------------------------------------------


class TestGetRunStatus:
    def test_returns_dict_on_200_running_state(self):
        payload = {
            "run_id": "run-abc",
            "state": "running",
            "started_at": "2026-07-07T18:00:00+00:00",
            "finished_at": None,
            "duration_seconds": None,
            "error": None,
            "reason": "manual",
        }
        resp = _make_response(200, payload)
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            result = daemon_client.get_run_status("run-abc")
        assert result == payload
        assert result["finished_at"] is None
        assert result["state"] == "running"

    def test_none_on_404(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(404, {"detail": "No such run."}),
        ):
            assert daemon_client.get_run_status("unknown-run") is None

    def test_none_on_connection_failure(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert daemon_client.get_run_status("run-1") is None

    def test_none_on_malformed_json(self):
        resp = _make_response(200, b"{{malformed")
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            assert daemon_client.get_run_status("run-1") is None

    def test_url_includes_run_id(self):
        resp = _make_response(200, {"run_id": "abc", "state": "succeeded"})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.get_run_status("abc-123")
        assert "/run/abc-123/status" in captured["req"].full_url


class TestGetLatestRun:
    def test_returns_dict_on_200(self):
        payload = {
            "run_id": "run-xyz",
            "state": "succeeded",
            "started_at": "2026-07-07T18:00:00+00:00",
            "finished_at": "2026-07-07T18:00:04+00:00",
            "duration_seconds": 4.2,
            "error": None,
            "reason": "manual",
        }
        resp = _make_response(200, payload)
        with patch("gui.daemon_client.urllib.request.urlopen", return_value=resp):
            result = daemon_client.get_latest_run()
        assert result == payload

    def test_none_on_404(self):
        body = {"detail": "No completed run yet — trigger one via POST /run."}
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=_http_error(404, body),
        ):
            assert daemon_client.get_latest_run() is None

    def test_none_on_connection_failure(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert daemon_client.get_latest_run() is None


# ---------------------------------------------------------------------------
# Header construction
# ---------------------------------------------------------------------------


class TestHeaderConstruction:
    def test_bearer_header_present_when_token_set(self):
        settings.ORCHESTRATOR_DAEMON_TOKEN = "my-secret-token"
        resp = _make_response(200, {"daemon_alive": True, "is_running": False})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.get_status()
        assert captured["req"].headers.get("Authorization") == "Bearer my-secret-token"

    def test_bearer_header_absent_when_token_unset(self):
        settings.ORCHESTRATOR_DAEMON_TOKEN = None
        resp = _make_response(200, {"daemon_alive": True, "is_running": False})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.get_status()
        assert "Authorization" not in captured["req"].headers
        assert "authorization" not in captured["req"].headers

    def test_bearer_header_present_on_trigger_run_post(self):
        settings.ORCHESTRATOR_DAEMON_TOKEN = "token-abc"
        resp = _make_response(202, {"run_id": "r1", "state": "queued"})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.trigger_run()
        assert captured["req"].headers.get("Authorization") == "Bearer token-abc"


# ---------------------------------------------------------------------------
# Port construction
# ---------------------------------------------------------------------------


class TestPortConstruction:
    def test_status_url_uses_configured_port(self):
        settings.ORCHESTRATOR_API_PORT = 9999
        resp = _make_response(200, {"daemon_alive": True})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.get_status()
        assert "127.0.0.1:9999" in captured["req"].full_url

    def test_health_url_uses_configured_port(self):
        settings.ORCHESTRATOR_API_PORT = 12345
        resp = _make_response(200, {"status": "ok", "daemon_alive": True})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.daemon_available()
        assert "127.0.0.1:12345" in captured["req"].full_url

    def test_trigger_run_url_uses_configured_port(self):
        settings.ORCHESTRATOR_API_PORT = 5555
        resp = _make_response(202, {"run_id": "r1", "state": "queued"})
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_fake_urlopen):
            daemon_client.trigger_run()
        assert "127.0.0.1:5555" in captured["req"].full_url


# ---------------------------------------------------------------------------
# Blanket "nothing ever raises" sweep across all five public functions.
# ---------------------------------------------------------------------------


class TestNeverRaises:
    def test_connection_refused_across_all_functions(self):
        with patch(
            "gui.daemon_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            assert daemon_client.daemon_available() is False
            assert daemon_client.get_status() is None
            result = daemon_client.trigger_run()
            assert result.ok is False and result.error == "network_error"
            assert daemon_client.get_run_status("any-id") is None
            assert daemon_client.get_latest_run() is None

    def test_malformed_json_across_all_functions(self):
        def _malformed(*args, **kwargs):
            return _make_response(200, b"totally not json {{{")

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_malformed):
            assert daemon_client.daemon_available() is False
            assert daemon_client.get_status() is None
            assert daemon_client.get_run_status("any-id") is None
            assert daemon_client.get_latest_run() is None

        def _malformed_202(*args, **kwargs):
            return _make_response(202, b"totally not json {{{")

        with patch("gui.daemon_client.urllib.request.urlopen", side_effect=_malformed_202):
            result = daemon_client.trigger_run()
            assert result.ok is False
