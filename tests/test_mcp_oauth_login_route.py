"""Tests for mcp_oauth_provider.register_login_routes -- the human-facing
GET/POST /login form that gates the InvestYo MCP OAuth 2.1 authorization
flow.

Verified working pattern: a bare ``FastMCP()`` (no auth kwargs) still wires
in custom routes via ``FastMCP.streamable_http_app()`` (it unconditionally
extends its route list with every ``@mcp.custom_route``-registered route),
so a ``starlette.testclient.TestClient`` wrapping that app is a real
end-to-end test of the routes' Starlette wiring, not just the handler
functions in isolation.
"""

import time
import urllib.parse
import uuid

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

import mcp_oauth_store
from mcp_oauth_provider import InvestyoOAuthProvider, register_login_routes
from mcp_oauth_store import McpOAuthStore


def _set_oauth_password(monkeypatch: pytest.MonkeyPatch, value):
    """See tests/test_mcp_oauth_provider.py's identical helper docstring --
    duplicated here (not shared conftest) to avoid any file-contention with
    the concurrent settings.py-editing agent."""
    from settings import settings

    monkeypatch.setitem(settings.__dict__, "MCP_OAUTH_PASSWORD", value)


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path, password="s3cret-pw"):
    _set_oauth_password(monkeypatch, password)
    # NOTE: a real ASGI TestClient dispatches the request through a worker
    # thread distinct from the one that constructed the store (verified
    # empirically) -- `sqlite:///:memory:` uses SQLAlchemy's thread-local
    # SingletonThreadPool for :memory: URLs, so a second thread would see a
    # brand-new, table-less in-memory database. A tmp_path-backed SQLite
    # FILE avoids this: db_config.create_db_engine uses NullPool for file
    # DBs, and every connection (any thread) reopens the same file on disk.
    db_path = tmp_path / f"mcp_oauth_{uuid.uuid4().hex}.db"
    store = McpOAuthStore(db_url=f"sqlite:///{db_path}")
    provider = InvestyoOAuthProvider(store=store)
    mcp = FastMCP("test-oauth")
    register_login_routes(mcp, provider)
    app = mcp.streamable_http_app()
    return TestClient(app, follow_redirects=False), store


def _save_pending(store: McpOAuthStore, nonce="nonce-1", **overrides) -> dict:
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


def test_get_login_valid_nonce_renders_form(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, store = _make_client(monkeypatch, tmp_path)
    _save_pending(store, "nonce-1")

    resp = client.get("/login?req=nonce-1")

    assert resp.status_code == 200
    assert "<form" in resp.text
    assert 'name="password"' in resp.text
    assert "nonce-1" in resp.text


def test_get_login_unknown_nonce_shows_expired(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, _store = _make_client(monkeypatch, tmp_path)

    resp = client.get("/login?req=does-not-exist")

    assert resp.status_code == 404
    assert "expired" in resp.text.lower()
    assert "<form" not in resp.text


def test_get_login_expired_nonce_shows_expired(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, store = _make_client(monkeypatch, tmp_path)
    _save_pending(store, "nonce-expired", expires_at=time.time() - 10)

    resp = client.get("/login?req=nonce-expired")

    assert resp.status_code == 404
    assert "expired" in resp.text.lower()


def test_post_login_wrong_password_shows_generic_error_and_nonce_still_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_client(monkeypatch, tmp_path, password="correct-pw")
    _save_pending(store, "nonce-1")

    resp = client.post("/login?req=nonce-1", data={"req": "nonce-1", "password": "wrong-pw"})

    assert resp.status_code == 200
    assert "incorrect" in resp.text.lower()
    # generic error -- doesn't leak which part was wrong
    assert "wrong-pw" not in resp.text

    # nonce is still live / POST-able (not consumed by a failed attempt)
    assert store.load_pending_authorization("nonce-1") is not None


def test_post_login_correct_password_after_lockout_shows_locked_not_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_client(monkeypatch, tmp_path, password="correct-pw")
    _save_pending(store, "nonce-1")

    for _ in range(mcp_oauth_store.LOGIN_LOCKOUT_THRESHOLD):
        store.record_login_failure()
    assert store.is_locked_out() is True

    resp = client.post("/login?req=nonce-1", data={"req": "nonce-1", "password": "correct-pw"})

    assert resp.status_code == 429
    assert "locked" in resp.text.lower()
    assert "location" not in {k.lower() for k in resp.headers.keys()}


def test_post_login_correct_password_no_lockout_redirects_with_code_and_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    client, store = _make_client(monkeypatch, tmp_path, password="correct-pw")
    _save_pending(
        store,
        "nonce-1",
        redirect_uri="https://example.com/callback",
        state="state-abc",
    )

    resp = client.post("/login?req=nonce-1", data={"req": "nonce-1", "password": "correct-pw"})

    assert resp.status_code == 302
    location = resp.headers["location"]
    parsed = urllib.parse.urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "example.com"
    assert parsed.path == "/callback"

    query = urllib.parse.parse_qs(parsed.query)
    assert "code" in query
    code = query["code"][0]
    assert code

    assert query["state"] == ["state-abc"]

    # the pending authorization is consumed
    assert store.load_pending_authorization("nonce-1") is None

    # the minted code is real and loadable
    loaded_code = store.load_authorization_code(code)
    assert loaded_code is not None
    assert loaded_code["client_id"] == "client-1"

    # a successful login resets any prior failure state
    assert store.is_locked_out() is False


def test_post_login_missing_password_setting_raises(monkeypatch: pytest.MonkeyPatch, tmp_path):
    client, store = _make_client(monkeypatch, tmp_path, password=None)
    _save_pending(store, "nonce-1")

    with pytest.raises(Exception):
        client.post("/login?req=nonce-1", data={"req": "nonce-1", "password": "anything"})
