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
    monkeypatch.setattr(
        "pilots.price_provider.get_latest_prices",
        lambda symbols: {sym: 180.0 for sym in symbols},
    )

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
    """Regression test for the Phase 31 audit's Critical #2 finding, updated
    for the batched-quote-fetch follow-up fix.

    The original handler called ``provider.get_latest_price(sym)`` -- a
    method that does not exist on ``CompositeProvider`` -- wrapped in a bare
    ``except Exception: pass``, so the AttributeError was silently swallowed
    every single tick and no quote was ever fetched. The handler now calls
    ``pilots.price_provider.get_latest_prices`` ONCE per tick (offloaded via
    ``run_in_executor``) instead of ``get_latest_price`` once per symbol.
    This test asserts (1) the real, existing ``get_latest_prices`` function
    is actually invoked with every underlying symbol this tick needs (proving
    the call site resolves to a real callable), and (2) a total batch-call
    failure surfaces as a logged WARNING instead of disappearing into a bare
    ``except: pass``.
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

    called_batches: list[list[str]] = []

    def fake_get_latest_prices(symbols):
        called_batches.append(list(symbols))
        raise RuntimeError("simulated batch quote fetch failure")

    # Patched at the source module -- api/ws_api.py's handler does
    # `from pilots.price_provider import get_latest_prices` fresh on every
    # new WebSocket connection, so patching the attribute here is picked up.
    monkeypatch.setattr("pilots.price_provider.get_latest_prices", fake_get_latest_prices)

    client = TestClient(app, client=("127.0.0.1", 50000))
    with caplog.at_level(logging.WARNING, logger="api.ws_api"):
        with client.websocket_connect("/ws/risk/portfolio") as ws:
            ws.receive_text()

    # The real provider function was actually called once, with every
    # underlying this cycle needed a quote for (AAPL, plus the
    # always-included SPY) -- this is only possible because the call site
    # resolves to a real, existing callable.
    assert len(called_batches) == 1
    assert set(called_batches[0]) == {"AAPL", "SPY"}

    # The simulated total-batch failure was logged at WARNING with the
    # exception detail, not silently discarded by a bare `except: pass`, and
    # the stream still produced a payload (the connection didn't die).
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("simulated batch quote fetch failure" in msg for msg in warning_messages)


def test_ws_portfolio_risk_quote_fetch_is_batched_not_per_symbol(monkeypatch):
    """The quote fetch for a multi-symbol portfolio must be ONE offloaded
    batch call per tick, not one call per underlying symbol -- the residual
    quote-fetch executor-offload fix (plan section 3). Regardless of how many
    distinct underlyings are held, ``get_latest_prices`` is invoked exactly
    once per tick, with every underlying passed in a single list.
    """
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "")

    mock_positions = [
        PositionSnapshot(
            symbol=sym,
            qty=10.0,
            avg_entry_price=100.0,
            market_value=1000.0,
            unrealized_pl=0.0,
        )
        for sym in ("AAPL", "MSFT", "GOOGL", "TSLA")
    ]
    monkeypatch.setattr(
        "data.paper_account_store.PaperAccountStore.get_open_positions",
        lambda self: mock_positions,
    )

    call_count = 0
    received_symbols: list[str] = []

    def fake_get_latest_prices(symbols):
        nonlocal call_count
        call_count += 1
        received_symbols.extend(symbols)
        return {sym: 100.0 for sym in symbols}

    monkeypatch.setattr("pilots.price_provider.get_latest_prices", fake_get_latest_prices)

    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect("/ws/risk/portfolio") as ws:
        data = ws.receive_text()
        payload = json.loads(data)

    # Exactly one batch call for this tick, regardless of the 4 distinct
    # underlyings (+ SPY) held -- not 5 individual get_latest_price calls.
    assert call_count == 1
    assert set(received_symbols) == {"AAPL", "MSFT", "GOOGL", "TSLA", "SPY"}
    assert payload["total_positions_count"] == 4
