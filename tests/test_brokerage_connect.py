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

        brokerage_credentials.write_rh_credentials("someone@example.com", "hunter2", "JBSWY3DPEHPK3PXP")

        contents = env_path.read_text(encoding="utf-8")
        assert "RH_USERNAME" in contents
        assert "RH_PASSWORD" in contents
        assert "RH_MFA_SECRET" in contents
        assert "UNRELATED_KEY=untouched" in contents
        # Mirrored into the live process environment.
        assert os.environ["RH_USERNAME"] == "someone@example.com"
        assert os.environ["RH_PASSWORD"] == "hunter2"
        assert os.environ["RH_MFA_SECRET"] == "JBSWY3DPEHPK3PXP"

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

    def test_write_rh_credentials_empty_mfa_clears_that_key_only(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        brokerage_credentials.write_rh_credentials("user@example.com", "pw", "")

        assert os.environ["RH_USERNAME"] == "user@example.com"
        assert os.environ["RH_PASSWORD"] == "pw"
        assert "RH_MFA_SECRET" not in os.environ

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_write_rh_credentials_never_logs_values(self, tmp_path, monkeypatch, caplog):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        secret_password = "sUp3rS3cr3tPassw0rd!!"
        with caplog.at_level(logging.DEBUG):
            brokerage_credentials.write_rh_credentials("user@example.com", secret_password, "")

        assert secret_password not in caplog.text

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)

    def test_clear_rh_credentials_removes_all_three(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(brokerage_credentials, "ENV_PATH", env_path)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.setenv("RH_MFA_SECRET", "SECRET")
        brokerage_credentials.write_rh_credentials("user@example.com", "pw", "SECRET")

        brokerage_credentials.clear_rh_credentials()

        assert "RH_USERNAME" not in os.environ
        assert "RH_PASSWORD" not in os.environ
        assert "RH_MFA_SECRET" not in os.environ
        contents = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        assert "RH_PASSWORD=pw" not in contents

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
            "user@example.com", "pw", "JBSWY3DPEHPK3PXP"
        )
        assert result is True
        assert calls["login"][0] == "user@example.com"
        assert calls["login"][1] == "pw"
        assert calls["login"][2] is not None  # a real TOTP code was generated
        assert calls["logout"] is True

    def test_missing_mfa_secret_fails_without_interactive_prompt(self, monkeypatch):
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
            "user@example.com", "wrongpw", "JBSWY3DPEHPK3PXP"
        )
        assert result is False

    def test_network_error_returns_false_never_raises(self, monkeypatch):
        def mock_login(*args, **kwargs):
            raise ConnectionError("network down")

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)

        result = robinhood_portfolio.verify_credentials(
            "user@example.com", "pw", "JBSWY3DPEHPK3PXP"
        )
        assert result is False

    def test_never_logs_credential_values(self, monkeypatch, caplog):
        secret_password = "sUp3rS3cr3tPassw0rd!!"

        def mock_login(username, password, store_session=True, mfa_code=None):
            raise RuntimeError(f"login failed for password={password}")

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)

        with caplog.at_level(logging.DEBUG):
            result = robinhood_portfolio.verify_credentials(
                "user@example.com", secret_password, "JBSWY3DPEHPK3PXP"
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
            "user@example.com", "pw", "JBSWY3DPEHPK3PXP"
        )
        assert result is True

    def test_login_still_works_unchanged(self, monkeypatch):
        """Refactoring _login()/_login_with() must not change _login()'s
        existing env-var + interactive-fallback behavior."""
        calls = {}

        def mock_login(username, password, store_session=True, mfa_code=None):
            calls["mfa_code"] = mfa_code
            return {"access_token": "tok"}

        monkeypatch.setattr(robinhood_portfolio.r, "login", mock_login)
        monkeypatch.setenv("RH_USERNAME", "user@example.com")
        monkeypatch.setenv("RH_PASSWORD", "pw")
        monkeypatch.delenv("RH_MFA_SECRET", raising=False)

        robinhood_portfolio._login()  # should fall back to interactive path, no raise
        assert calls["mfa_code"] is None

        monkeypatch.delenv("RH_USERNAME", raising=False)
        monkeypatch.delenv("RH_PASSWORD", raising=False)


# ---------------------------------------------------------------------------
# api/pilots_api.py — GET /brokerage/status (read-only, not flag-gated)
# ---------------------------------------------------------------------------


class TestBrokerageStatus:
    def test_status_not_connected_no_snapshot(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: False
        )

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
            resp = client.get("/brokerage/status")
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
            resp = client.get("/brokerage/status")
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
                resp = client.get("/brokerage/status")
        assert resp.status_code == 200

    def test_status_db_error_degrades_to_false(self, monkeypatch):
        monkeypatch.setattr(
            pilots_api.brokerage_credentials, "rh_credentials_present", lambda: False
        )

        class _BoomStore:
            def latest_account_snapshot(self):
                raise RuntimeError("cold db")

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_BoomStore()):
            resp = client.get("/brokerage/status")
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
                    json={"username": "u", "password": "p", "mfa_secret": "s"},
                    headers=_auth(),
                )
        assert resp.status_code == 403

    def test_403_when_token_unset_even_if_flag_enabled(self):
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "u", "password": "p", "mfa_secret": "s"},
                )
        assert resp.status_code == 403

    def test_401_wrong_token(self):
        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "u", "password": "p", "mfa_secret": "s"},
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
                    json={"username": "u", "password": "p", "mfa_secret": "s"},
                    headers=_auth(),
                )
        assert resp.status_code == 403


class TestBrokerageConnectHappyPath:
    def test_connect_success_persists_credentials(self, monkeypatch):
        written = {}

        def fake_verify(username, password, mfa_secret=""):
            return True

        def fake_write(username, password, mfa_secret=""):
            written["username"] = username
            written["password"] = password
            written["mfa_secret"] = mfa_secret

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
                            "mfa_secret": "JBSWY3DPEHPK3PXP",
                        },
                        headers=_auth(),
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"connected": True, "verified": True, "has_account_snapshot": False}
        assert written == {
            "username": "user@example.com",
            "password": "hunter2",
            "mfa_secret": "JBSWY3DPEHPK3PXP",
        }

    def test_connect_failure_never_persists_credentials(self, monkeypatch):
        write_called = {"count": 0}

        def fake_verify(username, password, mfa_secret=""):
            return False

        def fake_write(username, password, mfa_secret=""):
            write_called["count"] += 1

        monkeypatch.setattr(pilots_api.robinhood_portfolio, "verify_credentials", fake_verify)
        monkeypatch.setattr(pilots_api.brokerage_credentials, "write_rh_credentials", fake_write)

        with mock.patch.object(settings, "BROKERAGE_CONNECT_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = loopback_client.post(
                    "/brokerage/connect",
                    json={"username": "user@example.com", "password": "wrong", "mfa_secret": "SECRET"},
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
                            "mfa_secret": "JBSWY3DPEHPK3PXP",
                        },
                        headers=_auth(),
                    )
        assert resp.status_code == 200
        assert secret_password not in resp.text
        assert "JBSWY3DPEHPK3PXP" not in resp.text


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
