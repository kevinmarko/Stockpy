"""
tests/test_brokerage_connect.py
=================================
Tests for the brokerage-connect credential-intake surface:
  - data/brokerage_credentials.py (the dedicated, hard-scoped .env writer)
  - api/pilots_api.py's /brokerage/status, /brokerage/connect, /brokerage/disconnect

Robinhood login is device-approval push (the operator taps "approve" in the
Robinhood app), not a typed TOTP/SMS code — there is no more synchronous,
in-request credential-verification function to unit-test here. Verification
now happens via the async, killable-subprocess login-job flow
(data/robinhood_login.py, api/_rh_login.py) wired into /brokerage/connect and
/brokerage/refresh; that flow gets its own test coverage where it's wired up,
not in this file.

All Robinhood network calls (``r.login`` / ``r.logout``) are monkeypatched —
nothing in this file touches the real Robinhood API. Credential values used in
these tests are synthetic and never asserted to be absent from logs via
substring-search of real secrets (that would defeat the point) — instead we
assert the *mechanism* (only exception type names are logged, never messages
built from the credential args).
"""

from __future__ import annotations

import logging
import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api
import data.brokerage_credentials as brokerage_credentials
import data.robinhood_portfolio as robinhood_portfolio

client = TestClient(pilots_api.app)
# A client whose reported request.client.host is loopback, for the happy path.
loopback_client = TestClient(pilots_api.app, client=("127.0.0.1", 54321))

_CMD_TOKEN = "brokerage-cmd-tok"


def _auth():
    return {"Authorization": f"Bearer {_CMD_TOKEN}"}


# ---------------------------------------------------------------------------
# data/brokerage_credentials.py — the dedicated secret-writer
# ---------------------------------------------------------------------------


class TestBrokerageCredentialsWriter:
    def test_write_rh_credentials_writes_only_allowed_keys(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("UNRELATED_KEY=untouched\n", encoding="utf-8")
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        # write_rh_credentials also assigns the live settings singleton (the
        # value that actually matters for in-process reads — see that
        # function's docstring) — register both attrs with monkeypatch BEFORE
        # the call so teardown restores whatever this session's real .env had,
        # instead of permanently leaking "someone@example.com" into every
        # later test in the same pytest run.
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", None, raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", None, raising=False)

        brokerage_credentials.write_rh_credentials("someone@example.com", "hunter2")

        contents = env_path.read_text(encoding="utf-8")
        assert "RH_USERNAME" in contents
        assert "RH_PASSWORD" in contents
        assert "UNRELATED_KEY=untouched" in contents
        # Mirrored into the live process environment.
        assert os.environ["RH_USERNAME"] == "someone@example.com"
        assert os.environ["RH_PASSWORD"] == "hunter2"
        # Mirrored into the live settings singleton — the one that actually
        # controls data.robinhood_portfolio._fetch_live_snapshot()'s
        # in-worker login path (fixed 2026-08).
        assert brokerage_credentials._settings.RH_USERNAME == "someone@example.com"
        assert brokerage_credentials._settings.RH_PASSWORD == "hunter2"

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_write_rh_credentials_never_touches_a_third_key(self, tmp_path, monkeypatch):
        """This module's allowlist is hard-coded to exactly {RH_USERNAME,
        RH_PASSWORD} and must never grow a third key, whatever that key
        might be — a webapp (re)connect must never clobber some other
        operator-set `.env` value it has no business touching. (This test
        previously exercised this same invariant against a real third
        settings key tied to the old TOTP login flow; that key was retired
        entirely when Robinhood login moved to device-approval push. The
        allowlist logic under test is unchanged, so a plain hypothetical
        key name is used here instead.)"""
        env_path = tmp_path / ".env"
        env_path.write_text("SOME_OTHER_KEY=untouched-value\n", encoding="utf-8")
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.setenv("SOME_OTHER_KEY", "untouched-value")
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", None, raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", None, raising=False)

        brokerage_credentials.write_rh_credentials("user@example.com", "pw")

        assert os.environ["SOME_OTHER_KEY"] == "untouched-value"
        contents = env_path.read_text(encoding="utf-8")
        assert "SOME_OTHER_KEY=untouched-value" in contents

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("SOME_OTHER_KEY", raising=False)

    def test_write_rh_credentials_never_logs_values(self, tmp_path, monkeypatch, caplog):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", None, raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", None, raising=False)

        secret_password = "sUp3rS3cr3tPassw0rd!!"
        with caplog.at_level(logging.DEBUG):
            brokerage_credentials.write_rh_credentials("user@example.com", secret_password)

        assert secret_password not in caplog.text

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_clear_rh_credentials_removes_username_and_password_only(self, tmp_path, monkeypatch):
        """clear_rh_credentials() must never touch a third key it doesn't
        manage — a plain hypothetical key stands in for whatever unrelated
        `.env` value an operator might have set (see
        test_write_rh_credentials_never_touches_a_third_key above for why
        this no longer uses a real retired settings key as the example)."""
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.setenv("SOME_OTHER_KEY", "untouched-value")
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", None, raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", None, raising=False)
        brokerage_credentials.write_rh_credentials("user@example.com", "pw")

        brokerage_credentials.clear_rh_credentials()

        assert "RH_USERNAME" not in os.environ
        assert "RH_PASSWORD" not in os.environ
        # Out of scope for this module — never cleared by it.
        assert os.environ["SOME_OTHER_KEY"] == "untouched-value"
        contents = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        assert "RH_PASSWORD=pw" not in contents

        monkeypatch.delenv("SOME_OTHER_KEY", raising=False)

    def test_clear_rh_credentials_idempotent_when_nothing_set(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", None, raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", None, raising=False)
        # Should not raise even though nothing exists yet.
        brokerage_credentials.clear_rh_credentials()

    def test_rh_credentials_present_reflects_settings(self, monkeypatch):
        """rh_credentials_present() reads the `settings` singleton, NOT
        os.environ (fixed 2026-08) -- pydantic-settings loads .env into
        Settings only, never into the real process environment, so a plain
        os.environ.setenv would be silently ignored by this function."""
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", None, raising=False)
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", None, raising=False)
        assert brokerage_credentials.rh_credentials_present() is False
        monkeypatch.setattr(brokerage_credentials._settings, "RH_USERNAME", "user@example.com")
        monkeypatch.setattr(brokerage_credentials._settings, "RH_PASSWORD", "pw")
        assert brokerage_credentials.rh_credentials_present() is True


# ---------------------------------------------------------------------------
# api/pilots_api.py — GET /brokerage/status (read-only, not flag-gated)
# ---------------------------------------------------------------------------


class TestBrokerageStatus:
    """GET /brokerage/status is a normal read endpoint (require_read_token,
    not require_loopback) -- uses loopback_client like every other read test
    in this file, not the plain non-loopback `client` reserved for
    TestBrokerageConnect/TestBrokerageDisconnect's require_loopback checks."""
    def test_status_not_connected_no_snapshot(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: False
        )

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(settings, "ROBINHOOD_AUTO_REFRESH_ENABLED", True):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
                resp = loopback_client.get("/brokerage/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "connected": False,
            "has_account_snapshot": False,
            "auto_refresh_enabled": True,
        }

    def test_status_connected_with_snapshot(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: True
        )

        class _Store:
            def latest_account_snapshot(self):
                return object()

        with mock.patch.object(settings, "ROBINHOOD_AUTO_REFRESH_ENABLED", True):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
                resp = loopback_client.get("/brokerage/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "connected": True,
            "has_account_snapshot": True,
            "auto_refresh_enabled": True,
        }

    def test_status_auto_refresh_enabled_reflects_settings_value(self, monkeypatch):
        """auto_refresh_enabled mirrors the live settings.ROBINHOOD_AUTO_REFRESH_ENABLED
        value -- the SAME field data/robinhood_portfolio.py's Tier-3 login gate
        actually branches on -- never a hardcoded literal, and read-only (no
        write path exists on this endpoint)."""
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: False
        )

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
            with mock.patch.object(settings, "ROBINHOOD_AUTO_REFRESH_ENABLED", False):
                resp = loopback_client.get("/brokerage/status")
            assert resp.json()["auto_refresh_enabled"] is False

            with mock.patch.object(settings, "ROBINHOOD_AUTO_REFRESH_ENABLED", True):
                resp = loopback_client.get("/brokerage/status")
            assert resp.json()["auto_refresh_enabled"] is True

    def test_status_not_gated_by_brokerage_connect_enabled(self, monkeypatch):
        """Status is read-only and must remain reachable even when connect
        intake itself is disabled — the operator may have set creds by hand."""
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: True
        )

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", False):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
                resp = loopback_client.get("/brokerage/status")
        assert resp.status_code == 200

    def test_status_db_error_degrades_to_false(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: False
        )

        class _BoomStore:
            def latest_account_snapshot(self):
                raise RuntimeError("cold db")

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_BoomStore()):
            resp = loopback_client.get("/brokerage/status")
        assert resp.status_code == 200
        assert resp.json()["has_account_snapshot"] is False


# ---------------------------------------------------------------------------
# api/pilots_api.py — POST /brokerage/connect (three independent gates)
# ---------------------------------------------------------------------------


class TestBrokerageConnectGating:
    def test_403_when_flag_disabled(self):
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", False):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "u", "password": "p", "mfa_code": "s"},
                    headers=_auth(),
                )
        assert resp.status_code == 403

    def test_403_when_token_unset_even_if_flag_enabled(self):
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "u", "password": "p", "mfa_code": "s"},
                )
        assert resp.status_code == 403

    def test_401_wrong_token(self):
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "u", "password": "p", "mfa_code": "s"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_403_when_not_loopback(self):
        """The module-level `client` fixture reports host='testclient', not
        loopback — even with flag on and correct token, it must be rejected."""
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = client.post(
                    "/brokerage/connect",
                    json={"username": "u", "password": "p", "mfa_code": "s"},
                    headers=_auth(),
                )
        assert resp.status_code == 403


class TestBrokerageConnectHappyPath:
    """POST /brokerage/connect no longer blocks on a synchronous verify+
    persist — it starts an async device-approval login job
    (data.robinhood_login, glued in via api._rh_login) and returns that
    job's initial status immediately. These tests mock at the
    pilots_api.rh_login layer (start_connect_job / serialize_job) to verify
    the ENDPOINT's own wiring and gating in isolation, fast and
    deterministically. The real behavior underneath —
    api._rh_login.start_connect_job's background watcher thread persisting
    RH_USERNAME/RH_PASSWORD to .env if and only if the job actually
    succeeds, and never before/on failure — is covered end-to-end in
    tests/test_rh_login_api_glue.py, not here."""

    def test_connect_starts_a_job_and_returns_its_status(self, monkeypatch):
        captured = {}

        def fake_start_connect_job(username, password):
            captured["username"] = username
            captured["password"] = password
            return "the-job-object"

        monkeypatch.setattr(pilots_api.rh_login, "start_connect_job", fake_start_connect_job)
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {
                "job_id": "job-abc123",
                "mode": "connect",
                "state": "running",
                "phase": "starting",
                "error_code": None,
                "seconds_remaining": 180.0,
                "connected": False,
                "has_account_snapshot": False,
            },
        )

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "user@example.com", "password": "hunter2"},
                    headers=_auth(),
                )
        assert resp.status_code == 202
        body = resp.json()
        assert body["job_id"] == "job-abc123"
        assert body["mode"] == "connect"
        assert body["state"] == "running"
        # Credentials reach start_connect_job (which is responsible for
        # verifying + eventually persisting them) exactly as submitted.
        assert captured == {"username": "user@example.com", "password": "hunter2"}

    def test_connect_request_body_no_longer_accepts_mfa_code(self):
        """Device-approval login needs no code from the user — mfa_code was
        removed from BrokerageConnectRequest entirely. An extra field is
        silently ignored by Pydantic (not rejected), so this just documents
        that it does nothing / is not required, rather than asserting a 422."""
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(
                    pilots_api.rh_login, "start_connect_job", lambda u, p: "job"
                ):
                    with mock.patch.object(
                        pilots_api.rh_login,
                        "serialize_job",
                        lambda job: {"job_id": "x", "mode": "connect", "state": "running"},
                    ):
                        resp = loopback_client.post(
                            "/brokerage/connect",
                            json={"username": "user@example.com", "password": "hunter2"},
                            headers=_auth(),
                        )
        assert resp.status_code == 202

    def test_connect_response_never_echoes_credentials(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.rh_login, "start_connect_job", lambda username, password: "job"
        )
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {
                "job_id": "job-abc123",
                "mode": "connect",
                "state": "running",
                "phase": "starting",
                "error_code": None,
                "seconds_remaining": 180.0,
                "connected": False,
                "has_account_snapshot": False,
            },
        )

        secret_password = "sUp3rS3cr3tPassw0rd!!"
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "user@example.com", "password": secret_password},
                    headers=_auth(),
                )
        assert resp.status_code == 202
        assert secret_password not in resp.text


# ---------------------------------------------------------------------------
# api/pilots_api.py — POST /brokerage/disconnect
# ---------------------------------------------------------------------------


class TestBrokerageDisconnect:
    def test_disconnect_gated_same_as_connect(self):
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", False):
            resp = loopback_client.post("/brokerage/disconnect", headers=_auth())
        assert resp.status_code == 403

    def test_disconnect_success_clears_credentials(self, monkeypatch):
        cleared = {"count": 0}
        logged_out = {"count": 0}

        monkeypatch.setattr(
            pilots_api.robinhood_portfolio, "logout", lambda: logged_out.__setitem__("count", 1)
        )
        monkeypatch.setattr(
            pilots_api.brokerage_credentials,
            "clear_rh_credentials",
            lambda: cleared.__setitem__("count", 1),
        )

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/disconnect", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"connected": False}
        assert cleared["count"] == 1
        assert logged_out["count"] == 1

    def test_disconnect_survives_logout_failure(self, monkeypatch):
        cleared = {"count": 0}

        def boom_logout():
            raise RuntimeError("network down")

        monkeypatch.setattr(pilots_api.robinhood_portfolio, "logout", boom_logout)
        monkeypatch.setattr(
            pilots_api.brokerage_credentials,
            "clear_rh_credentials",
            lambda: cleared.__setitem__("count", 1),
        )

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/disconnect", headers=_auth())
        assert resp.status_code == 200
        assert cleared["count"] == 1


# ---------------------------------------------------------------------------
# api/pilots_api.py — POST /brokerage/refresh (three independent gates, a
# DEDICATED BROKERAGE_REFRESH_ENABLED flag distinct from BROKERAGE_CONNECT_ENABLED
# -- see require_brokerage_refresh_enabled's own docstring for why)
# ---------------------------------------------------------------------------


class TestBrokerageRefreshGating:
    def test_403_when_flag_disabled(self):
        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", False):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/refresh", headers=_auth())
        assert resp.status_code == 403

    def test_403_when_token_unset_even_if_flag_enabled(self):
        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
                resp = loopback_client.post("/brokerage/refresh")
        assert resp.status_code == 403

    def test_401_wrong_token(self):
        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/refresh",
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_403_when_not_loopback(self):
        """The module-level `client` fixture reports host='testclient', not
        loopback — even with the flag on and the correct token, it must be
        rejected (mirrors TestBrokerageConnectGating.test_403_when_not_loopback)."""
        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = client.post("/brokerage/refresh", headers=_auth())
        assert resp.status_code == 403

    def test_refresh_not_gated_by_brokerage_connect_enabled(self, monkeypatch):
        """A DEDICATED flag: connect intake being disabled must not block an
        on-demand refresh of already-configured credentials."""
        monkeypatch.setattr(pilots_api.rh_login, "start_refresh_job", lambda: "job")
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {"job_id": "job-x", "mode": "refresh", "state": "running"},
        )
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", False):
            with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
                with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                    resp = loopback_client.post("/brokerage/refresh", headers=_auth())
        assert resp.status_code == 202

    def test_brokerage_refresh_enabled_is_not_gui_writable(self):
        """Mirrors test_automation_writes_enabled_is_not_gui_writable in
        test_pilots_api.py: a GUI bug must never be able to flip this on."""
        assert "BROKERAGE_REFRESH_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "BROKERAGE_REFRESH_ENABLED" not in pilots_api.env_io.SECRET_KEYS

    def test_brokerage_connect_enabled_is_gui_writable(self):
        """BROKERAGE_CONNECT_ENABLED was previously a hand-set-only invariant
        (like its BROKERAGE_REFRESH_ENABLED sibling above) but was made
        GUI-writable by operator decision. It must stay a non-secret allowlisted
        key (/brokerage/connect and /brokerage/disconnect remain gated
        independently by FOLLOW_API_TOKEN and require_loopback, so this flag's
        own writability is not the sole safeguard on credential intake)."""
        assert "BROKERAGE_CONNECT_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "BROKERAGE_CONNECT_ENABLED" not in pilots_api.env_io.SECRET_KEYS


class TestBrokerageRefreshHappyPath:
    """POST /brokerage/refresh, like /connect, now starts an async job
    instead of blocking on fetch_account_snapshot directly. The
    `force=True` guarantee (a refresh must always bypass the cache) moved
    into the worker itself — data/robinhood_login_worker.py hardcodes
    `rp.fetch_account_snapshot(force=True)` for mode="refresh" — and is
    covered by the worker's own tests, not here. Likewise, the stale/live
    snapshot DATA a successful refresh produces is read back via
    `GET /portfolio` (already covered in tests/test_pilots_api.py), not
    returned by this endpoint, which only ever reports job status."""

    def test_refresh_starts_a_job_and_returns_its_status(self, monkeypatch):
        calls = {"count": 0}

        def fake_start_refresh_job():
            calls["count"] += 1
            return "the-job-object"

        monkeypatch.setattr(pilots_api.rh_login, "start_refresh_job", fake_start_refresh_job)
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {
                "job_id": "job-refresh1",
                "mode": "refresh",
                "state": "running",
                "phase": "starting",
                "error_code": None,
                "seconds_remaining": 180.0,
                "connected": True,
                "has_account_snapshot": True,
            },
        )

        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/refresh", headers=_auth())

        assert resp.status_code == 202
        assert calls["count"] == 1
        body = resp.json()
        assert body["job_id"] == "job-refresh1"
        assert body["mode"] == "refresh"
        assert body["state"] == "running"

    def test_refresh_job_start_failure_returns_clean_502(self, monkeypatch):
        """The only thing that can still 502 here is start_refresh_job()
        itself raising (e.g. OSError from subprocess.Popen if process
        creation fails) — an actual login/fetch failure now surfaces later
        via the job's state="failed"/error_code, not a 502 from this
        endpoint. Plain-string detail (mirrors /brokerage/connect's posture):
        no request body / form field exists here for a frontend to
        highlight, so a structured {error, message} dict would only
        round-trip through client.ts's `String(body.detail)` as
        "[object Object]"."""

        def boom():
            raise OSError("fork failed: resource temporarily unavailable, password xyz")

        monkeypatch.setattr(pilots_api.rh_login, "start_refresh_job", boom)

        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/refresh", headers=_auth())

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        # Never leaks the underlying exception text — logged server-side only.
        assert "password" not in detail
        assert "xyz" not in detail

    def test_refresh_never_logs_token(self, monkeypatch, caplog):
        monkeypatch.setattr(pilots_api.rh_login, "start_refresh_job", lambda: "job")
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {"job_id": "job-x", "mode": "refresh", "state": "running"},
        )
        with caplog.at_level(logging.DEBUG):
            with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
                with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                    loopback_client.post("/brokerage/refresh", headers=_auth())
        assert _CMD_TOKEN not in caplog.text


# ---------------------------------------------------------------------------
# api/pilots_api.py — GET /brokerage/login/status/{job_id},
# POST /brokerage/login/cancel/{job_id}
# ---------------------------------------------------------------------------


class TestBrokerageLoginStatus:
    def test_status_unknown_job_returns_404(self, monkeypatch):
        monkeypatch.setattr(pilots_api.rh_login, "get_login_state", lambda job_id: None)
        resp = loopback_client.get("/brokerage/login/status/nope", headers=_auth())
        assert resp.status_code == 404

    def test_status_known_job_returns_serialized_shape(self, monkeypatch):
        monkeypatch.setattr(pilots_api.rh_login, "get_login_state", lambda job_id: "job-obj")
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {
                "job_id": "job-abc",
                "mode": "connect",
                "state": "running",
                "phase": "awaiting_approval",
                "error_code": None,
                "seconds_remaining": 142.3,
                "connected": False,
                "has_account_snapshot": False,
            },
        )
        resp = loopback_client.get("/brokerage/login/status/job-abc", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["phase"] == "awaiting_approval"
        assert body["seconds_remaining"] == 142.3

    def test_status_requires_loopback(self, monkeypatch):
        """require_read_token only checks loopback-ness when STATE_API_TOKEN
        is UNSET (fail-open on loopback, 503 fail-closed otherwise) -- with a
        token configured and presented correctly it passes regardless of
        loopback, so this test sets one to isolate require_loopback's own
        check (mirrors TestBrokerageConnectGating.test_403_when_not_loopback,
        which does the same for require_command_token + require_loopback)."""
        monkeypatch.setattr(pilots_api.rh_login, "get_login_state", lambda job_id: "job-obj")
        monkeypatch.setattr(pilots_api.rh_login, "serialize_job", lambda job: {})
        with mock.patch.object(settings, "STATE_API_TOKEN", _CMD_TOKEN):
            resp = client.get("/brokerage/login/status/job-abc", headers=_auth())
        assert resp.status_code == 403


class TestBrokerageLoginCancel:
    def test_cancel_unknown_job_returns_404(self, monkeypatch):
        def boom(job_id):
            raise KeyError(job_id)

        monkeypatch.setattr(pilots_api.rh_login, "cancel_login", boom)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = loopback_client.post("/brokerage/login/cancel/nope", headers=_auth())
        assert resp.status_code == 404

    def test_cancel_known_job_reports_confirmed_stop(self, monkeypatch):
        monkeypatch.setattr(pilots_api.rh_login, "cancel_login", lambda job_id: True)
        monkeypatch.setattr(pilots_api.rh_login, "get_login_state", lambda job_id: "job-obj")
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {"job_id": "job-abc", "mode": "connect", "state": "cancelled"},
        )
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = loopback_client.post("/brokerage/login/cancel/job-abc", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is True
        assert body["state"] == "cancelled"

    def test_cancel_reports_unconfirmed_stop_honestly(self, monkeypatch):
        """Mirrors api/_jobs.py's cancel_job posture: a confirmed-stop
        failure surfaces as cancelled=False rather than a fabricated success."""
        monkeypatch.setattr(pilots_api.rh_login, "cancel_login", lambda job_id: False)
        monkeypatch.setattr(pilots_api.rh_login, "get_login_state", lambda job_id: "job-obj")
        monkeypatch.setattr(
            pilots_api.rh_login,
            "serialize_job",
            lambda job: {"job_id": "job-abc", "mode": "connect", "state": "running"},
        )
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = loopback_client.post("/brokerage/login/cancel/job-abc", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is False

    def test_cancel_401_missing_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = loopback_client.post("/brokerage/login/cancel/job-abc")
        assert resp.status_code == 401

    def test_cancel_403_when_not_loopback(self):
        """Valid command token, but from a non-loopback client (mirrors
        TestBrokerageConnectGating.test_403_when_not_loopback)."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post("/brokerage/login/cancel/job-abc", headers=_auth())
        assert resp.status_code == 403
