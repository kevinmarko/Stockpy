"""Tests for mcp_oauth_store.py -- the durable persistence layer backing the
InvestYo MCP OAuth 2.1 authorization server (mcp_oauth_provider.py).

Mirrors tests/test_cap_audit_store.py's convention: a fresh
``sqlite:///:memory:`` store per test, no tmp_path needed for simple CRUD
coverage.
"""

import time

import pytest

import mcp_oauth_store as oauth_store
from mcp_oauth_store import McpOAuthStore


def _store() -> McpOAuthStore:
    return McpOAuthStore(db_url="sqlite:///:memory:")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def test_register_and_get_client_round_trip():
    store = _store()
    store.register_client(
        {
            "client_id": "client-1",
            "client_secret": "secret-1",
            "redirect_uris": ["https://example.com/callback", "https://example.com/callback2"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "contacts": ["dev@example.com"],
            "client_name": "Test Client",
        }
    )

    row = store.get_client("client-1")
    assert row is not None
    assert row["client_id"] == "client-1"
    assert row["client_secret"] == "secret-1"
    assert row["redirect_uris"] == ["https://example.com/callback", "https://example.com/callback2"]
    assert row["grant_types"] == ["authorization_code", "refresh_token"]
    assert row["response_types"] == ["code"]
    assert row["contacts"] == ["dev@example.com"]
    assert row["client_name"] == "Test Client"


def test_get_client_unknown_returns_none():
    store = _store()
    assert store.get_client("does-not-exist") is None


def test_register_client_upserts_by_client_id():
    store = _store()
    store.register_client(
        {"client_id": "client-1", "redirect_uris": ["https://a.example/cb"], "client_name": "First"}
    )
    store.register_client(
        {"client_id": "client-1", "redirect_uris": ["https://b.example/cb"], "client_name": "Second"}
    )

    row = store.get_client("client-1")
    assert row["client_name"] == "Second"
    assert row["redirect_uris"] == ["https://b.example/cb"]

    with oauth_store.session_scope(store.Session) as session:
        count = session.query(oauth_store.OAuthClient).count()
    assert count == 1


# ---------------------------------------------------------------------------
# Pending authorizations
# ---------------------------------------------------------------------------


def test_pending_authorization_save_load_delete():
    store = _store()
    store.save_pending_authorization(
        "nonce-1",
        {
            "client_id": "client-1",
            "redirect_uri": "https://example.com/callback",
            "redirect_uri_provided_explicitly": True,
            "state": "xyz",
            "scopes": ["read"],
            "code_challenge": "challenge-1",
            "expires_at": time.time() + 600,
        },
    )

    row = store.load_pending_authorization("nonce-1")
    assert row is not None
    assert row["client_id"] == "client-1"
    assert row["redirect_uri"] == "https://example.com/callback"
    assert row["state"] == "xyz"
    assert row["scopes"] == ["read"]
    assert row["code_challenge"] == "challenge-1"

    store.delete_pending_authorization("nonce-1")
    assert store.load_pending_authorization("nonce-1") is None


def test_pending_authorization_expired_unloadable():
    store = _store()
    store.save_pending_authorization(
        "nonce-expired",
        {
            "client_id": "client-1",
            "redirect_uri": "https://example.com/callback",
            "code_challenge": "challenge-1",
            "expires_at": time.time() - 10,  # already past
        },
    )
    assert store.load_pending_authorization("nonce-expired") is None


def test_load_pending_authorization_unknown_returns_none():
    store = _store()
    assert store.load_pending_authorization("no-such-nonce") is None


# ---------------------------------------------------------------------------
# Authorization codes
# ---------------------------------------------------------------------------


def test_authorization_code_single_use():
    store = _store()
    store.save_authorization_code(
        "code-1",
        {
            "client_id": "client-1",
            "redirect_uri": "https://example.com/callback",
            "scopes": ["read", "write"],
            "code_challenge": "challenge-1",
            "expires_at": time.time() + 120,
        },
    )

    row = store.load_authorization_code("code-1")
    assert row is not None
    assert row["client_id"] == "client-1"
    assert row["scopes"] == ["read", "write"]

    store.delete_authorization_code("code-1")
    assert store.load_authorization_code("code-1") is None


def test_authorization_code_expired_unloadable():
    store = _store()
    store.save_authorization_code(
        "code-expired",
        {
            "client_id": "client-1",
            "redirect_uri": "https://example.com/callback",
            "scopes": [],
            "code_challenge": "challenge-1",
            "expires_at": time.time() - 5,
        },
    )
    assert store.load_authorization_code("code-expired") is None


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------


def test_access_token_save_load():
    store = _store()
    store.save_access_token(
        "at-1",
        {
            "client_id": "client-1",
            "scopes": ["read"],
            "subject": "operator",
            "expires_at": time.time() + 3600,
        },
    )

    row = store.load_access_token("at-1")
    assert row is not None
    assert row["client_id"] == "client-1"
    assert row["scopes"] == ["read"]
    assert row["subject"] == "operator"


def test_access_token_expired_unloadable():
    store = _store()
    store.save_access_token(
        "at-expired",
        {
            "client_id": "client-1",
            "scopes": [],
            "expires_at": time.time() - 1,
        },
    )
    assert store.load_access_token("at-expired") is None


def test_access_token_never_expires_when_expires_at_none():
    store = _store()
    store.save_access_token(
        "at-forever",
        {
            "client_id": "client-1",
            "scopes": [],
            "expires_at": None,
        },
    )
    row = store.load_access_token("at-forever")
    assert row is not None


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


def test_refresh_token_save_load_delete():
    store = _store()
    store.save_refresh_token(
        "rt-1",
        {
            "client_id": "client-1",
            "scopes": ["read"],
            "subject": "operator",
            "expires_at": time.time() + 1000,
        },
    )

    row = store.load_refresh_token("rt-1")
    assert row is not None
    assert row["client_id"] == "client-1"

    store.delete_refresh_token("rt-1")
    assert store.load_refresh_token("rt-1") is None


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revoke_token_deletes_access_token():
    store = _store()
    store.save_access_token("at-1", {"client_id": "c", "scopes": [], "expires_at": time.time() + 3600})
    store.revoke_token("at-1")
    assert store.load_access_token("at-1") is None


def test_revoke_token_deletes_refresh_token():
    store = _store()
    store.save_refresh_token("rt-1", {"client_id": "c", "scopes": [], "expires_at": time.time() + 3600})
    store.revoke_token("rt-1")
    assert store.load_refresh_token("rt-1") is None


def test_revoke_token_unknown_is_idempotent_noop():
    store = _store()
    # Must not raise -- RFC 7009: revoking an already-invalid/unknown token
    # is not an error.
    store.revoke_token("does-not-exist")


# ---------------------------------------------------------------------------
# purge_expired
# ---------------------------------------------------------------------------


def test_purge_expired_removes_only_expired_rows():
    store = _store()
    now = time.time()

    store.save_pending_authorization(
        "pending-expired",
        {"client_id": "c", "redirect_uri": "https://x/cb", "code_challenge": "cc", "expires_at": now - 10},
    )
    store.save_pending_authorization(
        "pending-valid",
        {"client_id": "c", "redirect_uri": "https://x/cb", "code_challenge": "cc", "expires_at": now + 600},
    )

    store.save_authorization_code(
        "code-expired",
        {"client_id": "c", "redirect_uri": "https://x/cb", "scopes": [], "code_challenge": "cc", "expires_at": now - 5},
    )
    store.save_authorization_code(
        "code-valid",
        {"client_id": "c", "redirect_uri": "https://x/cb", "scopes": [], "code_challenge": "cc", "expires_at": now + 120},
    )

    store.save_access_token("at-expired", {"client_id": "c", "scopes": [], "expires_at": now - 1})
    store.save_access_token("at-valid", {"client_id": "c", "scopes": [], "expires_at": now + 3600})
    store.save_access_token("at-forever", {"client_id": "c", "scopes": [], "expires_at": None})

    store.save_refresh_token("rt-expired", {"client_id": "c", "scopes": [], "expires_at": now - 1})
    store.save_refresh_token("rt-valid", {"client_id": "c", "scopes": [], "expires_at": now + 1000})
    store.save_refresh_token("rt-forever", {"client_id": "c", "scopes": [], "expires_at": None})

    store.purge_expired()

    with oauth_store.session_scope(store.Session) as session:
        pending_nonces = {r.nonce for r in session.query(oauth_store.OAuthPendingAuthorization).all()}
        codes = {r.code for r in session.query(oauth_store.OAuthAuthorizationCode).all()}
        access_tokens = {r.token for r in session.query(oauth_store.OAuthAccessToken).all()}
        refresh_tokens = {r.token for r in session.query(oauth_store.OAuthRefreshToken).all()}

    assert pending_nonces == {"pending-valid"}
    assert codes == {"code-valid"}
    assert access_tokens == {"at-valid", "at-forever"}
    assert refresh_tokens == {"rt-valid", "rt-forever"}


# ---------------------------------------------------------------------------
# Login lockout
# ---------------------------------------------------------------------------


def test_login_lockout_after_threshold_failures():
    store = _store()
    assert store.is_locked_out() is False

    for _ in range(oauth_store.LOGIN_LOCKOUT_THRESHOLD - 1):
        crossed = store.record_login_failure()
        assert crossed is False
        assert store.is_locked_out() is False

    crossed = store.record_login_failure()
    assert crossed is True
    assert store.is_locked_out() is True


def test_reset_login_state_clears_lockout():
    store = _store()
    for _ in range(oauth_store.LOGIN_LOCKOUT_THRESHOLD):
        store.record_login_failure()
    assert store.is_locked_out() is True

    store.reset_login_state()
    assert store.is_locked_out() is False


def test_lockout_self_expires(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    old_now = 1_000_000.0
    monkeypatch.setattr(oauth_store.time, "time", lambda: old_now)
    for _ in range(oauth_store.LOGIN_LOCKOUT_THRESHOLD):
        store.record_login_failure()
    assert store.is_locked_out() is True

    # Move time forward past LOGIN_LOCKOUT_SECONDS, then let real time
    # resume for the final assertion.
    monkeypatch.setattr(
        oauth_store.time, "time", lambda: old_now + oauth_store.LOGIN_LOCKOUT_SECONDS + 1
    )
    assert store.is_locked_out() is False

    monkeypatch.undo()
    assert store.is_locked_out() is False
