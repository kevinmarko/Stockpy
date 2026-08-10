"""Tests for mcp_oauth_provider.py -- InvestyoOAuthProvider, the
OAuthAuthorizationServerProvider implementation backing
investyo_mcp_server.py's OAuth 2.1 transport.

No pytest-asyncio is installed and no existing test file in this repo uses
``async def test_...`` -- these tests call the provider's async methods via
``asyncio.run(...)``, consistent with that convention.

Real SDK model instances (``OAuthClientInformationFull``,
``AuthorizationParams``) are constructed directly from the SDK classes, never
hand-rolled stand-ins.
"""

import asyncio
import uuid

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull
from starlette.testclient import TestClient

from mcp_oauth_password import hash_password
from mcp_oauth_provider import InvestyoOAuthProvider, register_login_routes
from mcp_oauth_store import McpOAuthStore


def _set_oauth_password(monkeypatch: pytest.MonkeyPatch, value):
    """Bypasses BaseModel.__setattr__'s field-existence check so this test
    passes regardless of whether MCP_OAUTH_PASSWORD is declared on the
    Settings model yet -- pydantic v2 stores field values directly in
    instance.__dict__, and monkeypatch.setitem auto-restores correctly on
    teardown (deletes the key if it didn't exist before, restores the old
    value otherwise)."""
    from settings import settings

    monkeypatch.setitem(settings.__dict__, "MCP_OAUTH_PASSWORD", value)


def _provider() -> tuple[InvestyoOAuthProvider, McpOAuthStore]:
    store = McpOAuthStore(db_url="sqlite:///:memory:")
    provider = InvestyoOAuthProvider(store=store)
    return provider, store


def _client(client_id="client-1", redirect_uri="https://example.com/callback") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


def test_register_and_get_client_round_trip():
    provider, _store = _provider()
    client = _client()

    asyncio.run(provider.register_client(client))
    fetched = asyncio.run(provider.get_client("client-1"))

    assert fetched is not None
    assert fetched.client_id == "client-1"
    assert str(fetched.redirect_uris[0]) == "https://example.com/callback"


def test_get_client_unknown_returns_none():
    provider, _store = _provider()
    assert asyncio.run(provider.get_client("nope")) is None


def test_authorize_returns_login_url_and_pending_round_trips():
    provider, store = _provider()
    client = _client()
    asyncio.run(provider.register_client(client))

    params = AuthorizationParams(
        state="state-xyz",
        scopes=["read"],
        code_challenge="challenge-abc",
        redirect_uri="https://example.com/callback",
        redirect_uri_provided_explicitly=True,
    )

    url = asyncio.run(provider.authorize(client, params))
    assert url.startswith("/login?req=")
    nonce = url.split("req=", 1)[1]
    assert nonce

    pending = store.load_pending_authorization(nonce)
    assert pending is not None
    assert pending["client_id"] == "client-1"
    assert pending["state"] == "state-xyz"
    assert pending["scopes"] == ["read"]
    assert pending["code_challenge"] == "challenge-abc"
    assert pending["redirect_uri"] == "https://example.com/callback"


def test_exchange_authorization_code_returns_token_and_consumes_code():
    provider, store = _provider()
    client = _client()
    asyncio.run(provider.register_client(client))

    import time

    store.save_authorization_code(
        "code-1",
        {
            "client_id": "client-1",
            "redirect_uri": "https://example.com/callback",
            "scopes": ["read"],
            "code_challenge": "challenge-abc",
            "expires_at": time.time() + 120,
        },
    )

    authz_code = asyncio.run(provider.load_authorization_code(client, "code-1"))
    assert authz_code is not None

    token = asyncio.run(provider.exchange_authorization_code(client, authz_code))

    assert token.token_type == "Bearer"
    assert token.access_token
    assert token.refresh_token
    assert token.expires_in == 3600
    assert token.scope == "read"

    # single-use: the code is now unloadable
    assert store.load_authorization_code("code-1") is None
    assert asyncio.run(provider.load_authorization_code(client, "code-1")) is None


def test_exchange_refresh_token_rotates_and_invalidates_old_token():
    provider, store = _provider()
    client = _client()
    asyncio.run(provider.register_client(client))

    import time

    store.save_refresh_token(
        "rt-old",
        {
            "client_id": "client-1",
            "scopes": ["read"],
            "subject": "operator",
            "expires_at": time.time() + 1000,
        },
    )

    old_refresh = asyncio.run(provider.load_refresh_token(client, "rt-old"))
    assert old_refresh is not None

    new_token = asyncio.run(provider.exchange_refresh_token(client, old_refresh, []))

    assert new_token.access_token
    assert new_token.refresh_token
    assert new_token.refresh_token != "rt-old"

    # old refresh token is gone (rotation, not reuse)
    assert store.load_refresh_token("rt-old") is None
    assert asyncio.run(provider.load_refresh_token(client, "rt-old")) is None

    # new refresh token loads fine
    assert store.load_refresh_token(new_token.refresh_token) is not None


def test_load_access_token_unknown_returns_none():
    provider, _store = _provider()
    assert asyncio.run(provider.load_access_token("does-not-exist")) is None


def test_load_access_token_expired_returns_none():
    provider, store = _provider()
    import time

    store.save_access_token(
        "at-expired",
        {"client_id": "client-1", "scopes": [], "expires_at": time.time() - 5},
    )
    assert asyncio.run(provider.load_access_token("at-expired")) is None


def test_load_access_token_valid_returns_model_with_int_expires_at():
    provider, store = _provider()
    import time

    store.save_access_token(
        "at-1",
        {"client_id": "client-1", "scopes": ["read"], "expires_at": time.time() + 3600},
    )
    token = asyncio.run(provider.load_access_token("at-1"))
    assert token is not None
    assert token.token == "at-1"
    assert isinstance(token.expires_at, int)


def test_revoke_token_unwraps_token_object_to_string():
    provider, store = _provider()
    import time

    store.save_access_token(
        "at-1",
        {"client_id": "client-1", "scopes": [], "expires_at": time.time() + 3600},
    )
    token = asyncio.run(provider.load_access_token("at-1"))
    assert token is not None

    asyncio.run(provider.revoke_token(token))

    assert store.load_access_token("at-1") is None


def test_login_password_setitem_helper_roundtrips(monkeypatch: pytest.MonkeyPatch):
    from settings import settings

    _set_oauth_password(monkeypatch, "correct-horse-battery-staple")
    assert settings.MCP_OAUTH_PASSWORD == "correct-horse-battery-staple"


# ---------------------------------------------------------------------------
# Multi-user login_post (settings.MCP_OAUTH_MULTI_USER_ENABLED=True)
#
# login_post is only reachable through register_login_routes' real Starlette
# wiring (it is a closure, not a standalone importable function), so these
# tests drive it end-to-end via a TestClient -- same pattern as
# tests/test_mcp_oauth_login_route.py's _make_client helper, deliberately
# duplicated here (not a shared conftest) to avoid file-contention with a
# concurrent agent, matching that file's own stated convention.
# ---------------------------------------------------------------------------


def _set_multi_user_enabled(monkeypatch: pytest.MonkeyPatch, value: bool):
    from settings import settings

    monkeypatch.setitem(settings.__dict__, "MCP_OAUTH_MULTI_USER_ENABLED", value)


def _hash(password: str) -> str:
    # Small Scrypt params -- correctness of the KDF wiring is
    # test_mcp_oauth_password.py's job, not this file's; keeps this file's
    # login-lockout loops (5 attempts x multiple users) fast.
    return hash_password(password, n=2**4, r=1, p=1)


def _make_multi_user_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _set_multi_user_enabled(monkeypatch, True)
    # See test_mcp_oauth_login_route.py's _make_client docstring for why a
    # tmp_path-backed SQLite FILE is required here (TestClient dispatches
    # through a different thread than the one that constructed the store).
    db_path = tmp_path / f"mcp_oauth_multi_{uuid.uuid4().hex}.db"
    store = McpOAuthStore(db_url=f"sqlite:///{db_path}")
    provider = InvestyoOAuthProvider(store=store)
    mcp = FastMCP("test-oauth-multi-user")
    register_login_routes(mcp, provider)
    app = mcp.streamable_http_app()
    return TestClient(app, follow_redirects=False), store


def _save_pending(store: McpOAuthStore, nonce: str, **overrides) -> dict:
    import time

    data = {
        "client_id": "client-1",
        "redirect_uri": "https://example.com/callback",
        "redirect_uri_provided_explicitly": True,
        "state": "state-abc",
        "scopes": ["read"],
        "code_challenge": "challenge-1",
        "expires_at": time.time() + 600,
    }
    data.update(overrides)
    store.save_pending_authorization(nonce, data)
    return data


def test_multi_user_login_correct_creds_issues_subject_username(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_multi_user_client(monkeypatch, tmp_path)
    store.create_user("alice", _hash("alice-pw"))
    _save_pending(store, "nonce-1")

    resp = client.post(
        "/login?req=nonce-1",
        data={"req": "nonce-1", "username": "alice", "password": "alice-pw"},
    )

    assert resp.status_code == 302
    import urllib.parse

    location = resp.headers["location"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    code = query["code"][0]

    loaded_code = store.load_authorization_code(code)
    assert loaded_code is not None
    assert loaded_code["subject"] == "alice"


def test_multi_user_login_wrong_password_locks_out_only_that_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_multi_user_client(monkeypatch, tmp_path)
    store.create_user("alice", _hash("alice-pw"))
    store.create_user("bob", _hash("bob-pw"))

    for i in range(5):
        _save_pending(store, f"nonce-fail-{i}")
        resp = client.post(
            f"/login?req=nonce-fail-{i}",
            data={"req": f"nonce-fail-{i}", "username": "alice", "password": "wrong"},
        )
        assert resp.status_code == 200

    assert store.is_locked_out("alice") is True
    # bob is completely unaffected by alice's failures.
    assert store.is_locked_out("bob") is False

    _save_pending(store, "nonce-bob")
    bob_resp = client.post(
        "/login?req=nonce-bob",
        data={"req": "nonce-bob", "username": "bob", "password": "bob-pw"},
    )
    assert bob_resp.status_code == 302

    _save_pending(store, "nonce-alice-locked")
    alice_locked_resp = client.post(
        "/login?req=nonce-alice-locked",
        data={"req": "nonce-alice-locked", "username": "alice", "password": "alice-pw"},
    )
    assert alice_locked_resp.status_code == 429


def test_multi_user_login_unknown_username_matches_known_wrong_password_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_multi_user_client(monkeypatch, tmp_path)
    store.create_user("alice", _hash("alice-pw"))

    # Same nonce for both requests -- a failed login attempt does NOT
    # consume the pending authorization (see
    # test_mcp_oauth_login_route.py::test_post_login_wrong_password_shows_generic_error_and_nonce_still_live),
    # so this isolates the comparison to the response body alone rather
    # than incidentally differing because two different nonces are
    # embedded in the rendered form's hidden input / form action.
    _save_pending(store, "nonce-shared")

    unknown_resp = client.post(
        "/login?req=nonce-shared",
        data={"req": "nonce-shared", "username": "nobody", "password": "anything"},
    )
    wrong_pw_resp = client.post(
        "/login?req=nonce-shared",
        data={"req": "nonce-shared", "username": "alice", "password": "wrong-pw"},
    )

    assert unknown_resp.status_code == wrong_pw_resp.status_code == 200
    assert unknown_resp.text == wrong_pw_resp.text
    # Genuinely generic -- doesn't echo back the unknown username.
    assert "nobody" not in unknown_resp.text


def test_multi_user_login_inactive_user_rejected_even_with_correct_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_multi_user_client(monkeypatch, tmp_path)
    store.create_user("alice", _hash("alice-pw"))
    store.set_user_active("alice", False)

    _save_pending(store, "nonce-inactive")
    resp = client.post(
        "/login?req=nonce-inactive",
        data={"req": "nonce-inactive", "username": "alice", "password": "alice-pw"},
    )

    assert resp.status_code == 200
    assert "incorrect" in resp.text.lower()
