"""
tests/test_state_api.py
========================
Tests for the standalone, read-only ``api/state_api.py`` FastAPI service
(WS10). Proves the file-backed engine/UI boundary (output/state_snapshot.json
+ transactions_store.TransactionsStore) is real and API-servable, without
touching any engine/calculation or broker/execution code.

All state-snapshot tests monkeypatch ``settings.OUTPUT_DIR`` to a tmp_path so
they never depend on (or pollute) a real ``output/`` directory. All trade
tests use an in-memory ``TransactionsStore`` per the codebase's established
``TransactionsStore(db_url="sqlite:///:memory:")`` idiom (see
tests/test_advisory.py, tests/test_kelly_no_history.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.state_api as state_api
from transactions_store import TransactionsStore

client = TestClient(state_api.app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /state
# ---------------------------------------------------------------------------


def test_state_returns_404_when_snapshot_missing(tmp_path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/state")
    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body
    assert body["detail"] == "No state snapshot yet — run the pipeline first."


def test_state_returns_parsed_json_when_present(tmp_path):
    fixture = {
        "signals": [
            {"symbol": "AAPL", "action": "BUY", "conviction": 0.8},
            {"symbol": "MSFT", "action": "HOLD", "conviction": 0.5},
        ],
        "macro_regime": "NEUTRAL",
        "generated_at": "2026-07-06T12:00:00Z",
    }
    snap_path = tmp_path / "state_snapshot.json"
    snap_path.write_text(json.dumps(fixture), encoding="utf-8")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/state")

    assert resp.status_code == 200
    assert resp.json() == fixture


def test_state_returns_404_on_corrupt_json(tmp_path):
    snap_path = tmp_path / "state_snapshot.json"
    snap_path.write_text("{not valid json", encoding="utf-8")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/state")

    # Corrupt file degrades exactly like a missing file (dead-letter, never
    # a raw 500 traceback).
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No state snapshot yet — run the pipeline first."


# ---------------------------------------------------------------------------
# /signals
# ---------------------------------------------------------------------------


def test_signals_returns_404_when_snapshot_missing(tmp_path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/signals")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No state snapshot yet — run the pipeline first."


def test_signals_extracts_signals_list(tmp_path):
    fixture = {
        "signals": [
            {"symbol": "AAPL", "action": "BUY"},
            {"symbol": "TSLA", "action": "SELL"},
        ],
        "other_field": "ignored",
    }
    snap_path = tmp_path / "state_snapshot.json"
    snap_path.write_text(json.dumps(fixture), encoding="utf-8")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/signals")

    assert resp.status_code == 200
    assert resp.json() == fixture["signals"]


def test_signals_returns_empty_list_when_field_absent(tmp_path):
    fixture = {"macro_regime": "RISK ON"}
    snap_path = tmp_path / "state_snapshot.json"
    snap_path.write_text(json.dumps(fixture), encoding="utf-8")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/signals")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# /trades
# ---------------------------------------------------------------------------


def test_trades_returns_empty_list_when_no_closed_trades(tmp_path):
    # A file-backed sqlite DB (not ``:memory:``) so the connection used by
    # the FastAPI request handler sees the same schema/rows as the fixture
    # setup above it — a bare ``:memory:`` URL hands out a fresh, empty
    # database per pooled connection checkout, which is exactly the failure
    # mode this test would otherwise mask.
    db_path = tmp_path / "empty_trades.db"
    store = TransactionsStore(db_url=f"sqlite:///{db_path}")
    with mock.patch.object(state_api, "TransactionsStore", return_value=store):
        resp = client.get("/trades")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_returns_closed_trades_as_records(tmp_path):
    db_path = tmp_path / "closed_trades.db"
    store = TransactionsStore(db_url=f"sqlite:///{db_path}")
    trade_id = store.record_trade(
        symbol="AAPL",
        side="long",
        entry_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entry_price=100.0,
        shares=10.0,
        strategy="test_strategy",
    )
    store.close_trade(
        trade_id=trade_id,
        exit_ts=datetime(2026, 1, 5, tzinfo=timezone.utc),
        exit_price=110.0,
    )

    with mock.patch.object(state_api, "TransactionsStore", return_value=store):
        resp = client.get("/trades")

    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["symbol"] == "AAPL"
    assert records[0]["exit_price"] == 110.0


def test_trades_returns_empty_list_on_db_error():
    class _BoomStore:
        def closed_trades_df(self):
            raise RuntimeError("db unavailable")

    with mock.patch.object(state_api, "TransactionsStore", return_value=_BoomStore()):
        resp = client.get("/trades")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Bearer-token auth (require_token dependency)
# ---------------------------------------------------------------------------
#
# Contract (implemented in api/state_api.py):
#   - settings.STATE_API_TOKEN falsy/None -> data endpoints OPEN (fail-open).
#     This is the DEFAULT, so every existing test above passes unchanged.
#   - settings.STATE_API_TOKEN set -> data endpoints require
#     ``Authorization: Bearer <token>``; correct -> 200, wrong/missing -> 401
#     with detail == "Invalid or missing bearer token".
#   - /health is ALWAYS open regardless of the token.
#
# ``require_token`` reads ``settings.STATE_API_TOKEN`` live on every request,
# so ``mock.patch.object(settings, "STATE_API_TOKEN", ...)`` takes effect
# without a module reload (unlike the CORS middleware, which captures its
# origins at app-construction time — see TestCORS below).

_INVALID_TOKEN_DETAIL = "Invalid or missing bearer token"


def _write_snapshot(tmp_path, fixture=None):
    """Write a minimal valid state_snapshot.json into tmp_path and return it.

    Used by the auth tests so a token-authorized request can reach a real 200
    (rather than a snapshot-missing 404 that would mask the auth outcome)."""
    if fixture is None:
        fixture = {"signals": [{"symbol": "AAPL", "action": "BUY"}], "macro_regime": "NEUTRAL"}
    snap_path = tmp_path / "state_snapshot.json"
    snap_path.write_text(json.dumps(fixture), encoding="utf-8")
    return fixture


class TestAuth:
    """Bearer-token guard on /state, /signals, /trades (never /health)."""

    def test_state_open_when_token_unset(self, tmp_path):
        # Token unset (default None) -> fail-open: a present snapshot yields 200
        # with no Authorization header at all.
        fixture = _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/state")
        assert resp.status_code == 200
        assert resp.json() == fixture

    def test_state_ok_with_correct_bearer_token(self, tmp_path):
        fixture = _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get(
                    "/state", headers={"Authorization": "Bearer secret-tok"}
                )
        assert resp.status_code == 200
        assert resp.json() == fixture

    def test_state_401_with_wrong_bearer_token(self, tmp_path):
        _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get(
                    "/state", headers={"Authorization": "Bearer WRONG-tok"}
                )
        assert resp.status_code == 401
        assert resp.json()["detail"] == _INVALID_TOKEN_DETAIL

    def test_state_401_with_no_authorization_header(self, tmp_path):
        _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/state")
        assert resp.status_code == 401
        assert resp.json()["detail"] == _INVALID_TOKEN_DETAIL

    def test_health_open_even_when_token_set(self):
        # /health is never guarded — no Authorization header even with a token
        # configured still yields 200.
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_signals_401_when_token_set_and_missing_header(self, tmp_path):
        _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/signals")
        assert resp.status_code == 401
        assert resp.json()["detail"] == _INVALID_TOKEN_DETAIL

    def test_signals_ok_when_token_set_and_correct_header(self, tmp_path):
        fixture = _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get(
                    "/signals", headers={"Authorization": "Bearer secret-tok"}
                )
        assert resp.status_code == 200
        assert resp.json() == fixture["signals"]

    def test_signals_open_when_token_unset(self, tmp_path):
        fixture = _write_snapshot(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/signals")
        assert resp.status_code == 200
        assert resp.json() == fixture["signals"]

    def test_trades_401_when_token_set_and_missing_header(self, tmp_path):
        # The guard runs before the handler body, so the DB is never touched on
        # a 401 — but patch TransactionsStore anyway (mirrors the existing
        # /trades tests) so a stray call could never hit a real DB.
        db_path = tmp_path / "auth_trades.db"
        store = TransactionsStore(db_url=f"sqlite:///{db_path}")
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(state_api, "TransactionsStore", return_value=store):
                resp = client.get("/trades")
        assert resp.status_code == 401
        assert resp.json()["detail"] == _INVALID_TOKEN_DETAIL

    def test_trades_ok_when_token_set_and_correct_header(self, tmp_path):
        db_path = tmp_path / "auth_trades_ok.db"
        store = TransactionsStore(db_url=f"sqlite:///{db_path}")
        with mock.patch.object(settings, "STATE_API_TOKEN", "secret-tok"):
            with mock.patch.object(state_api, "TransactionsStore", return_value=store):
                resp = client.get(
                    "/trades", headers={"Authorization": "Bearer secret-tok"}
                )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trades_open_when_token_unset(self, tmp_path):
        db_path = tmp_path / "auth_trades_open.db"
        store = TransactionsStore(db_url=f"sqlite:///{db_path}")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(state_api, "TransactionsStore", return_value=store):
                resp = client.get("/trades")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# CORS policy
# ---------------------------------------------------------------------------
#
# NOTE: CORSMiddleware captures ``settings.CORS_ALLOWED_ORIGINS`` at
# app-construction time (module import), so a per-test monkeypatch of settings
# would NOT retroactively change the middleware's allow-list. These tests
# therefore assert against the REAL default origin ("http://localhost:3000")
# without patching — which is exactly the production default and the value the
# middleware actually captured.


class TestCORS:
    """CORS origin reflection for the read-only API (GET only)."""

    def test_allowed_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_disallowed_origin_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 200
        # The disallowed origin must never be echoed back.
        assert resp.headers.get("access-control-allow-origin") != "http://evil.example"


# ---------------------------------------------------------------------------
# Architectural guard: no engine/broker imports in api/state_api.py
# ---------------------------------------------------------------------------


def test_state_api_never_imports_engine_or_broker_code():
    """Static guard: api/state_api.py must only import files/stdlib, fastapi,
    settings, and transactions_store — never engine/calculation or
    broker/execution modules.

    This is the load-bearing invariant of WS10 — the whole point is that the
    API proves the file-backed boundary is sufficient on its own, without any
    in-process engine calls. Only scans actual `import`/`from ... import`
    statements (via ast) so mentions in docstrings/comments don't false-positive."""
    import ast
    import pathlib

    src = pathlib.Path(state_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    forbidden_modules = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "data_engine",
        "execution",
    }
    overlap = imported_modules & forbidden_modules
    assert not overlap, f"api/state_api.py must not import {overlap}"
