from contextlib import contextmanager, ExitStack

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from pilots.paper_broker import get_account, get_positions, get_orders, get_closed_trades, get_portfolio_greeks
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
    pos = MagicMock(
        symbol="AAPL", qty=10, avg_entry_price=100.0, market_value=1500.0, unrealized_pl=500.0,
        strategy_id="strategy_A", pilot_id="pilot-1", experiment_arm="control",
    )
    mock_instance.get_open_positions.return_value = [pos]

    result = get_positions()

    mock_store.assert_called_with(readonly=True)
    # Regression: strategy_id/pilot_id/experiment_arm were previously dropped
    # even though PositionSnapshot already carries them (see docs bullet on
    # this fix in CLAUDE.md).
    assert result == [{
        "symbol": "AAPL", "qty": 10, "avg_cost": 100.0, "current_price": 150.0,
        "market_value": 1500.0, "unrealized_pl": 500.0, "unrealized_pl_pct": 0.5,
        "strategy_id": "strategy_A", "pilot_id": "pilot-1", "experiment_arm": "control",
    }]

@patch("pilots.paper_broker.PaperAccountStore")
def test_get_orders(mock_store):
    mock_instance = mock_store.return_value
    mock_instance.get_full_orders.return_value = [{"order_id": "123"}]

    result = get_orders(status="FILLED", limit=10)

    mock_store.assert_called_with(readonly=True)
    mock_instance.get_full_orders.assert_called_with(status="FILLED", limit=10)
    assert result == [{"order_id": "123"}]

@patch("pilots.paper_broker.PaperAccountStore")
def test_get_closed_trades(mock_store):
    mock_instance = mock_store.return_value
    mock_instance.get_full_closed_trades.return_value = [{"trade_id": 1, "symbol": "AAPL"}]

    result = get_closed_trades(symbol="AAPL", limit=10)

    mock_store.assert_called_with(readonly=True)
    mock_instance.get_full_closed_trades.assert_called_with(symbol="AAPL", limit=10)
    assert result == [{"trade_id": 1, "symbol": "AAPL"}]


# ---------------------------------------------------------------------------
# get_portfolio_greeks() -- must thread a real, pre-resolved SPY quote into
# calculate_portfolio_greeks rather than omitting spy_spot (regression for
# the fabricated-$500-SPY-spot bug; see docs/known_issues/
# options_risk_fabricated_spy_spot.md).
# ---------------------------------------------------------------------------

@patch("pilots.paper_broker.PaperAccountStore")
@patch("pilots.options_risk.calculate_portfolio_greeks")
@patch("pilots.price_provider.get_current_price")
def test_get_portfolio_greeks_threads_resolved_spy_spot(mock_get_price, mock_calc_greeks, mock_store):
    mock_get_price.return_value = 642.17
    mock_calc_greeks.return_value = {"beta_weighted_delta_spy": 0.0}

    get_portfolio_greeks()

    mock_get_price.assert_called_once_with("SPY")
    _, kwargs = mock_calc_greeks.call_args
    assert kwargs.get("spy_spot") == 642.17


@patch("pilots.paper_broker.PaperAccountStore")
@patch("pilots.options_risk.calculate_portfolio_greeks")
@patch("pilots.price_provider.get_current_price")
def test_get_portfolio_greeks_passes_none_not_fabricated_price_when_spy_unresolvable(
    mock_get_price, mock_calc_greeks, mock_store
):
    mock_get_price.return_value = 0.0  # get_current_price's own honest "unavailable" sentinel
    mock_calc_greeks.return_value = {"beta_weighted_delta_spy": 0.0}

    get_portfolio_greeks()

    _, kwargs = mock_calc_greeks.call_args
    assert kwargs.get("spy_spot") is None


# ---------------------------------------------------------------------------
# POST /pilots/paper-broker/reset -- fail-closed, cash-override behavior
# ---------------------------------------------------------------------------

_client = TestClient(pilots_api.app, client=("127.0.0.1", 54124))
_CMD_TOKEN = "paper-broker-cmd-tok"
_READ_TOKEN = "paper-broker-read-tok"



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


class TestGetPaperBrokerClosedTradesEndpoint:
    @patch("pilots.paper_broker.get_closed_trades")
    def test_returns_200_and_passes_through(self, mock_get_closed_trades):
        mock_get_closed_trades.return_value = [
            {"trade_id": 1, "symbol": "AAPL", "realized_pnl": 12.5, "strategy_id": "untagged"}
        ]
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/paper-broker/closed-trades?symbol=AAPL&limit=5",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body == [{"trade_id": 1, "symbol": "AAPL", "realized_pnl": 12.5, "strategy_id": "untagged"}]
        mock_get_closed_trades.assert_called_with(symbol="AAPL", limit=5)

    @patch("pilots.paper_broker.get_closed_trades")
    def test_fails_closed_with_wrong_token(self, mock_get_closed_trades):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/paper-broker/closed-trades",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401
        mock_get_closed_trades.assert_not_called()


class TestPilotsExecutionSec606ReportRetired:
    def test_sec_606_report_route_is_gone(self):
        """OrderManager no longer writes execution-audit rows, so the report
        would only ever read a frozen table; the route was removed."""
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/execution/sec-606/report")
        assert resp.status_code == 404


