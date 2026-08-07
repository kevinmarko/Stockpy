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

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from mcp_oauth_provider import InvestyoOAuthProvider
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
