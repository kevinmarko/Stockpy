"""
tests/test_ws_risk_stream.py
============================
Integration tests for FastAPI WebSocket endpoint /ws/risk/portfolio in api/ws_api.py.
"""
import json
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
