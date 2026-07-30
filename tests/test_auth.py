"""
tests/test_auth.py
===================
Tests for the shared bearer-token dependencies in ``api/auth.py``, used by
every standalone service in ``api/*.py``.

Exercises the dependencies directly against a minimal throwaway FastAPI app
rather than any one real service, so this file is the single place proving
the loopback-aware fail-open/fail-closed behavior itself -- every real
service's own test file (test_state_api.py, test_data_api.py, ...) only
proves it wired the guard in correctly, using a fixed loopback TestClient
that never exercises the non-loopback branch at all.
"""

from __future__ import annotations

from unittest import mock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from settings import settings
from api.auth import (
    make_command_token_guard,
    require_read_token,
    require_stream_token,
    require_write_token,
)

_app = FastAPI()


@_app.get("/read", dependencies=[Depends(require_read_token)])
def _read() -> dict:
    return {"ok": True}


@_app.get("/stream", dependencies=[Depends(require_stream_token)])
def _stream() -> dict:
    return {"ok": True}


@_app.get("/write", dependencies=[Depends(require_write_token)])
def _write() -> dict:
    return {"ok": True}


_require_test_command_token = make_command_token_guard(
    "ORCHESTRATOR_DAEMON_TOKEN", "disabled"
)


@_app.get("/command", dependencies=[Depends(_require_test_command_token)])
def _command() -> dict:
    return {"ok": True}


loopback_client = TestClient(_app, client=("127.0.0.1", 51000))
lan_client = TestClient(_app, client=("192.168.1.42", 51000))


class TestRequireReadTokenLoopback:
    def test_loopback_fail_open_when_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = loopback_client.get("/read")
        assert resp.status_code == 200

    def test_non_loopback_fail_closed_when_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = lan_client.get("/read")
        assert resp.status_code == 503
        assert "STATE_API_TOKEN is unset" in resp.json()["detail"]

    def test_non_loopback_succeeds_with_correct_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "lan-tok"):
            resp = lan_client.get("/read", headers={"Authorization": "Bearer lan-tok"})
        assert resp.status_code == 200

    def test_non_loopback_401_with_no_token_presented_once_configured(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "lan-tok"):
            resp = lan_client.get("/read")
        assert resp.status_code == 401

    def test_non_loopback_401_with_wrong_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "lan-tok"):
            resp = lan_client.get("/read", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_loopback_401_with_wrong_token_once_configured(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            resp = loopback_client.get("/read", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


class TestRequireStreamTokenLoopback:
    """Same loopback posture as require_read_token, plus the ?token= query
    param fallback (EventSource can't set an Authorization header)."""

    def test_non_loopback_fail_closed_when_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = lan_client.get("/stream")
        assert resp.status_code == 503

    def test_non_loopback_accepts_token_via_query_param(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "lan-tok"):
            resp = lan_client.get("/stream?token=lan-tok")
        assert resp.status_code == 200

    def test_non_loopback_401_with_wrong_query_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "lan-tok"):
            resp = lan_client.get("/stream?token=wrong")
        assert resp.status_code == 401


class TestRequireWriteTokenAlwaysFailClosed:
    """require_write_token has no loopback exception at all -- unlike reads,
    a write/compute endpoint must never be reachable with no token configured,
    regardless of where the request comes from."""

    def test_loopback_still_fails_closed_when_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = loopback_client.get("/write")
        assert resp.status_code == 403

    def test_non_loopback_fails_closed_when_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = lan_client.get("/write")
        assert resp.status_code == 403

    def test_succeeds_with_correct_token_from_either_origin(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
            r1 = loopback_client.get("/write", headers={"Authorization": "Bearer secret"})
            r2 = lan_client.get("/write", headers={"Authorization": "Bearer secret"})
        assert r1.status_code == 200
        assert r2.status_code == 200


class TestMakeCommandTokenGuard:
    """Command-token guards are already always fail-closed and don't
    distinguish loopback at all -- confirming that stays true here."""

    def test_disabled_when_unset_regardless_of_origin(self):
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", None):
            r1 = loopback_client.get("/command")
            r2 = lan_client.get("/command")
        assert r1.status_code == 403
        assert r2.status_code == 403

    def test_succeeds_with_correct_token_from_either_origin(self):
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"):
            r1 = loopback_client.get("/command", headers={"Authorization": "Bearer cmd-tok"})
            r2 = lan_client.get("/command", headers={"Authorization": "Bearer cmd-tok"})
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_does_not_accept_a_different_command_token_setting(self):
        """A token scoped to a DIFFERENT command surface (e.g.
        FOLLOW_API_TOKEN) must never also unlock this one."""
        with mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "cmd-tok"), \
             mock.patch.object(settings, "FOLLOW_API_TOKEN", "follow-tok"):
            resp = loopback_client.get("/command", headers={"Authorization": "Bearer follow-tok"})
        assert resp.status_code == 401
