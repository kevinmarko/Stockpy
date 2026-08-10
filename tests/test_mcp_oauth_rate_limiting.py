"""Tests for mcp_oauth_rate_limit.py -- the per-IP sliding-window request
limiter wrapped around the InvestYo MCP OAuth server's unauthenticated
/register, /login, /token surface.

Follows tests/test_mcp_oauth_login_route.py's exact TestClient-against-
mcp.streamable_http_app() + tmp_path-backed SQLite fixture idiom. Different
source IPs are simulated via TestClient(..., client=(ip, port)) -- a loopback
tuple simulates "arrived via cloudflared" (the trust gate's honored path); a
non-loopback tuple simulates a direct, un-tunneled connection whose
CF-Connecting-IP header must be ignored.
"""

import uuid

import pytest
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from mcp_oauth_provider import InvestyoOAuthProvider, register_login_routes
from mcp_oauth_rate_limit import RateLimitRule, rate_limit_asgi_middleware
from mcp_oauth_store import McpOAuthStore

# Small, fast rules so tests never depend on real wall-clock windows.
_SMALL_RULES = {
    "register": RateLimitRule(limit=3, window_seconds=60),
    "login": RateLimitRule(limit=3, window_seconds=60),
    "token": RateLimitRule(limit=3, window_seconds=60),
}


def _set_oauth_password(monkeypatch: pytest.MonkeyPatch, value="s3cret-pw"):
    from settings import settings

    monkeypatch.setitem(settings.__dict__, "MCP_OAUTH_PASSWORD", value)


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path, *, client=("127.0.0.1", 12345), rules=None):
    _set_oauth_password(monkeypatch)
    db_path = tmp_path / f"mcp_oauth_ratelimit_{uuid.uuid4().hex}.db"
    store = McpOAuthStore(db_url=f"sqlite:///{db_path}")
    provider = InvestyoOAuthProvider(store=store)

    issuer_url = "http://127.0.0.1:8080"
    mcp = FastMCP(
        "test-oauth-ratelimit",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )
    register_login_routes(mcp, provider)
    app = rate_limit_asgi_middleware(mcp.streamable_http_app(), rules=rules if rules is not None else _SMALL_RULES)
    return TestClient(app, follow_redirects=False, client=client), store


def _register_body(name="test-client"):
    return {
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": name,
    }


def test_under_limit_requests_succeed_normally(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, _store = _make_client(monkeypatch, tmp_path)

    # limit=3 -- send 2, both should reach the real /register handler.
    for _ in range(2):
        resp = client.post("/register", json=_register_body())
        assert resp.status_code == 201, resp.text


def test_register_over_limit_returns_429_with_retry_after(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, _store = _make_client(monkeypatch, tmp_path)

    for _ in range(3):
        resp = client.post("/register", json=_register_body())
        assert resp.status_code == 201, resp.text

    over_resp = client.post("/register", json=_register_body())
    assert over_resp.status_code == 429
    assert "retry-after" in {k.lower() for k in over_resp.headers.keys()}
    assert int(over_resp.headers["retry-after"]) > 0


def test_two_different_cf_connecting_ip_values_get_independent_budgets(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Both requests arrive via the same simulated loopback peer (as if both
    # passed through cloudflared), but present different CF-Connecting-IP
    # values -- each real end-user IP must get its own budget.
    client, _store = _make_client(monkeypatch, tmp_path, client=("127.0.0.1", 12345))

    for _ in range(3):
        resp = client.post(
            "/register", json=_register_body(), headers={"CF-Connecting-IP": "1.2.3.4"}
        )
        assert resp.status_code == 201, resp.text

    # IP 1.2.3.4 is now exhausted.
    exhausted_resp = client.post(
        "/register", json=_register_body(), headers={"CF-Connecting-IP": "1.2.3.4"}
    )
    assert exhausted_resp.status_code == 429

    # A different IP is unaffected.
    other_resp = client.post(
        "/register", json=_register_body(), headers={"CF-Connecting-IP": "5.6.7.8"}
    )
    assert other_resp.status_code == 201, other_resp.text


def test_untrusted_direct_peer_forged_header_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Two different NON-loopback direct peers presenting the SAME forged
    CF-Connecting-IP get independent budgets keyed by their own real peer
    addresses -- proving the forged header was ignored rather than trusted."""
    client_a, _store_a = _make_client(monkeypatch, tmp_path, client=("203.0.113.5", 12345))
    client_b, _store_b = _make_client(monkeypatch, tmp_path, client=("203.0.113.6", 12345))

    forged_headers = {"CF-Connecting-IP": "9.9.9.9"}

    for _ in range(3):
        resp = client_a.post("/register", json=_register_body(), headers=forged_headers)
        assert resp.status_code == 201, resp.text

    # Peer A (203.0.113.5) is now exhausted under the forged shared header.
    exhausted_resp = client_a.post("/register", json=_register_body(), headers=forged_headers)
    assert exhausted_resp.status_code == 429

    # Peer B (203.0.113.6), presenting the SAME forged header, is unaffected
    # -- proof the real scope["client"] peer address was used, not the header.
    other_resp = client_b.post("/register", json=_register_body(), headers=forged_headers)
    assert other_resp.status_code == 201, other_resp.text


def test_mcp_and_well_known_stay_reachable_after_register_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, _store = _make_client(monkeypatch, tmp_path)

    for _ in range(3):
        resp = client.post("/register", json=_register_body())
        assert resp.status_code == 201, resp.text
    exhausted_resp = client.post("/register", json=_register_body())
    assert exhausted_resp.status_code == 429

    # /.well-known/oauth-authorization-server isn't in RATE_LIMIT_RULES at all.
    well_known_resp = client.get("/.well-known/oauth-authorization-server")
    assert well_known_resp.status_code == 200

    # /mcp isn't in RATE_LIMIT_RULES either -- it's gated by the SDK's own
    # RequireAuthMiddleware (401 without a bearer token), never a 429.
    mcp_resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert mcp_resp.status_code == 401


def test_login_and_register_are_independent_buckets(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, store = _make_client(monkeypatch, tmp_path)

    for _ in range(3):
        resp = client.post("/register", json=_register_body())
        assert resp.status_code == 201, resp.text
    exhausted_register_resp = client.post("/register", json=_register_body())
    assert exhausted_register_resp.status_code == 429

    # /login (a distinct bucket) is unaffected by /register's exhaustion --
    # an unknown nonce still resolves to the real handler's 404, not a 429.
    login_resp = client.get("/login?req=does-not-exist")
    assert login_resp.status_code == 404
    assert "expired" in login_resp.text.lower()
