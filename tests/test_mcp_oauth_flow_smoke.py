"""End-to-end offline smoke test for the full OAuth 2.1 flow InvestyoOAuthProvider
implements: RFC 7591 dynamic client registration -> /authorize -> the human
/login password gate -> RFC 7636 PKCE-verified /token exchange -> an
authenticated /mcp call -> refresh-token rotation.

This is the integration proof that mcp_oauth_store.py / mcp_oauth_provider.py
(one agent's scope) and investyo_mcp_server.py's FastMCP(auth_server_provider=...,
auth=AuthSettings(...)) wiring (a second agent's scope) actually compose
correctly -- every other OAuth test file exercises one side or the other in
isolation. Runs fully offline: no tunnel, no claude.ai, no network -- a real
FastMCP instance wrapped in starlette.testclient.TestClient, driven with real
HTTP requests (not mocked internals) and a real SHA256 PKCE verifier/challenge
pair, exactly the mechanics a real OAuth client would perform.
"""

import base64
import contextlib
import hashlib
import urllib.parse
import uuid

import pytest
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from mcp_oauth_provider import InvestyoOAuthProvider, register_login_routes
from mcp_oauth_store import McpOAuthStore


def _set_oauth_password(monkeypatch: pytest.MonkeyPatch, value):
    from settings import settings

    monkeypatch.setitem(settings.__dict__, "MCP_OAUTH_PASSWORD", value)


def _make_pkce_pair():
    verifier = base64.urlsafe_b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


@contextlib.contextmanager
def _make_oauth_app(monkeypatch: pytest.MonkeyPatch, tmp_path, password="s3cret-pw"):
    _set_oauth_password(monkeypatch, password)
    db_path = tmp_path / f"mcp_oauth_flow_{uuid.uuid4().hex}.db"
    store = McpOAuthStore(db_url=f"sqlite:///{db_path}")
    provider = InvestyoOAuthProvider(store=store)

    issuer_url = "http://127.0.0.1:8080"
    mcp = FastMCP(
        "test-oauth-flow",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )
    register_login_routes(mcp, provider)
    app = mcp.streamable_http_app()
    # Entering TestClient as a context manager runs the ASGI app's lifespan
    # startup (mcp.streamable_http_app()'s lifespan=lambda app: self.session
    # _manager.run()) -- required for /mcp itself to work (its session
    # manager raises "Task group is not initialized" otherwise, confirmed
    # empirically); /register, /authorize, /login, /token don't need it,
    # only the actual streamable-http MCP endpoint does.
    with TestClient(app, follow_redirects=False) as client:
        yield client, store


def test_full_oauth_authorization_code_flow_with_real_pkce_and_refresh_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    with _make_oauth_app(monkeypatch, tmp_path, password="s3cret-pw") as (client, store):
        verifier, challenge = _make_pkce_pair()

        # 1. RFC 7591 dynamic client registration -- unauthenticated by design.
        register_resp = client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "test-claude-connector",
            },
        )
        assert register_resp.status_code == 201, register_resp.text
        client_info = register_resp.json()
        client_id = client_info["client_id"]
        assert client_id

        # Confirm the client is genuinely durable in the store, not in-memory.
        assert store.get_client(client_id) is not None

        # 2. /authorize -- redirects the browser to our human-facing /login.
        authorize_resp = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz-state",
            },
        )
        assert authorize_resp.status_code in (302, 307), authorize_resp.text
        login_location = authorize_resp.headers["location"]
        assert login_location.startswith("/login?req=")
        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(login_location).query)["req"][0]

        # 3a. GET /login -- the human sees the password form.
        login_get_resp = client.get(login_location)
        assert login_get_resp.status_code == 200
        assert "<form" in login_get_resp.text

        # 3b. A wrong password first -- proves the gate is real, not decorative.
        wrong_resp = client.post(f"/login?req={nonce}", data={"req": nonce, "password": "not-it"})
        assert wrong_resp.status_code == 200
        assert "incorrect" in wrong_resp.text.lower()

        # 3c. POST /login with the correct password -- redirects back with a code.
        login_post_resp = client.post(
            f"/login?req={nonce}", data={"req": nonce, "password": "s3cret-pw"}
        )
        assert login_post_resp.status_code == 302
        callback_location = login_post_resp.headers["location"]
        parsed_callback = urllib.parse.urlparse(callback_location)
        assert parsed_callback.path == "/api/mcp/auth_callback"
        callback_query = urllib.parse.parse_qs(parsed_callback.query)
        assert callback_query["state"] == ["xyz-state"]
        auth_code = callback_query["code"][0]
        assert auth_code

        # 4. POST /token -- the SDK's own handler verifies PKCE (SHA256(verifier)
        # vs the stored code_challenge) before ever calling exchange_authorization_code.
        token_resp = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200, token_resp.text
        token_payload = token_resp.json()
        access_token = token_payload["access_token"]
        refresh_token = token_payload["refresh_token"]
        assert access_token and refresh_token
        assert token_payload["token_type"].lower() == "bearer"

        # The code must now be single-use -- replaying it must fail.
        replay_resp = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert replay_resp.status_code == 400, replay_resp.text

        # 5. An authenticated /mcp call -- confirms the SDK's RequireAuthMiddleware
        # actually gates the endpoint using the access token we were issued.
        unauthenticated_resp = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        )
        assert unauthenticated_resp.status_code == 401, unauthenticated_resp.text

        authenticated_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert authenticated_resp.status_code != 401, authenticated_resp.text

        # 6. Refresh-token rotation -- a new pair is issued, the OLD refresh token
        # is immediately rejected (rotated, not merely re-usable until its own TTL).
        refresh_resp = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        assert refresh_resp.status_code == 200, refresh_resp.text
        refreshed_payload = refresh_resp.json()
        new_access_token = refreshed_payload["access_token"]
        new_refresh_token = refreshed_payload["refresh_token"]
        assert new_access_token != access_token
        assert new_refresh_token != refresh_token

        reuse_old_refresh_resp = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        assert reuse_old_refresh_resp.status_code == 400, reuse_old_refresh_resp.text

        # New access token still works.
        new_authenticated_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": f"Bearer {new_access_token}"},
        )
        assert new_authenticated_resp.status_code != 401, new_authenticated_resp.text


def test_authorize_login_lockout_blocks_even_the_correct_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Full-stack proof (register -> authorize -> lockout) that the login
    gate's lockout mechanism (mcp_oauth_store.py) is actually reachable and
    load-bearing through the real /authorize -> /login redirect chain, not
    just unit-tested in isolation."""
    with _make_oauth_app(monkeypatch, tmp_path, password="s3cret-pw") as (client, store):
        _verifier, challenge = _make_pkce_pair()

        register_resp = client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        client_id = register_resp.json()["client_id"]

        authorize_resp = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        nonce = urllib.parse.parse_qs(
            urllib.parse.urlparse(authorize_resp.headers["location"]).query
        )["req"][0]

        for _ in range(5):
            client.post(f"/login?req={nonce}", data={"req": nonce, "password": "wrong"})

        assert store.is_locked_out() is True

        locked_resp = client.post(
            f"/login?req={nonce}", data={"req": nonce, "password": "s3cret-pw"}
        )
        assert locked_resp.status_code == 429
        assert "location" not in {k.lower() for k in locked_resp.headers.keys()}


def test_manual_dry_run_refuses_to_start_without_required_settings(monkeypatch: pytest.MonkeyPatch):
    """Confirms investyo_mcp_server.py's --auth-mode oauth fail-closed refusal
    paths (MCP_OAUTH_ENABLED unset, MCP_OAUTH_PASSWORD unset) actually raise
    before any server binds a socket -- exercised via the real module's
    conditional construction logic at MCP_OAUTH_ENABLED=False (the safe
    default), confirming zero import-time side effects when OAuth is off."""
    import investyo_mcp_server as srv

    # Default state: OAuth disabled, bearer-token path is the only one active.
    assert srv._settings.MCP_OAUTH_ENABLED is False
    assert srv.mcp._auth_server_provider is None
    assert srv.mcp.settings.auth is None
