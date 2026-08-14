from contextlib import contextmanager, ExitStack

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from pilots.paper_broker import get_account, get_positions, get_orders
from settings import settings
import api.pilots_api as pilots_api

@patch("pilots.paper_broker.PaperAccountStore")
def test_get_account(mock_store):
    mock_instance = mock_store.return_value
    snapshot = MagicMock(equity=1000.0, cash=500.0, buying_power=500.0)
    mock_instance.get_account.return_value = snapshot

    result = get_account()
    
    mock_store.assert_called_with(readonly=True)
    assert result == {"equity": 1000.0, "cash": 500.0, "buying_power": 500.0}

@patch("pilots.paper_broker.PaperAccountStore")
def test_get_positions(mock_store):
    mock_instance = mock_store.return_value
    pos = MagicMock(symbol="AAPL", qty=10, avg_entry_price=100.0, market_value=1500.0, unrealized_pl=500.0)
    mock_instance.get_open_positions.return_value = [pos]

    result = get_positions()
    
    mock_store.assert_called_with(readonly=True)
    assert result == [{"symbol": "AAPL", "qty": 10, "avg_cost": 100.0, "current_price": 150.0, "market_value": 1500.0, "unrealized_pl": 500.0, "unrealized_pl_pct": 0.5}]

@patch("pilots.paper_broker.PaperAccountStore")
def test_get_orders(mock_store):
    mock_instance = mock_store.return_value
    mock_instance.get_full_orders.return_value = [{"order_id": "123"}]

    result = get_orders(status="FILLED", limit=10)

    mock_store.assert_called_with(readonly=True)
    mock_instance.get_full_orders.assert_called_with(status="FILLED", limit=10)
    assert result == [{"order_id": "123"}]


# ---------------------------------------------------------------------------
# POST /pilots/paper-broker/reset -- fail-closed, cash-override behavior
# ---------------------------------------------------------------------------

_client = TestClient(pilots_api.app, client=("127.0.0.1", 54124))
_CMD_TOKEN = "paper-broker-cmd-tok"


@contextmanager
def mock_patch_settings(**kwargs):
    with ExitStack() as stack:
        for key, value in kwargs.items():
            stack.enter_context(patch.object(settings, key, value))
        yield


class TestPostPaperBrokerReset:
    def test_fails_closed_when_writes_disabled(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/paper-broker/reset",
                json={"cash": 50000.0},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/reset",
                json={"cash": 50000.0},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_cash_override_passed_through_to_store(self):
        mock_store = MagicMock()
        mock_store.get_account.return_value = MagicMock(equity=50000.0, cash=50000.0, buying_power=50000.0)
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            with patch("data.paper_account_store.PaperAccountStore", return_value=mock_store):
                resp = _client.post(
                    "/pilots/paper-broker/reset",
                    json={"cash": 50000.0},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["cash"] == 50000.0
        mock_store.reset_account.assert_called_once_with(starting_cash=50000.0)

    def test_omitted_cash_preserves_default_behavior(self):
        mock_store = MagicMock()
        mock_store.get_account.return_value = MagicMock(equity=100000.0, cash=100000.0, buying_power=100000.0)
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            with patch("data.paper_account_store.PaperAccountStore", return_value=mock_store):
                resp = _client.post(
                    "/pilots/paper-broker/reset",
                    json={},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json()["cash"] == 100000.0
        mock_store.reset_account.assert_called_once_with(starting_cash=None)

    def test_no_body_at_all_preserves_default_behavior(self):
        mock_store = MagicMock()
        mock_store.get_account.return_value = MagicMock(equity=100000.0, cash=100000.0, buying_power=100000.0)
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            with patch("data.paper_account_store.PaperAccountStore", return_value=mock_store):
                resp = _client.post(
                    "/pilots/paper-broker/reset",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        mock_store.reset_account.assert_called_once_with(starting_cash=None)


# ---------------------------------------------------------------------------
# POST /brokerage/options/order & execute_paper_order
# ---------------------------------------------------------------------------


class TestExecutePaperOrder:
    def test_live_mode_returns_advisory_rejection(self):
        from pilots.paper_broker import execute_paper_order
        res = execute_paper_order("AAPL", is_live=True)
        assert res["ok"] is False
        assert "Advisory-Only" in res["message"]

    @patch("pilots.paper_broker_options_order.PaperAccountStore")
    def test_stock_order_by_dollar_amount(self, mock_store_cls):
        from pilots.paper_broker import execute_paper_order
        mock_store = mock_store_cls.return_value
        mock_store.apply_fill.return_value = True

        res = execute_paper_order(
            "AGNC",
            asset_type="stock",
            side="buy",
            dollar_amount=500.0,
            limit_price=10.0,
        )
        assert res["ok"] is True
        assert "50.00 shares" in res["message"]
        mock_store.apply_fill.assert_called_once()
        args, kwargs = mock_store.apply_fill.call_args
        assert kwargs["symbol"] == "AGNC"
        assert kwargs["qty"] == 50.0
        assert kwargs["fill_price"] == 10.0

    @patch("pilots.paper_broker_options_order.PaperAccountStore")
    def test_option_order_single_leg(self, mock_store_cls):
        from pilots.paper_broker import execute_paper_order
        mock_store = mock_store_cls.return_value
        mock_store.apply_fill.return_value = True

        legs = [{
            "contract": {"strike": 10.5, "ask": 0.15, "bid": 0.10, "lastPrice": 0.12},
            "type": "put",
            "action": "Buy"
        }]

        res = execute_paper_order(
            "AGNC",
            asset_type="option",
            expiration="2026-08-14",
            legs=legs,
            quantity=2,
        )
        assert res["ok"] is True
        assert "2 contract(s)" in res["message"]
        mock_store.apply_fill.assert_called_once()

    @patch("pilots.paper_broker_options_order.PaperAccountStore")
    def test_post_options_order_endpoint(self, mock_store_cls):
        mock_store = mock_store_cls.return_value
        mock_store.apply_fill.return_value = True

        resp = _client.post(
            "/brokerage/options/order",
            json={
                "symbol": "AGNC",
                "asset_type": "stock",
                "side": "buy",
                "dollar_amount": 500.0,
                "limit_price": 10.0,
                "isLive": False,
            }
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "AGNC" in body["message"]

