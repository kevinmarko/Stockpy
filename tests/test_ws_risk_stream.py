"""
tests/test_ws_risk_stream.py
============================
Integration tests for FastAPI WebSocket endpoint /ws/risk/portfolio in api/ws_api.py.
"""
import json
import logging

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.data_api import app
from data.paper_account_store import PositionSnapshot
from settings import settings


@pytest.fixture
def ws_client():
    return TestClient(app, client=("127.0.0.1", 50000))


def test_ws_portfolio_risk_auth_rejection(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "super-secret-token")
    client = TestClient(app, client=("127.0.0.1", 50000))
    # Attempt connecting without token
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/risk/portfolio?token=wrong_token") as ws:
            pass
    assert exc_info.value.code == 4003


def test_ws_portfolio_risk_stream_pushes_payload(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "")
    client = TestClient(app, client=("127.0.0.1", 50000))

    with client.websocket_connect("/ws/risk/portfolio") as ws:
        data = ws.receive_text()
        payload = json.loads(data)

        assert "timestamp" in payload
        assert "net_delta" in payload
        assert "net_dollar_delta" in payload
        assert "net_gamma" in payload
        assert "net_dollar_gamma_1pct" in payload
        assert "net_theta" in payload
        assert "net_vega" in payload
        assert "beta_weighted_delta_spy" in payload
        assert "positions" in payload
        assert "missing_positions" in payload
        assert isinstance(payload["positions"], list)


def test_ws_portfolio_risk_with_active_positions(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "")

    mock_positions = [
        PositionSnapshot(
            symbol="AAPL",
            qty=100.0,
            avg_entry_price=150.0,
            market_value=18000.0,
            unrealized_pl=3000.0,
        )
    ]

    monkeypatch.setattr(
        "data.paper_account_store.PaperAccountStore.get_open_positions",
        lambda self: mock_positions
    )

    # Pin the live quote fetch to a deterministic value matching the mocked
    # position's own market_value/qty (180.0). Now that Critical #2 is fixed,
    # compute_portfolio_risk_stream's quotes.get(underlying) genuinely takes
    # precedence over the position's own spot_price (see
    # pilots/realtime_risk_streamer.py's spot-resolution order) -- leaving
    # this unmocked would make the assertion below depend on a real,
    # non-deterministic live AAPL price fetched over the network.
    monkeypatch.setattr("pilots.price_provider.get_latest_price", lambda sym: 180.0)

    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect("/ws/risk/portfolio") as ws:
        data = ws.receive_text()
        payload = json.loads(data)

        assert payload["total_positions_count"] == 1
        assert payload["resolved_positions_count"] == 1
        assert len(payload["positions"]) == 1
        pos = payload["positions"][0]
        assert pos["symbol"] == "AAPL"
        assert pos["qty"] == 100.0
        assert pos["dollar_delta"] == 18000.0


def test_ws_portfolio_risk_quote_fetch_uses_price_provider_and_logs_failure(monkeypatch, caplog):
    """Regression test for the Phase 31 audit's Critical #2 finding.

    The original handler called ``provider.get_latest_price(sym)`` -- a
    method that does not exist on ``CompositeProvider`` -- wrapped in a bare
    ``except Exception: pass``, so the AttributeError was silently swallowed
    every single tick and no quote was ever fetched. This test would have
    caught that bug two ways at once: (1) it asserts the real, existing
    ``pilots.price_provider.get_latest_price`` function is actually invoked
    for every underlying symbol (proving the call site now resolves to a
    real callable rather than raising AttributeError on the first line
    reached), and (2) it asserts a simulated per-symbol failure surfaces as a
    logged WARNING instead of disappearing into a bare ``except: pass``.
    """
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "")

    mock_positions = [
        PositionSnapshot(
            symbol="AAPL",
            qty=10.0,
            avg_entry_price=150.0,
            market_value=1800.0,
            unrealized_pl=300.0,
        )
    ]
    monkeypatch.setattr(
        "data.paper_account_store.PaperAccountStore.get_open_positions",
        lambda self: mock_positions,
    )

    called_symbols: list[str] = []

    def fake_get_latest_price(sym):
        called_symbols.append(sym)
        if sym == "AAPL":
            raise RuntimeError("simulated price provider failure")
        return 500.0

    # Patched at the source module -- api/ws_api.py's handler does
    # `from pilots.price_provider import get_latest_price` fresh on every
    # new WebSocket connection, so patching the attribute here is picked up.
    monkeypatch.setattr("pilots.price_provider.get_latest_price", fake_get_latest_price)

    client = TestClient(app, client=("127.0.0.1", 50000))
    with caplog.at_level(logging.WARNING, logger="api.ws_api"):
        with client.websocket_connect("/ws/risk/portfolio") as ws:
            ws.receive_text()

    # The real provider function was actually called for every underlying
    # this cycle needed a quote for (AAPL, plus the always-included SPY) --
    # this is only possible because the call site resolves to a real,
    # existing callable rather than raising AttributeError immediately.
    assert "AAPL" in called_symbols
    assert "SPY" in called_symbols

    # The simulated AAPL failure was logged at WARNING with the exception
    # detail, not silently discarded by a bare `except Exception: pass`.
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("AAPL" in msg and "simulated price provider failure" in msg for msg in warning_messages)
