"""
tests/test_brokerage_connect.py
=================================
Tests for the brokerage-connect credential-intake surface:
  - data/brokerage_credentials.py (the dedicated, hard-scoped .env writer)
  - data/robinhood_portfolio.py::verify_credentials (read-only login check)
  - api/pilots_api.py's /brokerage/status, /brokerage/connect, /brokerage/disconnect

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
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        brokerage_credentials.write_rh_credentials("someone@example.com", "hunter2")

        contents = env_path.read_text(encoding="utf-8")
        assert "RH_USERNAME" in contents
        assert "RH_PASSWORD" in contents
        assert "RH_MFA_SECRET" not in contents
        assert "UNRELATED_KEY=untouched" in contents
        # Mirrored into the live process environment.
        assert os.environ["RH_USERNAME"] == "someone@example.com"
        assert os.environ["RH_PASSWORD"] == "hunter2"
        assert "RH_MFA_SECRET" not in os.environ

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_write_rh_credentials_never_touches_existing_mfa_secret(self, tmp_path, monkeypatch):
        """A webapp (re)connect must never clobber an operator-set RH_MFA_SECRET
        used by the main pipeline's own unattended login — this module doesn't
        manage that key at all, write or clear."""
        env_path = tmp_path / ".env"
        env_path.write_text("RH_MFA_SECRET=OPERATORSECRET\n", encoding="utf-8")
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.setenv("RH_MFA_SECRET", "OPERATORSECRET")

        brokerage_credentials.write_rh_credentials("user@example.com", "pw")

        assert os.environ["RH_MFA_SECRET"] == "OPERATORSECRET"
        contents = env_path.read_text(encoding="utf-8")
        assert "RH_MFA_SECRET=OPERATORSECRET" in contents

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

    def test_write_rh_credentials_never_logs_values(self, tmp_path, monkeypatch, caplog):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        secret_password = "sUp3rS3cr3tPassw0rd!!"
        with caplog.at_level(logging.DEBUG):
            brokerage_credentials.write_rh_credentials("user@example.com", secret_password)

        assert secret_password not in caplog.text

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_clear_rh_credentials_removes_username_and_password_only(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.setenv("RH_MFA_SECRET", "OPERATORSECRET")
        brokerage_credentials.write_rh_credentials("user@example.com", "pw")

        brokerage_credentials.clear_rh_credentials()

        assert "RH_USERNAME" not in os.environ
        assert "RH_PASSWORD" not in os.environ
        # RH_MFA_SECRET is out of scope for this module — never cleared by it.
        assert os.environ["RH_MFA_SECRET"] == "OPERATORSECRET"
        contents = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        assert "RH_PASSWORD=pw" not in contents

        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

    def test_clear_rh_credentials_idempotent_when_nothing_set(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)
        # Should not raise even though nothing exists yet.
        brokerage_credentials.clear_rh_credentials()

    def test_rh_credentials_present_reflects_environ(self, monkeypatch):
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        assert brokerage_credentials.rh_credentials_present() is False
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        assert brokerage_credentials.rh_credentials_present() is True
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)


# ---------------------------------------------------------------------------
# data/robinhood_portfolio.py::verify_credentials
# ---------------------------------------------------------------------------


class TestVerifyCredentials:
    def test_success_logs_out_and_returns_true(self, monkeypatch):
        calls = {"login": None, "logout": False}

        def mock_login(username, password, store_session=True, mfa_code=None):
            calls["login"] = (username, password, mfa_code)
            return {"access_token": "tok"}

        def mock_logout():
            calls["logout"] = True

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)
        monkeypatch.setattr(robinhood_portfolio.r, "logout", mock_logout)

        result = robinhood_portfolio.verify_credentials(
            "user@example.com", "pw", "123456"
        )
        assert result is True
        assert calls["login"][0] == "user@example.com"
        assert calls["login"][1] == "pw"
        assert calls["login"][2] == "123456"  # the code is passed through unchanged, no derivation
        assert calls["logout"] is True

    def test_missing_mfa_code_fails_without_interactive_prompt(self, monkeypatch):
        def boom_login(*args, **kwargs):
            raise AssertionError("r.login must not be called without an MFA secret")

        monkeypatch.setattr(robinhood_portfolio.r, "login", boom_login)

        result = robinhood_portfolio.verify_credentials("user@example.com", "pw", "")
        assert result is False

    def test_missing_username_or_password_fails_fast(self):
        assert robinhood_portfolio.verify_credentials("", "pw", "SECRET") is False
        assert robinhood_portfolio.verify_credentials("user@example.com", "", "SECRET") is False

    def test_bad_credentials_returns_false_never_raises(self, monkeypatch):
        def mock_login(username, password, store_session=True, mfa_code=None):
            return {"detail": "invalid credentials"}  # no access_token

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)

        result = robinhood_portfolio.verify_credentials(
            "user@example.com", "wrongpw", "123456"
        )
        assert result is False

    def test_network_error_returns_false_never_raises(self, monkeypatch):
        def mock_login(*args, **kwargs):
            raise ConnectionError("network down")

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)

        result = robinhood_portfolio.verify_credentials(
            "user@example.com", "pw", "123456"
        )
        assert result is False

    def test_never_logs_credential_values(self, monkeypatch, caplog):
        secret_password = "sUp3rS3cr3tPassw0rd!!"

        def mock_login(username, password, store_session=True, mfa_code=None):
            raise RuntimeError(f"login failed for password={password}")

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)

        with caplog.at_level(logging.DEBUG):
            result = robinhood_portfolio.verify_credentials(
                "user@example.com", secret_password, "123456"
            )
        assert result is False
        # The exception message embeds the password, but verify_credentials
        # must only log the exception TYPE, never str(exc).
        assert secret_password not in caplog.text

    def test_logout_failure_does_not_flip_result_to_false(self, monkeypatch):
        def mock_login(username, password, store_session=True, mfa_code=None):
            return {"access_token": "tok"}

        def boom_logout():
            raise RuntimeError("logout network error")

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)
        monkeypatch.setattr(robinhood_portfolio.r, "logout", boom_logout)

        result = robinhood_portfolio.verify_credentials(
            "user@example.com", "pw", "123456"
        )
        assert result is True

    def test_login_with_mfa_secret_ignores_interactivity(self, monkeypatch):
        """With RH_MFA_SECRET set, _login() derives mfa_code via pyotp and
        takes the direct r.login(..., mfa_code=...) path regardless of
        whether stdin happens to be a TTY -- the interactive-vs-headless
        distinction only matters on the missing-secret fallback path."""
        calls = {}

        def mock_login(username, password, store_session=True, mfa_code=None):
            calls["mfa_code"] = mfa_code
            return {"access_token": "tok"}

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)
        monkeypatch.setattr(robinhood_portfolio.sys.stdin, "isatty", lambda: False)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.setenv("RH_MFA_SECRET", "JBSWY3DPEHPK3PXP")

        robinhood_portfolio._login()
        assert calls["mfa_code"]  # a real 6-digit TOTP code, not None/empty

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

    def test_login_falls_back_to_interactive_only_at_a_real_terminal(self, monkeypatch):
        """Missing RH_MFA_SECRET + a genuine TTY (a human running python3
        main.py by hand) still falls through to robin_stocks' interactive
        prompt, exactly as before -- this narrow case is preserved on purpose."""
        calls = {}

        def mock_login(username, password, store_session=True, mfa_code=None):
            calls["mfa_code"] = mfa_code
            return {"access_token": "tok"}

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)
        monkeypatch.setattr(robinhood_portfolio.sys.stdin, "isatty", lambda: True)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        robinhood_portfolio._login()  # falls back to interactive path, no raise
        assert calls["mfa_code"] is None

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_login_raises_immediately_when_headless_and_no_mfa_secret(self, monkeypatch):
        """The actual bug fix: missing RH_MFA_SECRET in a headless context
        (no TTY -- the Pilots API server, main.py under cron/systemd, any app
        bundle launched without a terminal) must raise immediately, never
        fall through to a blocking input() call that hangs forever with zero
        feedback (this is exactly what a real operator hit)."""
        def boom_login(*args, **kwargs):
            raise AssertionError("r.login must not be called at all on this path")

        monkeypatch.setattr(robinhood_portfolio.r, "login", boom_login)
        monkeypatch.setattr(robinhood_portfolio.sys.stdin, "isatty", lambda: False)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        with pytest.raises(ValueError, match="MFA code is required"):
            robinhood_portfolio._login()

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)


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

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
            resp = loopback_client.get("/brokerage/status")
        assert resp.status_code == 200
        assert resp.json() == {"connected": False, "has_account_snapshot": False}

    def test_status_connected_with_snapshot(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: True
        )

        class _Store:
            def latest_account_snapshot(self):
                return object()

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
            resp = loopback_client.get("/brokerage/status")
        assert resp.status_code == 200
        assert resp.json() == {"connected": True, "has_account_snapshot": True}

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
    def test_connect_success_persists_credentials(self, monkeypatch):
        verify_args = {}
        written = {}

        def fake_verify(username, password, mfa_code=""):
            verify_args["username"] = username
            verify_args["password"] = password
            verify_args["mfa_code"] = mfa_code
            return True

        def fake_write(username, password):
            written["username"] = username
            written["password"] = password

        monkeypatch.setattr(pilots_api.robinhood_portfolio, "verify_credentials", fake_verify)
        monkeypatch.setattr(pilots_api.brokerage_credentials, "write_rh_credentials", fake_write)

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
                    resp = loopback_client.post(
                        "/brokerage/connect",
                        json={
                            "username": "user@example.com",
                            "password": "hunter2",
                            "mfa_code": "123456",
                        },
                        headers=_auth(),
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"connected": True, "verified": True, "has_account_snapshot": False}
        # The one-time code reaches verify_credentials but is NOT part of what
        # gets persisted — write_rh_credentials only ever receives username/password.
        assert verify_args == {
            "username": "user@example.com",
            "password": "hunter2",
            "mfa_code": "123456",
        }
        assert written == {
            "username": "user@example.com",
            "password": "hunter2",
        }

    def test_connect_failure_never_persists_credentials(self, monkeypatch):
        write_called = {"count": 0}

        def fake_verify(username, password, mfa_code=""):
            return False

        def fake_write(username, password):
            write_called["count"] += 1

        monkeypatch.setattr(pilots_api.robinhood_portfolio, "verify_credentials", fake_verify)
        monkeypatch.setattr(pilots_api.brokerage_credentials, "write_rh_credentials", fake_write)

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "user@example.com", "password": "wrong", "mfa_code": "654321"},
                    headers=_auth(),
                )
        assert resp.status_code == 401
        assert write_called["count"] == 0
        # No leakage of which field was wrong.
        assert "username" not in resp.json()["detail"].lower()
        assert "password" not in resp.json()["detail"].lower()

    def test_connect_response_never_echoes_credentials(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.robinhood_portfolio, "verify_credentials", lambda *a, **k: True
        )
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "write_rh_credentials", lambda *a, **k: None
        )

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        secret_password = "sUp3rS3cr3tPassw0rd!!"
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
                    resp = loopback_client.post(
                        "/brokerage/connect",
                        json={
                            "username": "user@example.com",
                            "password": secret_password,
                            "mfa_code": "123456",
                        },
                        headers=_auth(),
                    )
        assert resp.status_code == 200
        assert secret_password not in resp.text
        assert "123456" not in resp.text


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


class _FakeRefreshSnap:
    """Minimal AccountSnapshot double: to_dict()/is_stale()/age_hours() is
    everything _serialize_portfolio touches (mirrors test_pilots_api.py's
    test_portfolio_serializes_snapshot _FakeSnap)."""

    def __init__(self, *, stale: bool = False, age_hours: float = 0.02, total_equity: float = 1000.0):
        self._stale = stale
        self._age_hours = age_hours
        self._total_equity = total_equity

    def to_dict(self):
        return {
            "positions": {},
            "buying_power": 250.0,
            "total_equity": self._total_equity,
            "total_dividends": 3.0,
            "fetched_at": "2026-07-31T00:00:00+00:00",
        }

    def is_stale(self):
        return self._stale

    def age_hours(self):
        return self._age_hours


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
        monkeypatch.setattr(
            pilots_api.robinhood_portfolio,
            "fetch_account_snapshot",
            lambda force=False: _FakeRefreshSnap(),
        )
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", False):
            with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
                with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                    resp = loopback_client.post("/brokerage/refresh", headers=_auth())
        assert resp.status_code == 200

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
    def test_refresh_calls_fetch_with_force_true(self, monkeypatch):
        calls = {}

        def fake_fetch(force=False):
            calls["force"] = force
            return _FakeRefreshSnap()

        monkeypatch.setattr(pilots_api.robinhood_portfolio, "fetch_account_snapshot", fake_fetch)

        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/refresh", headers=_auth())

        assert resp.status_code == 200
        assert calls["force"] is True
        body = resp.json()
        assert body["total_equity"] == 1000.0
        assert body["is_stale"] is False
        # Honestly relabeled from _serialize_portfolio's hardcoded "db" (that
        # value is correct for GET /portfolio, which reads HistoricalStore
        # directly -- this endpoint triggered a live fetch instead).
        assert body["source"] == "live"

    def test_refresh_surfaces_stale_degraded_snapshot_as_200(self, monkeypatch):
        """fetch_account_snapshot itself already degrades a live-fetch
        failure to the last cached snapshot when one exists -- that's still a
        real (if stale) snapshot, not an endpoint-level failure."""
        monkeypatch.setattr(
            pilots_api.robinhood_portfolio,
            "fetch_account_snapshot",
            lambda force=False: _FakeRefreshSnap(stale=True, age_hours=48.0),
        )

        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/refresh", headers=_auth())

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_stale"] is True
        assert body["age_hours"] == 48.0
        assert body["source"] == "live"

    def test_refresh_failure_returns_clean_502(self, monkeypatch):
        """Plain-string detail (mirrors /brokerage/connect's plain-401 posture):
        no request body / form field exists here for a frontend to highlight,
        so a structured {error, message} dict would only round-trip through
        client.ts's `String(body.detail)` as "[object Object]"."""
        def boom(force=False):
            raise RuntimeError("no cache and Robinhood login failed: bad password xyz")

        monkeypatch.setattr(pilots_api.robinhood_portfolio, "fetch_account_snapshot", boom)

        with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post("/brokerage/refresh", headers=_auth())

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        # Never leaks the underlying exception text (which could embed
        # credential-adjacent detail) — logged server-side only.
        assert "bad password" not in detail
        assert "xyz" not in detail

    def test_refresh_never_logs_token(self, monkeypatch, caplog):
        monkeypatch.setattr(
            pilots_api.robinhood_portfolio,
            "fetch_account_snapshot",
            lambda force=False: _FakeRefreshSnap(),
        )
        with caplog.at_level(logging.DEBUG):
            with mock.patch.object(settings, "BROKERAGE_REFRESH_ENABLED", True):
                with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                    loopback_client.post("/brokerage/refresh", headers=_auth())
        assert _CMD_TOKEN not in caplog.text
