from contextlib import contextmanager, ExitStack

import numpy as np
import pandas as pd
import pytest
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

    @patch("pilots.paper_broker_options_order.PaperAccountStore")
    def test_post_options_order_endpoint(self, mock_store_cls):
        mock_store = mock_store_cls.return_value
        mock_store.apply_fill.return_value = True

        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/brokerage/options/order",
                json={
                    "symbol": "AGNC",
                    "asset_type": "stock",
                    "side": "buy",
                    "dollar_amount": 500.0,
                    "limit_price": 10.0,
                    "isLive": False,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "AGNC" in body["message"]

    @patch("pilots.paper_broker_options_order.PaperAccountStore")
    def test_post_options_order_endpoint_fails_closed_when_writes_disabled(self, mock_store_cls):
        """Same auth/flag gate as POST /pilots/paper-broker/reset -- an order-
        execution endpoint that mutates the paper account must not be
        reachable on the fail-open read tier alone."""
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/brokerage/options/order",
                json={"symbol": "AGNC", "asset_type": "stock", "dollar_amount": 500.0, "limit_price": 10.0},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403
        mock_store_cls.return_value.apply_fill.assert_not_called()

    @patch("pilots.paper_broker_options_order.PaperAccountStore")
    def test_post_options_order_endpoint_fails_closed_with_wrong_token(self, mock_store_cls):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/brokerage/options/order",
                json={"symbol": "AGNC", "asset_type": "stock", "dollar_amount": 500.0, "limit_price": 10.0},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401
        mock_store_cls.return_value.apply_fill.assert_not_called()


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


class TestStrategyOptionsEndpoints:
    @patch("pilots.paper_broker.get_strategy_options_candidates")
    def test_get_strategy_options_candidates_endpoint(self, mock_get_candidates):
        mock_get_candidates.return_value = [
            {"symbol": "AAPL", "strategy": "Put Credit Spread", "action": "Open"}
        ]
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/paper-broker/strategy-options/candidates?symbols=AAPL",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["candidates"][0]["symbol"] == "AAPL"

    @patch("pilots.paper_broker.execute_strategy_options")
    def test_post_strategy_options_execute_endpoint(self, mock_exec):
        mock_exec.return_value = {
            "executed_count": 1,
            "skipped_count": 0,
            "failed_count": 0,
            "executed": [{"symbol": "AAPL", "strategy": "Put Credit Spread"}],
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/strategy-options/execute",
                json={"symbols": ["AAPL"], "dry_run": False},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["executed_count"] == 1
        assert body["executed"][0]["symbol"] == "AAPL"

    @patch("pilots.paper_broker.execute_strategy_options")
    def test_post_strategy_options_execute_fails_closed_when_writes_disabled(self, mock_exec):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/paper-broker/strategy-options/execute",
                json={"symbols": ["AAPL"]},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403
        mock_exec.assert_not_called()

    @patch("pilots.paper_broker.get_portfolio_greeks")
    def test_get_portfolio_greeks_endpoint(self, mock_get_greeks):
        mock_get_greeks.return_value = {
            "total_positions": 1,
            "net_delta_shares": 100.0,
            "net_dollar_delta": 15000.0,
            "net_gamma": 0.0,
            "net_theta_daily": 0.0,
            "net_vega_1pct": 0.0,
            "beta_weighted_delta_spy": 30.0,
            "positions": [],
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/paper-broker/greeks",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_positions"] == 1
        assert body["net_delta_shares"] == 100.0
        assert body["net_dollar_delta"] == 15000.0

    @patch("validation.options_harness.OptionsValidationHarness.run_backtest")
    def test_options_backtest_endpoint(self, mock_run_bt):
        from validation.options_harness import OptionsBacktestResult
        mock_run_bt.return_value = OptionsBacktestResult(
            strategy_name="Put Credit Spread",
            ticker="SPY",
            start_date="2020-01-01",
            end_date="2024-01-01",
            initial_capital=100000.0,
            final_capital=115000.0,
            total_return_pct=15.0,
            annualized_return_pct=3.5,
            sharpe_ratio=1.45,
            sortino_ratio=1.85,
            max_drawdown_pct=5.5,
            total_trades=20,
            winning_trades=16,
            losing_trades=4,
            win_rate_pct=80.0,
            profit_factor=2.5,
            avg_win=600.0,
            avg_loss=400.0,
            pbo=0.10,
            dsr=0.95,
            passes_stress=True,
            deployable=True,
            equity_curve=[],
            trades=[],
        )
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/backtest",
                json={"strategy": "Put Credit Spread", "ticker": "SPY", "start_date": "2020-01-01", "end_date": "2024-01-01"},
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["strategy_name"] == "Put Credit Spread"
        assert body["sharpe_ratio"] == 1.45
        assert body["passes_stress"] is True
        assert body["deployable"] is True

    @patch("ml.options_meta_labeler.global_options_meta_labeler")
    def test_options_meta_model_status_endpoint(self, mock_model):
        mock_model.n_samples = 1500
        mock_model.train_accuracy = 0.825
        mock_model.train_roc_auc = 0.86
        mock_model.trained_at = None

        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/meta-model/status",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["n_samples"] == 1500
        assert body["train_accuracy"] == 82.5
        assert body["train_roc_auc"] == 0.86

    @patch("data.paper_account_store.PaperAccountStore.settle_expired_options")
    def test_paper_broker_settle_expired_endpoint(self, mock_settle):
        mock_settle.return_value = [{"symbol": "AAPL 2023-01-20 $150.00 CALL", "cash_settlement": 500.0}]

        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/settle-expired",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["settled_count"] == 1
        assert body["settled"][0]["cash_settlement"] == 500.0

    @patch("data.paper_account_store.PaperAccountStore.settle_expired_options")
    @patch("data.market_data.get_provider")
    def test_paper_broker_settle_expired_endpoint_degrades_when_provider_construction_fails(
        self, mock_get_provider, mock_settle
    ):
        """Regression: post_paper_broker_settle_expired's `except Exception: engine =
        None` branch (added logging in the 2026-08 mcp-widget-contracts fix) must still
        let the endpoint succeed rather than crashing -- PaperAccountStore.settle_expired_options
        tolerates market_provider=None by skipping mark-to-market pricing for expired
        contracts honestly (CONSTRAINT #4/#6), it does not raise."""
        mock_get_provider.side_effect = RuntimeError("boom: provider construction failed")
        mock_settle.return_value = []

        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/settle-expired",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["settled_count"] == 0
        # The failed engine construction must degrade to market_provider=None
        # rather than propagating the exception or silently fabricating a provider.
        mock_settle.assert_called_once_with(market_provider=None)


class TestManageExitsEndpoint:
    @patch("pilots.paper_broker.manage_position_exits")
    def test_post_manage_exits_success(self, mock_manage):
        mock_manage.return_value = {
            "evaluated_count": 2,
            "executed_count": 1,
            "failed_count": 0,
            "executed": [{"symbol": "AAPL", "position_symbol": "AAPL 2026-08-21 $150.00 CALL", "reason": "PROFIT_TARGET"}],
            "failed": [],
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/manage-exits",
                json={"dry_run": False, "profit_target_pct": 0.50},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["evaluated_count"] == 2
        assert body["executed_count"] == 1
        assert body["executed"][0]["symbol"] == "AAPL"
        mock_manage.assert_called_once_with(
            dry_run=False,
            profit_target_pct=0.50,
            stop_loss_multiple=None,
            manage_dte_threshold=None,
        )

    def test_post_manage_exits_fails_closed_when_writes_disabled(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/paper-broker/manage-exits",
                json={"dry_run": False},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_post_manage_exits_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/manage-exits",
                json={"dry_run": False},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


class TestRollEndpoint:
    @patch("pilots.paper_broker.execute_roll")
    def test_post_roll_success(self, mock_roll):
        mock_roll.return_value = {
            "ok": True,
            "order_id": "ROLL-AAPL-12345",
            "symbol": "AAPL",
            "contracts": 1,
            "message": "Successfully rolled 1 contract(s) for AAPL",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/roll",
                json={
                    "symbol": "AAPL",
                    "close_legs": [{"symbol": "AAPL 2026-08-21 $150.00 CALL", "side": "buy", "fill_price": 2.50}],
                    "open_legs": [{"symbol": "AAPL 2026-09-18 $155.00 CALL", "side": "sell", "fill_price": 4.00}],
                    "contracts": 1,
                    "limit_price": 1.50,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["order_id"] == "ROLL-AAPL-12345"
        mock_roll.assert_called_once()

    def test_post_roll_fails_closed_when_writes_disabled(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/paper-broker/roll",
                json={
                    "symbol": "AAPL",
                    "close_legs": [],
                    "open_legs": [],
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_post_roll_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/roll",
                json={
                    "symbol": "AAPL",
                    "close_legs": [],
                    "open_legs": [],
                },
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_roll_live_mode_advisory_rejection(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/roll",
                json={
                    "symbol": "AAPL",
                    "close_legs": [],
                    "open_legs": [],
                    "is_live": True,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "Advisory-Only" in body["message"]


class TestDeltaHedgeEndpoints:
    @patch("pilots.options_hedging.get_delta_hedge_preview")
    def test_get_delta_hedge_preview_success(self, mock_preview):
        mock_preview.return_value = {
            "symbol": "SPY",
            "net_dollar_delta": 25000.0,
            "beta_weighted_delta_spy": 50.0,
            "target_hedge_shares": -50.0,
            "tolerance_band_shares": 25.0,
            "action": "SELL",
            "shares": 50.0,
            "required_action": True,
            "reason": "Delta imbalance (+50.00 SPY-equiv) exceeds tolerance band (±25.0 shares)",
            "spy_spot": 500.0,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/paper-broker/delta-hedge/preview",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "SELL"
        assert body["shares"] == 50.0
        assert body["required_action"] is True
        assert body["symbol"] == "SPY"

    def test_get_delta_hedge_preview_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/paper-broker/delta-hedge/preview",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    @patch("pilots.options_hedging.execute_delta_hedge")
    def test_post_delta_hedge_execute_success(self, mock_exec):
        mock_exec.return_value = {
            "ok": True,
            "action": "SELL",
            "shares": 50.0,
            "symbol": "SPY",
            "order_id": "HEDGE-SPY-12345",
            "message": "Successfully executed delta hedge: SELL 50.00 SPY @ $500.00",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/delta-hedge/execute",
                json={"dry_run": False, "shares": 50.0},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["action"] == "SELL"
        assert body["shares"] == 50.0

    def test_post_delta_hedge_execute_fails_closed_when_writes_disabled(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/paper-broker/delta-hedge/execute",
                json={"dry_run": False},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_post_delta_hedge_execute_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/paper-broker/delta-hedge/execute",
                json={"dry_run": False},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


class TestVolSurfaceEndpoint:
    @patch("pilots.volatility_surface.get_volatility_surface_data")
    def test_get_vol_surface_success(self, mock_vol):
        # Real shape produced by calculate_volatility_surface()/get_volatility_surface_data()
        # (see tests/test_volatility_surface.py) -- NOT the frontend's VolSurfaceResponse
        # contract. The endpoint is responsible for reshaping via to_vol_surface_response()
        # (docs/known_issues/scenario_matrix_field_mismatch.md's bug class -- here `smiles`
        # dict-of-expirations vs. a flat `smile_points` array, `skew_summary` vs. `skew`,
        # `term_structure` as an interpolated-grid object vs. a per-expiration array).
        mock_vol.return_value = {
            "symbol": "SPY",
            "spot_price": 500.0,
            "as_of": "2026-08-19",
            "expirations": ["2026-09-18"],
            "smiles": {
                "2026-09-18": {
                    "expiration": "2026-09-18",
                    "dte": 30,
                    "atm_iv": 0.22,
                    "skew_25d": 0.04,
                    "put_25d_iv": 0.24,
                    "call_25d_iv": 0.20,
                    "curve": [
                        {"strike": 500.0, "moneyness": 1.0, "iv": 0.22, "call_delta": 0.50, "put_delta": -0.50},
                    ],
                    "strikes": [
                        {"strike": 500.0, "moneyness": 1.0, "iv": 0.22, "call_bid": 4.5, "call_ask": 4.7, "put_bid": None, "put_ask": None},
                    ],
                }
            },
            "term_structure": {"points": [], "term_slope_30_90": None, "term_slope_7_30": None, "structure_regime": "unknown"},
            "skew_summary": {"front_month_skew_25d": 0.04, "average_skew_25d": 0.04, "expirations_skew": {"2026-09-18": 0.04}},
            "vrp_cone": {
                "10d": {"window_days": 10, "implied_vol": 0.22, "realized_vol": 0.19, "vrp": 0.03, "vrp_ratio": 1.16, "regime": "premium_rich"},
                "30d": {"window_days": 30, "implied_vol": 0.22, "realized_vol": 0.18, "vrp": 0.04, "vrp_ratio": 1.22, "regime": "premium_rich"},
            },
            "surface_grid": [],
            "missing_data": False,
            "reason": None,
            "warnings": [],
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["spot_price"] == 500.0
        assert body["selected_expiration"] == "2026-09-18"
        # Reshaped field names the frontend (VolSurfaceView.tsx / webapp/src/api/types.ts)
        # actually reads.
        assert len(body["smile_points"]) == 1
        assert body["smile_points"][0]["strike"] == 500.0
        assert len(body["term_structure"]) == 1
        assert body["term_structure"][0]["atm_iv"] == 0.22
        assert body["skew"]["skew_25delta"] == 0.04
        assert body["skew"]["put_25delta_iv"] == 0.24
        assert body["skew"]["call_25delta_iv"] == 0.20
        assert body["skew"]["realized_vol_30d"] == 0.18
        assert body["skew"]["vrp_spread"] == 0.04
        # Raw internal keys must NOT leak through.
        assert "smiles" not in body
        assert "skew_summary" not in body
        assert "surface_grid" not in body

    def test_get_vol_surface_requires_symbol(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_vol_surface_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface?symbol=SPY",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


class TestScenarioMatrixEndpoint:
    @patch("pilots.scenario_matrix.evaluate_portfolio_scenario_matrix")
    def test_post_scenario_matrix_success(self, mock_matrix):
        mock_matrix.return_value = {
            "spot_shifts": [-10.0, -5.0, 0.0, 5.0, 10.0],
            "iv_shifts": [-20.0, 0.0, 20.0],
            "time_shifts": [0, 7, 14, 21, 30],
            "time_days_forward": 0,
            "current_portfolio_value": 10500.0,
            "total_positions_evaluated": 2,
            "matrix": [
                {"spot_shift_pct": 0.0, "iv_shift_pct": 0.0, "pl_change": 0.0, "net_delta": 50.0, "net_gamma": 0.02, "net_theta": -5.0, "net_vega": 25.0}
            ],
            "historical_presets": [
                {"id": "lehman_2008", "name": "Lehman Brothers", "projected_pl": -1500.0, "projected_pl_pct": -14.29}
            ],
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/paper-broker/scenario-matrix",
                json={"time_days_forward": 0},
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_portfolio_value"] == 10500.0
        assert body["total_positions_evaluated"] == 2
        assert len(body["matrix"]) == 1
        assert len(body["historical_presets"]) == 1

    def test_post_scenario_matrix_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/paper-broker/scenario-matrix",
                json={},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


class TestEarningsCrushEndpoints:
    @patch("pilots.earnings_crush.get_earnings_crush_candidates")
    def test_get_earnings_crush_candidates_success(self, mock_get_candidates):
        # Real shape produced by evaluate_earnings_crush_candidates() /
        # get_earnings_crush_candidates() (see tests/test_earnings_crush.py) --
        # NOT the frontend's EarningsCrushCandidate contract. The endpoint is
        # responsible for reshaping via to_earnings_crush_candidate_response()
        # (docs/known_issues/scenario_matrix_field_mismatch.md's bug class).
        mock_get_candidates.return_value = [
            {
                "symbol": "NVDA",
                "spot": 125.0,
                "earnings_date": "2026-08-20",
                "days_to_earnings": 2,
                "expiration": "2026-08-21",
                "dte": 3,
                "atm_iv": 0.65,
                "expected_move_usd": 11.20,
                "expected_move_pct": 0.0896,
                "realized_move_pct": 0.055,
                "crush_edge_ratio": 1.63,
                "is_recommended": True,
                "strategy": "Iron Condor",
                "strikes": {
                    "long_put": 110.0,
                    "short_put": 114.0,
                    "short_call": 136.0,
                    "long_call": 140.0,
                },
                "legs": [],
                "net_credit": 1.40,
                "max_profit": 140.0,
                "max_loss": 260.0,
                "pricing_is_estimated": False,
                "historical_summary": {
                    "quarters_count": 8,
                    "median_move_pct": 0.055,
                    "sparse_history": False,
                    "fallback": False,
                },
            }
        ]
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/earnings-crush/candidates?symbols=NVDA&min_edge=1.25",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        candidate = body["candidates"][0]
        assert candidate["symbol"] == "NVDA"
        assert candidate["crush_edge_ratio"] == 1.63
        assert candidate["edge_passed"] is True
        # Reshaped field names the frontend (EarningsCrushScanner.tsx /
        # webapp/src/api/types.ts) actually reads -- a bare pass-through of the
        # raw candidate dict would leave these absent and crash the UI.
        assert candidate["spot_price"] == 125.0
        assert candidate["report_date"] == "2026-08-20"
        assert candidate["expected_move_dollar"] == 11.20
        assert candidate["median_realized_move_pct"] == 0.055
        assert candidate["suggested_strategy"] == "Iron Condor"
        assert candidate["estimated_credit"] == 1.40
        assert candidate["put_wing_strike"] == 110.0
        assert candidate["short_put_strike"] == 114.0
        assert candidate["short_call_strike"] == 136.0
        assert candidate["call_wing_strike"] == 140.0

    def test_get_earnings_crush_candidates_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/earnings-crush/candidates?symbols=NVDA",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    @patch("pilots.earnings_crush.execute_earnings_crush_trade")
    def test_post_earnings_crush_execute_success(self, mock_exec):
        mock_exec.return_value = {
            "ok": True,
            "order_id": "ec_12345678",
            "symbol": "NVDA",
            "strategy": "Iron Condor",
            "contracts": 1,
            "message": "Successfully executed Iron Condor earnings crush trade for NVDA.",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/earnings-crush/execute",
                json={
                    "symbol": "NVDA",
                    "strategy": "Iron Condor",
                    "contracts": 1,
                    "dry_run": False,
                    # earnings_crush is an UNGATEABLE_DATA_GAP strategy -- execution is
                    # blocked by default (see CLAUDE.md's "Reversed 2026-08-29" note);
                    # this test exercises real fill wiring, not the gate itself, so it
                    # opts in explicitly like an operator taking deliberate responsibility.
                    "override_deployability_gate": True,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["order_id"] == "ec_12345678"
        assert body["symbol"] == "NVDA"
        assert body["strategy"] == "Iron Condor"

    def test_post_earnings_crush_execute_fails_closed_when_writes_disabled(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/options/earnings-crush/execute",
                json={"symbol": "NVDA"},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_post_earnings_crush_execute_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/earnings-crush/execute",
                json={"symbol": "NVDA"},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_earnings_crush_execute_live_mode_advisory_rejection(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/earnings-crush/execute",
                # override_deployability_gate=True so this reaches the advisory-only
                # guard inside execute_earnings_crush_trade rather than being blocked
                # earlier by the (independent, both-enforced) deployability gate.
                json={"symbol": "NVDA", "is_live": True, "override_deployability_gate": True},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "Advisory-Only" in body["message"]

    # -----------------------------------------------------------------------
    # `degraded`/`symbols_errored` diagnostics follow-up (finding #7, this
    # repo's "distinguish nothing-found from fetch-failed" honesty fix).
    # pilots.earnings_crush.get_earnings_crush_candidates is mocked with a
    # side_effect that mutates the `diagnostics` dict it receives, simulating
    # the real implementation's contract (see pilots/earnings_crush.py).
    # -----------------------------------------------------------------------

    @patch("pilots.earnings_crush.get_earnings_crush_candidates")
    def test_get_earnings_crush_candidates_degraded_true_when_store_unavailable(self, mock_get_candidates):
        def _side_effect(*, symbols=None, min_edge=None, store=None, diagnostics=None):
            if diagnostics is not None:
                diagnostics["store_available"] = False
                diagnostics["options_provider_available"] = True
                diagnostics["symbols_errored"] = []
            return []

        mock_get_candidates.side_effect = _side_effect
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/earnings-crush/candidates?symbols=NVDA",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is True
        assert body["candidates"] == []

    @patch("pilots.earnings_crush.get_earnings_crush_candidates")
    def test_get_earnings_crush_candidates_degraded_false_when_healthy(self, mock_get_candidates):
        def _side_effect(*, symbols=None, min_edge=None, store=None, diagnostics=None):
            if diagnostics is not None:
                diagnostics["store_available"] = True
                diagnostics["options_provider_available"] = True
                diagnostics["symbols_errored"] = []
            return []

        mock_get_candidates.side_effect = _side_effect
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/earnings-crush/candidates?symbols=NVDA",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is False
        assert body["symbols_errored"] == []


class TestUnusualFlowEndpoints:
    @patch("pilots.unusual_options_flow.get_unusual_options_activity")
    def test_get_unusual_flow_success(self, mock_get_uoa):
        mock_get_uoa.return_value = [
            {
                "id": "uoa_12345",
                "timestamp": "2026-08-14T15:00:00Z",
                "symbol": "NVDA",
                "expiration": "2026-08-21",
                "strike": 130.0,
                "option_type": "CALL",
                "trade_type": "SWEEP",
                "sentiment": "BULLISH",
                "volume": 15000,
                "open_interest": 2500,
                "vol_oi_ratio": 6.0,
                "price": 4.50,
                "bid": 4.40,
                "ask": 4.50,
                "notional": 6750000.0,
                "iv": 0.62,
                "iv_anomaly": True,
            }
        ]
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/unusual?symbols=NVDA&min_vol_oi=3.0",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["records"][0]["symbol"] == "NVDA"
        assert body["records"][0]["vol_oi_ratio"] == 6.0
        assert body["records"][0]["trade_type"] == "SWEEP"
        assert body["records"][0]["sentiment"] == "BULLISH"

    @patch("pilots.unusual_options_flow.get_unusual_options_activity")
    def test_get_unusual_flow_honors_singular_symbol_param(self, mock_get_uoa):
        # webapp/src/api/client.ts::getUnusualOptionsFlow sends `symbol` (singular),
        # not `symbols` -- before this fix the query param had no matching handler
        # argument, so FastAPI silently ignored it and the ticker filter was a
        # complete no-op against the live backend.
        mock_get_uoa.return_value = []
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/unusual?symbol=NVDA",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        mock_get_uoa.assert_called_once()
        assert mock_get_uoa.call_args.kwargs["symbols"] == ["NVDA"]

    def test_get_unusual_flow_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/unusual",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    # -----------------------------------------------------------------------
    # `degraded`/`symbols_fetch_failed` diagnostics follow-up (finding #7, this
    # repo's "distinguish nothing-found from fetch-failed" honesty fix).
    # pilots.unusual_options_flow.get_unusual_options_activity does not yet
    # carry the `diagnostics` kwarg on THIS branch (it's implemented on a
    # sibling branch, unusual-options-flow-engine-fixes) -- so it is mocked
    # here with a side_effect that mutates the `diagnostics` dict it
    # receives, simulating the real implementation's contract (see the task
    # description / docs/known_issues/earnings_crush_uoa_followup_audit_findings.md).
    # -----------------------------------------------------------------------

    @patch("pilots.unusual_options_flow.get_unusual_options_activity")
    def test_get_unusual_flow_degraded_true_on_fetch_failure(self, mock_get_uoa):
        def _side_effect(*, symbols=None, min_vol_oi=None, min_notional=None, limit=50, diagnostics=None):
            if diagnostics is not None:
                diagnostics["symbols_fetch_failed"] = ["XYZ"]
                diagnostics["read_from_cache"] = False
            return []

        mock_get_uoa.side_effect = _side_effect
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/unusual",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is True
        assert body["symbols_fetch_failed"] == ["XYZ"]

    @patch("pilots.unusual_options_flow.get_unusual_options_activity")
    def test_get_unusual_flow_degraded_false_when_served_from_cache(self, mock_get_uoa):
        def _side_effect(*, symbols=None, min_vol_oi=None, min_notional=None, limit=50, diagnostics=None):
            if diagnostics is not None:
                diagnostics["symbols_fetch_failed"] = []
                diagnostics["read_from_cache"] = True
            return [{"symbol": "NVDA", "strike": 130.0}]

        mock_get_uoa.side_effect = _side_effect
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/unusual",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["degraded"] is False
        assert body["count"] == 1

    @patch("pilots.unusual_options_flow.get_flow_sentiment")
    def test_get_flow_sentiment_success(self, mock_sentiment):
        # Real shape produced by calculate_net_flow_sentiment()/get_flow_sentiment()
        # (see tests/test_unusual_options_flow.py) -- NOT the frontend's FlowSentimentData
        # contract. The endpoint is responsible for reshaping via
        # to_flow_sentiment_response() (docs/known_issues/scenario_matrix_field_mismatch.md's
        # bug class -- here call_put_ratio vs. put_call_ratio, a reciprocal, not a rename).
        mock_sentiment.return_value = {
            "symbol": "NVDA",
            "sentiment_score": 0.72,
            "sentiment_label": "VERY_BULLISH",
            "bullish_notional": 18500000.0,
            "bearish_notional": 3000000.0,
            "neutral_notional": 0.0,
            "total_notional": 21500000.0,
            "call_volume": 45000,
            "put_volume": 12000,
            "call_put_ratio": 3.75,
            "top_active_strikes": [{"strike": 130.0, "volume": 15000, "option_type": "CALL", "notional": 6500000.0}],
            "record_count": 8,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/sentiment?symbol=NVDA",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "NVDA"
        assert body["sentiment_score"] == 0.72
        assert body["sentiment_label"] == "VERY_BULLISH"
        # Reshaped field the frontend (UnusualFlowFeed.tsx / webapp/src/api/types.ts)
        # actually reads -- put_call_ratio is the RECIPROCAL of call_put_ratio
        # (put_volume / call_volume = 12000 / 45000), not merely a renamed copy.
        assert "call_put_ratio" not in body
        assert abs(body["put_call_ratio"] - (12000 / 45000)) < 1e-4
        assert len(body["top_active_strikes"]) == 1

    def test_get_flow_sentiment_requires_symbol(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/sentiment",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_flow_sentiment_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/sentiment?symbol=NVDA",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 1. GET /pilots/options/forecast/har-rv
# ---------------------------------------------------------------------------


class TestOptionsForecastHarRvEndpoint:
    def test_get_forecast_har_rv_success(self):
        # The live endpoint reshapes get_har_volatility_forecast()'s raw internal result
        # (forecast_annualized_vol/model_fit/forecast_rv_1d, in daily-VARIANCE units -- see
        # tests/test_har_volatility.py for coverage of that shape) into the frontend's
        # HarRvForecastResponse contract via to_har_rv_forecast_response() -- ANNUALIZED
        # VOLATILITY, and a `coefficients` object instead of a bare `model_fit` dict.
        # webapp/src/components/options/VolForecastScanner.tsx reads
        # forecast.coefficients.beta_0 unconditionally; a bare pass-through of the raw
        # result (no `coefficients` key at all) crashed that panel on every live load.
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/har-rv?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "fair_iv_blend" in body
        assert body["fair_iv_blend"] is not None and body["fair_iv_blend"] > 0
        for key in ("rv_daily", "rv_weekly", "rv_monthly", "forecast_vol_1d", "forecast_vol_5d", "forecast_vol_22d", "forecast_vol_30d"):
            assert key in body and body[key] is not None and body[key] >= 0
        assert "coefficients" in body
        coeffs = body["coefficients"]
        assert "beta_0" in coeffs and "beta_d" in coeffs and "beta_w" in coeffs and "beta_m" in coeffs
        assert coeffs["beta_d"] >= 0 and coeffs["beta_w"] >= 0 and coeffs["beta_m"] >= 0
        # The raw internal keys must NOT leak through -- they'd be a silent contract drift
        # the frontend would never notice (it never reads them).
        assert "model_fit" not in body
        assert "forecast_annualized_vol" not in body

    def test_get_forecast_har_rv_requires_symbol(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/har-rv",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_forecast_har_rv_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/har-rv?symbol=SPY",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_get_forecast_har_rv_no_token_fail_open(self):
        with mock_patch_settings(STATE_API_TOKEN=""):
            resp = _client.get("/pilots/options/forecast/har-rv?symbol=SPY")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# 2. GET /pilots/options/forecast/mispricing
# ---------------------------------------------------------------------------


class TestOptionsForecastMispricingEndpoint:
    def test_get_forecast_mispricing_success(self):
        # The live endpoint reshapes get_volatility_mispricing_data()'s raw internal
        # result (MispricingAnalysis.to_dict() -- baseline_fair_iv/rich_candidates_count/
        # strike_mispricings with valuation_tag+spread -- see tests/test_vol_mispricing.py
        # for coverage of that shape) into the frontend's VolMispricingResponse contract
        # via to_vol_mispricing_response(): fair_iv_baseline/rich_strikes_count/strikes
        # with classification+iv_spread+suggested_action, plus trade_recommendations.
        # webapp/src/components/options/VolForecastScanner.tsx reads
        # `s.classification === "RICH"` per strike -- a bare pass-through of the raw
        # `valuation_tag` field name meant the Rich/Cheap filter buttons silently
        # returned zero results forever on live data (CONSTRAINT #4: a silent "0 rich
        # strikes" is exactly the unannounced-fabrication failure mode to catch here).
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/mispricing?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "spot_price" in body
        assert "fair_iv_baseline" in body
        assert "market_atm_iv" in body
        assert "rich_strikes_count" in body
        assert "cheap_strikes_count" in body
        assert "strikes" in body
        assert len(body["strikes"]) > 0
        first = body["strikes"][0]
        assert "classification" in first and first["classification"] in ("RICH", "CHEAP", "NEUTRAL", "UNKNOWN")
        assert "iv_spread" in first
        assert "suggested_action" in first
        # Raw internal keys must NOT leak through.
        assert "valuation_tag" not in first
        assert "spread" not in first
        assert "baseline_fair_iv" not in body
        assert "rich_candidates_count" not in body
        assert "strike_mispricings" not in body

    def test_get_forecast_mispricing_requires_symbol(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/mispricing",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_forecast_mispricing_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/mispricing?symbol=SPY",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_get_forecast_mispricing_no_token_fail_open(self):
        with mock_patch_settings(STATE_API_TOKEN=""):
            resp = _client.get("/pilots/options/forecast/mispricing?symbol=SPY")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# 3. POST /pilots/options/gamma-scalp/simulate
# ---------------------------------------------------------------------------


class TestOptionsGammaScalpSimulateEndpoint:
    # The live endpoint reshapes simulate_gamma_scalping()'s raw internal result
    # (success/attribution/path_history/theoretical_gamma_rent -- see
    # tests/test_gamma_scalper.py for coverage of that shape) into the frontend's
    # GammaScalpResponse contract via to_gamma_scalp_response(): gamma_rent_total/
    # theta_burn_total/transaction_costs/pnl_path (not path_history).
    # webapp/src/components/options/GammaScalperView.tsx reads `result.pnl_path.length`
    # unconditionally right after the panel auto-simulates on mount; a bare pass-through
    # of the raw result (no `pnl_path` key at all) crashed that panel immediately every
    # time it opened.
    def test_post_gamma_scalp_simulate_default(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/gamma-scalp/simulate",
                json={},
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "rebalance_count" in body
        assert "stock_pnl" in body
        assert "option_pnl" in body
        assert "total_pnl" in body
        assert "gamma_rent_total" in body
        assert "theta_burn_total" in body
        assert "transaction_costs" in body
        assert "trades" in body
        assert "pnl_path" in body
        # Raw internal keys must NOT leak through.
        assert "attribution" not in body
        assert "path_history" not in body
        assert "theoretical_gamma_rent" not in body

    def test_post_gamma_scalp_simulate_custom_path(self):
        # Still supports the raw/advanced position+price_path shape directly.
        custom_payload = {
            "position": {
                "symbol": "SPY",
                "strategy": "Long Straddle",
                "spot_price": 100.0,
                "strike": 100.0,
                "dte": 30,
                "implied_vol": 0.25,
                "contracts": 1,
            },
            "price_path": [100.0, 104.0, 96.0, 104.0, 96.0, 100.0],
            "delta_threshold": 0.08,
            "dt_days": 0.1,
            "transaction_cost_per_share": 0.005,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/gamma-scalp/simulate",
                json=custom_payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["rebalance_count"] >= 1
        assert len(body["trades"]) >= 1
        assert len(body["pnl_path"]) == 6
        first_trade = body["trades"][0]
        assert first_trade["side"] in ("BUY", "SELL", "HOLD")
        assert "shares_traded" in first_trade
        assert "cash_flow" in first_trade
        assert "total_pnl" in first_trade

    def test_post_gamma_scalp_simulate_webapp_request_shape_is_honored(self):
        # webapp/src/api/client.ts::simulateGammaScalping posts exactly this flat shape
        # (GammaScalpRequest) -- before this fix, none of these fields had a matching
        # Pydantic model field, so every operator-configured symbol/strike/IV/option-type/
        # contracts/price-path selection was silently dropped and the live endpoint always
        # simulated a hardcoded default position on a freshly regenerated random path.
        payload = {
            "symbol": "NVDA",
            "spot_price": 128.5,
            "option_type": "PUT",
            "strike": 125.0,
            "contracts": 5,
            "delta_threshold": 0.10,
            "iv": 0.55,
            "underlying_price_path": [128.5, 130.0, 126.0, 129.0],
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/gamma-scalp/simulate",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "NVDA"
        assert body["price_path"] == [128.5, 130.0, 126.0, 129.0]

    def test_post_gamma_scalp_simulate_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/gamma-scalp/simulate",
                json={},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_gamma_scalp_simulate_no_token_fail_open(self):
        with mock_patch_settings(STATE_API_TOKEN=""):
            resp = _client.post(
                "/pilots/options/gamma-scalp/simulate",
                json={},
            )
        assert resp.status_code == 200
        assert "total_pnl" in resp.json()


# ---------------------------------------------------------------------------
# 4. POST /pilots/options/alerts/test
# ---------------------------------------------------------------------------


class TestOptionsAlertsTestEndpoint:
    def test_post_options_alerts_test_whale_uoa(self):
        payload = {
            "alert_type": "whale_uoa",
            "payload": {
                "symbol": "NVDA",
                "strike": 130.0,
                "option_type": "CALL",
                "expiration": "2026-08-21",
                "vol_oi_ratio": 7.5,
                "notional": 650000.0,
                "trade_type": "SWEEP",
            },
            "channels": ["console"],
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/options/alerts/test",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["success"] is True
        assert body["alert_type"] == "whale_uoa"
        assert "Whale" in body["title"]
        assert body["level"] == "WARNING"
        assert "NVDA" in body["message"]

    def test_post_options_alerts_test_earnings_crush(self):
        payload = {
            "alert_type": "earnings_crush",
            "payload": {
                "symbol": "AMD",
                "edge_ratio": 1.45,
                "implied_move_pct": 8.0,
                "historical_move_pct": 5.2,
            },
            "channels": ["console"],
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/options/alerts/test",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "Earnings" in body["title"]
        assert "AMD" in body["message"]

    def test_post_options_alerts_test_delta_hedge(self):
        payload = {
            "alert_type": "delta_hedge",
            "payload": {
                "symbol": "SPY",
                "beta_weighted_delta_spy": 65.0,
                "shares_needed": -65,
            },
            "channels": ["console"],
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/options/alerts/test",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "Delta Hedge" in body["title"]

    def test_post_options_alerts_test_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/options/alerts/test",
                json={"alert_type": "custom"},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_options_alerts_test_fails_closed_without_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/options/alerts/test",
                json={"alert_type": "custom"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5. GET /pilots/options/dispersion/opportunities & POST /pilots/options/dispersion/execute
# ---------------------------------------------------------------------------


_MOCK_DISPERSION_INPUTS = (
    {"QQQ": 450.0, "SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0, "NVDA": 120.0, "AMZN": 180.0, "GOOGL": 165.0, "META": 500.0, "TSLA": 210.0, "AVGO": 160.0},
    {"QQQ": 0.22, "SPY": 0.18, "AAPL": 0.28, "MSFT": 0.25, "NVDA": 0.45, "AMZN": 0.32, "GOOGL": 0.30, "META": 0.35, "TSLA": 0.50, "AVGO": 0.38},
    0.45,
)


class TestOptionsDispersionEndpoints:
    @patch("pilots.dispersion_trading._source_real_dispersion_inputs", return_value=_MOCK_DISPERSION_INPUTS)
    def test_get_dispersion_opportunities_success(self, mock_inputs):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/dispersion/opportunities",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body
        assert body["count"] >= 1
        assert "opportunities" in body
        opp = body["opportunities"][0]
        assert "index_symbol" in opp
        assert "implied_correlation" in opp
        assert "realized_correlation" in opp
        assert "correlation_spread" in opp
        assert "regime" in opp
        # Flat card shape from `_opportunity_to_frontend_card()` -- matches
        # webapp/src/api/types.ts::DispersionOpportunity, NOT the raw nested `basket`
        # shape `evaluate_dispersion_opportunity()` itself returns.
        assert "id" in opp
        assert "index_spot" in opp
        assert "index_iv" in opp
        assert "vega_neutrality_ratio" in opp
        assert "constituents" in opp
        assert isinstance(opp["constituents"], list)
        assert opp["constituents"]
        assert "symbol" in opp["constituents"][0]
        assert "weight" in opp["constituents"][0]

    def test_get_dispersion_opportunities_with_index_filter(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/dispersion/opportunities?index=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["opportunities"][0]["index_symbol"] == "SPY"

    def test_get_dispersion_opportunities_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=""):
            resp = _client.get("/pilots/options/dispersion/opportunities")
        assert resp.status_code == 200
        assert "opportunities" in resp.json()

    def test_get_dispersion_opportunities_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/dispersion/opportunities",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_dispersion_execute_fails_closed_when_writes_disabled(self):
        payload = {
            "index_symbol": "QQQ",
            "dry_run": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/options/dispersion/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_post_dispersion_execute_fails_closed_with_wrong_token(self):
        payload = {"index_symbol": "QQQ", "dry_run": True}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/dispersion/execute",
                json=payload,
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    @patch("pilots.dispersion_trading._source_real_dispersion_inputs", return_value=_MOCK_DISPERSION_INPUTS)
    def test_post_dispersion_execute_dry_run(self, mock_inputs):
        payload = {
            "index_symbol": "QQQ",
            "dry_run": True,
            # dispersion_trading is an UNGATEABLE_DATA_GAP strategy -- blocked by
            # default (see CLAUDE.md's "Reversed 2026-08-29" note); this test
            # exercises the real dry-run preview, not the gate, so it opts in.
            "override_deployability_gate": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/dispersion/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["dry_run"] is True
        assert body["index_symbol"] == "QQQ"
        assert "Dry run" in body["message"]

    def test_post_dispersion_execute_live_advisory_rejection(self):
        payload = {
            "index_symbol": "QQQ",
            "is_live": True,
            # override so this reaches the advisory-only guard rather than the
            # (independent, both-enforced) deployability gate.
            "override_deployability_gate": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/dispersion/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "Advisory-Only" in body["message"]

    @patch("pilots.dispersion_trading._source_real_dispersion_inputs", return_value=_MOCK_DISPERSION_INPUTS)
    @patch("pilots.dispersion_trading.PaperAccountStore")
    def test_post_dispersion_execute_real_paper_execution(self, mock_store_cls, mock_inputs):
        mock_store = mock_store_cls.return_value
        mock_store.apply_multi_leg_fill.return_value = True

        payload = {
            "index_symbol": "QQQ",
            "dry_run": False,
            "is_live": False,
            "override_deployability_gate": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/dispersion/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "execution_id" in body
        assert body["executed_orders_count"] >= 2
        assert mock_store.apply_multi_leg_fill.called


# ---------------------------------------------------------------------------
# 6. GET /pilots/options/zero-dte/signals & POST /pilots/options/zero-dte/execute
# ---------------------------------------------------------------------------


class TestOptionsZeroDteEndpoints:
    def test_get_zero_dte_signals_success(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/zero-dte/signals?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "as_of" in body
        # `{signals: [...]}` card shape from `get_0dte_signals_for_frontend()` -- matches
        # webapp/src/api/types.ts::ZeroDteSignalResponse, NOT get_0dte_signals()'s own
        # flat internal dict (which uses `spot`/`opening_range`/`squeeze` directly).
        assert "signals" in body
        assert len(body["signals"]) == 1
        card = body["signals"][0]
        assert card["symbol"] == "SPY"
        assert "spot_price" in card
        assert "opening_range_high" in card
        assert "opening_range_low" in card
        assert "ttm_squeeze_active" in card
        assert card["momentum_direction"] in ("BULLISH_BREAKOUT", "BEARISH_BREAKDOWN", "IN_RANGE")
        assert card["suggested_action"] in ("BUY_CALL", "BUY_PUT", "WAIT")

    def test_get_zero_dte_signals_missing_symbol_422(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/zero-dte/signals",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_zero_dte_signals_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=""):
            resp = _client.get("/pilots/options/zero-dte/signals?symbol=QQQ")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "QQQ"

    def test_get_zero_dte_signals_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/zero-dte/signals?symbol=SPY",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_zero_dte_execute_fails_closed_when_writes_disabled(self):
        payload = {
            "symbol": "SPY",
            "option_type": "CALL",
            "strike": 560.0,
            "contracts": 2,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/options/zero-dte/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_post_zero_dte_execute_fails_closed_with_wrong_token(self):
        payload = {
            "symbol": "SPY",
            "option_type": "CALL",
            "strike": 560.0,
            "contracts": 2,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/zero-dte/execute",
                json=payload,
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_zero_dte_execute_dry_run(self):
        payload = {
            "symbol": "SPY",
            "option_type": "CALL",
            "strike": 560.0,
            "contracts": 2,
            "dry_run": True,
            # zero_dte_engine is an UNGATEABLE_DATA_GAP strategy -- blocked by
            # default (see CLAUDE.md's "Reversed 2026-08-29" note); this test
            # exercises the real dry-run preview, not the gate, so it opts in.
            "override_deployability_gate": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/zero-dte/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["dry_run"] is True
        assert body["symbol"] == "SPY"
        assert body["strike"] == 560.0
        assert body["contracts"] == 2
        assert "Dry run" in body["message"]

    def test_post_zero_dte_execute_live_advisory_rejection(self):
        payload = {
            "symbol": "SPY",
            "option_type": "PUT",
            "strike": 555.0,
            "is_live": True,
            # override so this reaches the advisory-only guard rather than the
            # (independent, both-enforced) deployability gate.
            "override_deployability_gate": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/zero-dte/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "Advisory-Only" in body["message"]

    @patch("pilots.zero_dte_engine.PaperAccountStore")
    def test_post_zero_dte_execute_real_paper_execution(self, mock_store_cls):
        mock_store = mock_store_cls.return_value
        mock_store.apply_multi_leg_fill.return_value = True

        payload = {
            "symbol": "SPY",
            "option_type": "CALL",
            "strike": 560.0,
            "contracts": 3,
            "limit_price": 2.25,
            "dry_run": False,
            "is_live": False,
            "override_deployability_gate": True,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/zero-dte/execute",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "order_id" in body
        assert body["symbol"] == "SPY"
        assert body["contracts"] == 3
        assert body["fill_price"] == 225.0
        mock_store.apply_multi_leg_fill.assert_called_once()


# ---------------------------------------------------------------------------
# 7. GET /pilots/options/vpin/metrics
# ---------------------------------------------------------------------------


class TestOptionsVpinEndpoint:
    """`GET /pilots/options/vpin/metrics` now computes VPIN from REAL hourly bars fetched via
    `data.market_data.get_provider()` (a bar-level BVC approximation -- see
    `pilots/options_vpin.py`'s module docstring and
    `docs/known_issues/options_vpin_fabricated_live_data.md`), never from
    `generate_synthetic_option_trades()`'s fabricated random-walk data. Every test here mocks
    `data.market_data.get_provider` so the suite stays offline/deterministic, matching
    `tests/test_daemon_runtime.py::TestMaybeUpdateCircuitBreaker`'s established pattern for the
    identical real-bars-for-VPIN call shape.
    """

    @staticmethod
    def _fake_hourly_bars(n: int = 40, seed: int = 7):
        rng = np.random.default_rng(seed)
        prices = 500.0 + np.cumsum(rng.normal(0, 0.5, n))
        return pd.DataFrame(
            {
                "Open": prices,
                "High": prices + 0.1,
                "Low": prices - 0.1,
                "Close": prices,
                "Volume": rng.integers(1_000, 50_000, n).astype(float),
            },
            index=pd.date_range("2026-08-01 09:30", periods=n, freq="h"),
        )

    def test_get_vpin_metrics_success(self):
        class _FakeProvider:
            def get_intraday_bars(self, symbol, lookback_days=10, interval="1h"):
                return TestOptionsVpinEndpoint._fake_hourly_bars()

        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN), patch(
            "data.market_data.get_provider", lambda: _FakeProvider()
        ):
            resp = _client.get(
                "/pilots/options/vpin/metrics?symbol=SPY&num_buckets=20",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "vpin" in body
        assert 0.0 <= body["vpin"] <= 1.0
        # Real bar-level data, never the retired synthetic-trades fallback.
        assert body["data_available"] is True
        assert body["data_source"] == "bar_level_bvc_approximation"
        assert body["reason"] is None
        # Field names from `get_options_vpin_metrics_for_frontend()` -- matches
        # webapp/src/api/types.ts::VpinMetricsResponse, NOT
        # get_options_vpin_metrics()'s own internal `toxicity_regime`/`is_toxic`/
        # `bucket_history`/`recommended_spread_concession`/`sample_time` keys.
        assert body["regime"] in ["LOW", "MODERATE", "HIGH_TOXICITY"]
        assert "buckets" in body
        assert len(body["buckets"]) > 0
        bucket = body["buckets"][0]
        assert "total_volume" in bucket
        assert "imbalance" in bucket
        assert "price_start" in bucket
        assert "price_end" in bucket
        assert "defensive_spread_concession" in body
        assert "as_of" in body

    def test_get_vpin_metrics_missing_symbol_422(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vpin/metrics",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_vpin_metrics_fail_open_without_token(self):
        class _FakeProvider:
            def get_intraday_bars(self, symbol, lookback_days=10, interval="1h"):
                return TestOptionsVpinEndpoint._fake_hourly_bars()

        with mock_patch_settings(STATE_API_TOKEN=""), patch(
            "data.market_data.get_provider", lambda: _FakeProvider()
        ):
            resp = _client.get("/pilots/options/vpin/metrics?symbol=NVDA")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "NVDA"

    def test_get_vpin_metrics_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vpin/metrics?symbol=SPY",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_get_vpin_metrics_honestly_unavailable_when_no_real_data(self):
        """CONSTRAINT #4 regression: when the market-data provider cannot supply real bars
        (e.g. a bad symbol, or every provider in the fallback chain failing), the endpoint must
        return an explicit `data_available: False` / `vpin: None` response -- never silently
        substitute `generate_synthetic_option_trades()`'s fabricated data, which is what this
        endpoint did before this fix."""
        class _ExplodingProvider:
            def get_intraday_bars(self, symbol, lookback_days=10, interval="1h"):
                raise RuntimeError("simulated market data outage")

        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN), patch(
            "data.market_data.get_provider", lambda: _ExplodingProvider()
        ):
            resp = _client.get(
                "/pilots/options/vpin/metrics?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["vpin"] is None
        assert body["regime"] is None
        assert body["data_available"] is False
        assert body["data_source"] is None
        assert body["reason"] is not None
        assert body["buckets"] == []
        assert "unavailable" in body["warning_message"].lower()


# ---------------------------------------------------------------------------
# 8. POST /pilots/options/sor/analyze
# ---------------------------------------------------------------------------


class TestOptionsSorAnalyzeEndpoint:
    def test_post_sor_analyze_multi_leg_success(self):
        payload = {
            "symbol": "SPY",
            "spot_price": 500.0,
            "vpin": 0.15,
            "urgency": "NORMAL",
            "legs": [
                {
                    "symbol": "SPY 2026-09-18 $490.00 PUT",
                    "action": "SELL",
                    "type": "PUT",
                    "strike": 490.0,
                    "bid": 2.50,
                    "ask": 2.60,
                    "delta": -0.30,
                    "gamma": 0.02,
                    "ratio": 1,
                },
                {
                    "symbol": "SPY 2026-09-18 $485.00 PUT",
                    "action": "BUY",
                    "type": "PUT",
                    "strike": 485.0,
                    "bid": 1.40,
                    "ask": 1.48,
                    "delta": -0.20,
                    "gamma": 0.015,
                    "ratio": 1,
                },
            ],
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/sor/analyze",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        # Field names from `analyze_routing_options_for_frontend()` -- matches
        # webapp/src/api/types.ts::SorAnalysisResponse, NOT
        # analyze_routing_options()'s own internal `valid`/`legs_count`/
        # `cob_pricing`/`synthetic_legging`/`recommended_policy`/`policy_rationale`/
        # `policies_comparison` keys.
        assert body["symbol"] == "SPY"
        assert body["recommended_route"] in ["COB_NET_PACKAGE", "LEG_PASSIVE_FIRST", "SPLIT_DIRECT"]
        assert "cob_net_price" in body
        assert "cob_natural_price" in body
        assert "synthetic_net_price" in body
        assert "expected_savings" in body
        assert 0.0 <= body["hung_leg_probability"] <= 1.0
        assert "adverse_selection_cost" in body
        assert "rationale" in body
        assert len(body["legs_breakdown"]) == 2

    def test_post_sor_analyze_empty_legs_graceful_fallback(self):
        payload = {"symbol": "AAPL", "legs": []}
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/sor/analyze",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        # No legs -> honest zeroed-out card (no `valid`/`legs_count` flags in the
        # frontend contract; see the "success" test above for why).
        assert body["symbol"] == "AAPL"
        assert body["legs_breakdown"] == []
        assert body["hung_leg_probability"] == 0.0
        assert body["recommended_route"] == "COB_NET_PACKAGE"

    def test_post_sor_analyze_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/sor/analyze",
                json={"legs": []},
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 9. POST /pilots/options/sor/simulate-legging
# ---------------------------------------------------------------------------


class TestOptionsSorSimulateLeggingEndpoint:
    def test_post_sor_simulate_legging_success(self):
        payload = {
            "legs": [
                {
                    "symbol": "SPY 2026-09-18 $500.00 CALL",
                    "action": "BUY",
                    "type": "CALL",
                    "strike": 500.0,
                    "bid": 5.00,
                    "ask": 5.20,
                    "delta": 0.50,
                    "gamma": 0.02,
                },
                {
                    "symbol": "SPY 2026-09-18 $510.00 CALL",
                    "action": "SELL",
                    "type": "CALL",
                    "strike": 510.0,
                    "bid": 2.10,
                    "ask": 2.15,
                    "delta": 0.30,
                    "gamma": 0.015,
                },
            ],
            "spot_price": 500.0,
            "volatility": 0.22,
            "latency_seconds": 2.0,
            "num_simulations": 500,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/sor/simulate-legging",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        # Field names from `simulate_legging_execution_for_frontend()` -- matches
        # webapp/src/api/types.ts::LeggingSimulationResponse, NOT
        # simulate_legging_execution()'s own internal `valid`/`hung_leg_probability`/
        # `distribution.percentiles`/`recommended_policy` keys. This endpoint reports
        # execution-latency risk for a fixed leg set -- it has no COB-vs-legging
        # routing recommendation of its own (that's `sor/analyze`'s job).
        assert body["num_simulations"] == 500
        assert body["latency_seconds"] == 2.0
        assert 0.0 <= body["hung_leg_rate"] <= 1.0
        assert "expected_edge_dollars" in body
        assert "edge_std_dollars" in body
        assert "worst_case_loss_dollars" in body
        assert "p95_adverse_selection" in body
        assert len(body["pnl_distribution"]) > 0
        assert len(body["latency_curve"]) > 0

    def test_post_sor_simulate_legging_empty_legs_fallback(self):
        payload = {"legs": [], "num_simulations": 100}
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/sor/simulate-legging",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        # No legs -> honest zeroed-out card, empty distribution/curve (never a
        # fabricated non-empty histogram -- CONSTRAINT #4).
        assert body["symbol"] == "MULTI"
        assert body["hung_leg_rate"] == 0.0
        assert body["pnl_distribution"] == []
        assert body["latency_curve"] == []


    def test_post_sor_simulate_legging_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/sor/simulate-legging",
                json={"legs": []},
                headers={"Authorization": "Bearer INVALID_TOKEN"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 10. GET /pilots/options/gex/profile
# ---------------------------------------------------------------------------


class TestOptionsGexProfileEndpoint:
    @patch("data.market_data.get_provider")
    def test_get_gex_profile_success(self, mock_provider_fn):
        mock_prov = MagicMock()
        mock_prov.get_latest_quote.return_value = MagicMock(price=500.0)
        mock_provider_fn.return_value = mock_prov
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/gex/profile?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["spot_price"] > 0
        assert "net_gex" in body
        assert "total_call_gex" in body
        assert "total_put_gex" in body
        assert "call_wall_strike" in body
        assert "put_wall_strike" in body
        assert "gamma_regime" in body
        assert body["gamma_regime"] in ["POSITIVE_GAMMA", "NEGATIVE_GAMMA", "PIN_RISK_HIGH"]
        assert "regime_description" in body
        assert "dealer_hedging_flow" in body
        assert "dealer_hedging_shares_per_1pct_move" in body
        assert "strikes" in body
        assert isinstance(body["strikes"], list)
        if body["strikes"]:
            stk = body["strikes"][0]
            assert "strike" in stk
            assert "call_gex" in stk
            assert "put_gex" in stk
            assert "net_gex" in stk

    def test_get_gex_profile_custom_spot_price(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/gex/profile?symbol=AAPL&spot_price=160.0",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["spot_price"] == 160.0

    def test_get_gex_profile_no_token_fail_open(self):
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/options/gex/profile?symbol=NVDA")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "NVDA"
        assert "net_gex" in body

    def test_get_gex_profile_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/gex/profile?symbol=SPY",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_get_gex_profile_missing_symbol_validation_error(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/gex/profile",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 11. POST /pilots/options/lob/simulate-queue
# ---------------------------------------------------------------------------


class TestLobSimulateQueueEndpoint:
    def test_post_lob_simulate_queue_success(self):
        payload = {
            "symbol": "SPY",
            "price_level": 500.0,
            "order_size": 10.0,
            "depth_ahead": 50.0,
            "lambda_limit": 4.0,
            "mu_cancel": 0.05,
            "theta_market": 5.0,
            "time_horizon_sec": 60.0,
            "num_simulations": 300,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/lob/simulate-queue",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["price_level"] == 500.0
        assert body["order_size"] == 10.0
        assert body["depth_ahead"] == 50.0
        assert 0.0 <= body["fill_probability"] <= 1.0
        assert "expected_wait_time" in body or "expected_fill_time_sec" in body
        percentiles = body.get("queue_progression_percentiles") or body.get("progression_percentiles") or {}
        assert "p50" in percentiles

    def test_post_lob_simulate_queue_no_token_fail_open(self):
        payload = {
            "symbol": "AAPL",
            "price_level": 150.0,
            "order_size": 5.0,
            "depth_ahead": 20.0,
            "num_simulations": 100,
        }
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.post(
                "/pilots/options/lob/simulate-queue",
                json=payload,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert 0.0 <= body["fill_probability"] <= 1.0

    def test_post_lob_simulate_queue_fails_with_wrong_token(self):
        payload = {
            "symbol": "SPY",
            "price_level": 500.0,
            "order_size": 5.0,
            "depth_ahead": 20.0,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/lob/simulate-queue",
                json=payload,
                headers={"Authorization": "Bearer BAD_TOKEN"},
            )
        assert resp.status_code == 401

    def test_post_lob_simulate_queue_missing_required_fields(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/lob/simulate-queue",
                json={"symbol": "SPY"},
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 12. GET /pilots/options/copula/pairs
# ---------------------------------------------------------------------------


class TestOptionsCopulaPairsEndpoint:
    def test_get_copula_pairs_success_query_params(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/copula/pairs?symbol_y=GLD&symbol_x=GDX",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pair"] == "GLD/GDX"
        assert body["asset_x"] == "GDX"
        assert body["asset_y"] == "GLD"
        assert body["copula_family"] in ("Clayton", "Gumbel", "Frank", "Gaussian")
        assert "tail_dependence" in body
        tail = body["tail_dependence"]
        assert "lower_tail_dependence" in tail
        assert "upper_tail_dependence" in tail
        assert "theta" in tail
        assert "kendall_tau" in tail
        assert isinstance(body["kalman_beta"], float)
        assert isinstance(body["kalman_alpha"], float)
        assert isinstance(body["ou_half_life_days"], float)
        assert body["ou_half_life_days"] > 0.0
        assert isinstance(body["spread_z_score"], float)
        assert body["signal_action"] in ("LONG_SPREAD", "SHORT_SPREAD", "EXIT", "HOLD")
        assert len(body["historical_series"]) > 0
        point = body["historical_series"][-1]
        assert "date" in point
        assert "asset_x_price" in point
        assert "asset_y_price" in point
        assert "kalman_beta" in point
        assert "spread" in point
        assert "spread_z_score" in point
        assert "upper_band_2sigma" in point
        assert "lower_band_2sigma" in point

    def test_get_copula_pairs_success_pair_param(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/copula/pairs?pair=EWA/EWC",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pair"] == "EWA/EWC"
        assert body["asset_x"] == "EWC"
        assert body["asset_y"] == "EWA"

    def test_get_copula_pairs_default_fallback(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/copula/pairs",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pair"] == "GLD/GDX"

    def test_get_copula_pairs_no_token_fail_open(self):
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/options/copula/pairs?symbol_y=AAPL&symbol_x=MSFT")
        assert resp.status_code == 200
        body = resp.json()
        assert body["asset_y"] == "AAPL"
        assert body["asset_x"] == "MSFT"

    def test_get_copula_pairs_fails_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/copula/pairs?symbol_y=GLD&symbol_x=GDX",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 13. POST /pilots/options/market-maker/simulate
# ---------------------------------------------------------------------------


class TestMarketMakerSimulateEndpoint:
    def test_post_market_maker_simulate_success(self):
        payload = {
            "symbol": "SPY",
            "spot_price": 500.0,
            "volatility": 0.20,
            "gamma": 0.1,
            "kappa": 1.5,
            "num_steps": 50,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/market-maker/simulate",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["risk_aversion_gamma"] == 0.1
        assert body["order_flow_intensity_kappa"] == 1.5
        assert body["volatility_sigma"] == 0.20
        assert body["max_inventory"] == 10
        assert "final_pnl" in body
        assert "sharpe_ratio" in body
        assert "max_drawdown" in body
        assert "total_trades" in body
        assert 0.0 <= body["fill_rate"] <= 1.0
        assert "final_inventory" in body
        assert body["avg_spread"] > 0.0
        assert len(body["steps"]) == 50
        step0 = body["steps"][0]
        assert step0["step"] == 0
        assert "mid_price" in step0
        assert "reservation_price" in step0
        assert "bid_price" in step0
        assert "ask_price" in step0
        assert step0["ask_price"] >= step0["bid_price"]
        assert "inventory" in step0
        assert "pnl" in step0

    def test_post_market_maker_simulate_alias_fields(self):
        payload = {
            "symbol": "QQQ",
            "spot_price": 450.0,
            "volatility_sigma": 0.25,
            "risk_aversion_gamma": 0.2,
            "order_flow_intensity_kappa": 2.0,
            "time_steps": 30,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/market-maker/simulate",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "QQQ"
        assert body["risk_aversion_gamma"] == 0.2
        assert body["order_flow_intensity_kappa"] == 2.0
        assert body["volatility_sigma"] == 0.25
        assert len(body["steps"]) == 30

    def test_post_market_maker_simulate_no_token_fail_open(self):
        payload = {
            "symbol": "IWM",
            "spot_price": 200.0,
            "num_steps": 20,
        }
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.post(
                "/pilots/options/market-maker/simulate",
                json=payload,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "IWM"
        assert len(body["steps"]) == 20

    def test_post_market_maker_simulate_fails_with_wrong_token(self):
        payload = {
            "symbol": "SPY",
            "spot_price": 500.0,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/market-maker/simulate",
                json=payload,
                headers={"Authorization": "Bearer INVALID_TOKEN"},
            )
        assert resp.status_code == 401

def _synthetic_ai_forecast_bars(n: int = 750, base_price: float = 150.0, seed: int = 11) -> "pd.DataFrame":
    """A real-shaped (Open/High/Low/Close/Volume), sufficiently-long synthetic
    OHLCV panel for hermetically testing get_transformer_forecast/
    post_diffusion_stress_test without touching HistoricalStore's real DB/live
    fallback (audit finding F7 fix) -- both endpoints need >=750 lookback days
    to clear their own real minimum-history/minimum-training-window gates.
    """
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=0.0003, scale=0.012, size=n)
    close = base_price * np.cumprod(1 + rets)
    idx = pd.bdate_range(end="2026-08-01", periods=n)
    return pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.005,
            "Low": close * 0.995,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )


class TestAIForecastingEndpoints:
    def test_get_transformer_forecast_success(self):
        mock_store = MagicMock()
        mock_store.get_bars.return_value = _synthetic_ai_forecast_bars(seed=11)
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=mock_store):
                resp = _client.get(
                    "/pilots/options/ai/transformer-forecast?symbol=AAPL",
                    headers={"Authorization": f"Bearer {_READ_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert len(body["forecast"]) == 4
        assert "1d" in body["forecast"]
        assert "5d" in body["forecast"]
        assert "21d" in body["forecast"]
        assert "60d" in body["forecast"]
        assert len(body["attention_heatmap"]) == 60
        mock_store.get_bars.assert_called_once_with("AAPL", lookback_days=750)

    def test_get_transformer_forecast_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/ai/transformer-forecast?symbol=AAPL",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_get_transformer_forecast_insufficient_history_returns_honest_422(self):
        mock_store = MagicMock()
        mock_store.get_bars.return_value = pd.DataFrame()
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=mock_store):
                resp = _client.get(
                    "/pilots/options/ai/transformer-forecast?symbol=ZZZZ",
                    headers={"Authorization": f"Bearer {_READ_TOKEN}"},
                )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history_for_symbol"

    def test_post_diffusion_stress_test_success(self):
        payload = {
            "symbol": "TSLA",
            "spot_price": 200.0,
            "volatility": 0.5,
            "num_paths": 100,
            "horizon": 10,
            "drift": 0.05
        }
        mock_store = MagicMock()
        mock_store.get_bars.return_value = _synthetic_ai_forecast_bars(seed=22, base_price=200.0)
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=mock_store):
                resp = _client.post(
                    "/pilots/options/ai/diffusion-stress-test",
                    json=payload,
                    headers={"Authorization": f"Bearer {_READ_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "TSLA"
        assert len(body["paths"]) == 100
        assert len(body["paths"][0]) == 10
        assert "VaR_95" in body
        assert "CVaR_95" in body
        mock_store.get_bars.assert_called_once_with("TSLA", lookback_days=750)

    def test_post_diffusion_stress_test_insufficient_history_returns_honest_422(self):
        payload = {"symbol": "ZZZZ", "spot_price": 200.0, "volatility": 0.5, "num_paths": 100, "horizon": 10, "drift": 0.05}
        mock_store = MagicMock()
        mock_store.get_bars.return_value = pd.DataFrame()
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=mock_store):
                resp = _client.post(
                    "/pilots/options/ai/diffusion-stress-test",
                    json=payload,
                    headers={"Authorization": f"Bearer {_READ_TOKEN}"},
                )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history_for_symbol"

    def test_post_diffusion_stress_test_fails_closed_with_wrong_token(self):
        payload = {
            "symbol": "TSLA",
            "spot_price": 200.0,
            "volatility": 0.5,
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/ai/diffusion-stress-test",
                json=payload,
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

class TestOptimizationEndpoints:
    def test_post_portfolio_optimize_hrp_cvar_success(self):
        payload = {
            "symbols": ["AAPL", "MSFT", "GOOGL"]
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/portfolio/optimize/hrp-cvar",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "allocations" in body
        assert "dendrogram" in body
        assert "expected_return" in body
        assert len(body["allocations"]) == 3

    def test_post_portfolio_optimize_hrp_cvar_fails_closed_with_wrong_token(self):
        payload = {
            "symbols": ["AAPL", "MSFT", "GOOGL"]
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/portfolio/optimize/hrp-cvar",
                json=payload,
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_post_execution_optimize_almgren_chriss_success(self):
        payload = {
            "symbol": "AAPL",
            "quantity": 100.0,
            "urgency": 0.5
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/execution/optimize/almgren-chriss",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "expected_trajectory" in body
        assert len(body["expected_trajectory"]) > 0

    def test_post_execution_optimize_almgren_chriss_fails_closed_with_wrong_token(self):
        payload = {
            "symbol": "AAPL",
            "quantity": 100.0
        }
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/execution/optimize/almgren-chriss",
                json=payload,
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /pilots/execution/fix/route & GET /pilots/execution/fix/venues
# ---------------------------------------------------------------------------



    def test_post_portfolio_optimize_hrp_cvar_invalid_empty_symbols(self):
        payload = {"symbols": []}
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/portfolio/optimize/hrp-cvar",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_post_execution_optimize_almgren_chriss_invalid_negative_quantity(self):
        payload = {"symbol": "AAPL", "quantity": -100.0, "urgency": 0.5}
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/execution/optimize/almgren-chriss",
                json=payload,
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422


class TestPilotsExecutionFixEndpoints:
    def test_get_execution_fix_venues_success(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/fix/venues?symbol=SPY&spot_price=500.0",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "venues" in body
        assert "supported_policies" in body
        assert "timestamp" in body
        assert len(body["venues"]) == 6
        expected_names = {"CBOE", "MIAX", "BOX", "PHLX", "ARCA", "EDGX"}
        venue_names = {v["venue"] for v in body["venues"]}
        assert venue_names == expected_names
        for v in body["venues"]:
            assert "base_latency_ms" in v
            assert "liquidity_depth" in v
            assert "taker_fee" in v
            assert "maker_fee" in v
            assert "simulated_book_depth" in v
            assert len(v["simulated_book_depth"]["bids"]) == 3
            assert len(v["simulated_book_depth"]["asks"]) == 3

    def test_get_execution_fix_venues_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/execution/fix/venues")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["venues"]) == 6

    def test_get_execution_fix_venues_fails_with_invalid_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/fix/venues",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_post_execution_fix_route_smart_sweep_success(self):
        payload = {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": 250.0,
            "limit_price": 500.0,
            "routing_policy": "SMART_SWEEP",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["side"] == "BUY"
        assert body["quantity"] == 250.0
        assert body["limit_price"] == 500.0
        assert body["routing_policy"] == "SMART_SWEEP"
        assert body["status"] == "FILLED"
        assert body["total_filled_qty"] == 250.0
        assert body["leaves_qty"] == 0.0
        assert body["weighted_avg_price"] > 0
        assert "total_net_fee" in body
        assert "avg_latency_ms" in body
        assert len(body["fills"]) > 0
        assert len(body["fix_audit_log"]) == len(body["fills"])
        # SMART_SWEEP routes to lowest taker fee first (BOX: 0.10)
        assert body["fills"][0]["venue"] == "BOX"
        assert "raw_fix" in body["fills"][0]
        assert "8=FIX.4.4" in body["fills"][0]["raw_fix"]
        assert "nbbo" in body
        assert body["nbbo"]["best_bid"] > 0
        assert body["nbbo"]["best_ask"] >= body["nbbo"]["best_bid"]

    def test_post_execution_fix_route_fastest_venue_success(self):
        payload = {
            "symbol": "QQQ",
            "side": "BUY",
            "quantity": 100.0,
            "limit_price": 450.0,
            "routing_policy": "FASTEST_VENUE",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["routing_policy"] == "FASTEST_VENUE"
        assert len(body["fills"]) > 0
        # FASTEST_VENUE routes to lowest latency tier (EDGX 0.6ms, MIAX 0.8ms base)
        assert body["fills"][0]["venue"] in {"EDGX", "MIAX"}

    def test_post_execution_fix_route_max_rebate_success(self):
        payload = {
            "symbol": "AAPL",
            "side": "SELL",
            "quantity": 150.0,
            "limit_price": 220.0,
            "routing_policy": "MAX_REBATE",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["routing_policy"] == "MAX_REBATE"
        assert len(body["fills"]) > 0
        # MAX_REBATE routes to EDGX first (maker rebate 0.40)
        assert body["fills"][0]["venue"] == "EDGX"
        assert body["total_rebates"] > 0

    def test_post_execution_fix_route_partial_fills_and_multi_venue_sweep(self):
        payload = {
            "symbol": "TSLA",
            "side": "BUY",
            "quantity": 2500.0,
            "limit_price": 250.0,
            "routing_policy": "SMART_SWEEP",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["quantity"] == 2500.0
        assert len(body["fills"]) >= 2
        total_fill_qty = sum(f["fill_qty"] for f in body["fills"])
        # Allow a tiny float-summation epsilon (IEEE 754 accumulation across
        # multiple venue fills can land a few ULPs above the exact target).
        assert total_fill_qty <= 2500.0 + 1e-9
        assert len(body["fix_audit_log"]) == len(body["fills"])

    def test_post_execution_fix_route_invalid_side(self):
        payload = {
            "symbol": "SPY",
            "side": "INVALID_SIDE",
            "quantity": 100.0,
            "limit_price": 500.0,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_post_execution_fix_route_invalid_policy(self):
        payload = {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": 100.0,
            "limit_price": 500.0,
            "routing_policy": "UNKNOWN_POLICY",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_post_execution_fix_route_invalid_quantity(self):
        payload = {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": -50.0,
            "limit_price": 500.0,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_post_execution_fix_route_fails_closed_with_wrong_token(self):
        payload = {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": 100.0,
            "limit_price": 500.0,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_post_execution_fix_route_fails_closed_without_token(self):
        payload = {
            "symbol": "SPY",
            "side": "BUY",
            "quantity": 50.0,
            "limit_price": 500.0,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/fix/route",
                json=payload,
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /pilots/ai/research/synthesize
# ---------------------------------------------------------------------------


class TestPilotsAIResearchSynthesize:
    def test_synthesize_success(self):
        payload = {
            "prompt": "Construct a trend following strategy using 20-day and 50-day moving average crossovers.",
            "strategy_type": "hypothesis",
            "target_asset_class": "equities",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/synthesize",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "class " in body["code"]
        assert body["validation_passed"] is True
        assert body["synthesis_mode"] in {"hypothesis", "template"}
        assert body["target_asset_class"] == "equities"
        assert isinstance(body["metadata"], dict)

    def test_synthesize_empty_prompt_400(self):
        payload = {"prompt": "   "}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/synthesize",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 400
        assert "Prompt cannot be empty" in resp.json()["detail"]

    def test_synthesize_missing_prompt_422(self):
        payload = {"strategy_type": "momentum"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/synthesize",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_synthesize_wrong_token_401(self):
        payload = {"prompt": "A simple mean-reversion signal."}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/synthesize",
                json=payload,
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_synthesize_fail_closed_without_token(self):
        payload = {"prompt": "A simple RSI oscillator momentum signal."}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/synthesize",
                json=payload,
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /pilots/ai/research/backtest
# ---------------------------------------------------------------------------


class TestPilotsAIResearchBacktest:
    _SAMPLE_STRATEGY = """
import numpy as np
import pandas as pd

def strategy(df: pd.DataFrame) -> pd.Series:
    close = df['Close']
    ma20 = close.rolling(20, min_periods=1).mean()
    ma50 = close.rolling(50, min_periods=1).mean()
    pos = pd.Series(0.0, index=df.index)
    pos[close > ma20] = 1.0
    pos[close < ma50] = -1.0
    return pos
"""

    @staticmethod
    def _make_bars_store(n_rows: int):
        """A minimal fake HistoricalStore whose get_bars() returns a
        controlled, deterministic OHLCV DataFrame of exactly n_rows rows --
        used to isolate this endpoint's <50-row branch logic from whatever
        market data happens (or doesn't happen) to already be cached in
        whichever environment the test suite runs in."""
        idx = pd.date_range("2024-01-02", periods=n_rows, freq="B")
        rng = np.random.default_rng(42)
        close = 400.0 + np.cumsum(rng.normal(0.05, 1.5, size=n_rows))

        class _Store:
            def get_bars(self, symbol, *args, **kwargs):
                return pd.DataFrame(
                    {
                        "Open": close,
                        "High": close * 1.005,
                        "Low": close * 0.995,
                        "Close": close,
                        "Volume": np.full(n_rows, 50_000_000),
                    },
                    index=idx,
                )

        return _Store()

    def test_backtest_success(self):
        """Isolated: mocks HistoricalStore to return a controlled >=50-row
        fixture rather than depending on ambient real market data being
        cached wherever this test happens to run."""
        payload = {
            "code": self._SAMPLE_STRATEGY,
            "symbol": "SPY",
            "cost_bps": 5.0,
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=self._make_bars_store(120)):
                resp = _client.post(
                    "/pilots/ai/research/backtest",
                    json=payload,
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert "is_deployable" in body
        assert "sharpe_ratio" in body
        assert body["data_source"] == "real_historical_bars"
        assert body["is_synthetic_data"] is False
        assert "pbo" in body
        assert "dsr" in body
        assert "gate_evaluations" in body
        assert "pbo_gate" in body["gate_evaluations"]
        assert "dsr_gate" in body["gate_evaluations"]
        assert "sharpe_gate" in body["gate_evaluations"]
        assert "max_dd_gate" in body["gate_evaluations"]
        assert body["strategy_id"] == "SPY"

    def test_backtest_falls_back_to_synthetic_data_when_bars_insufficient(self):
        """Isolated, controlled version of the <50-row synthetic-fallback
        path -- this test's outcome is deterministic regardless of what
        market data is or isn't cached wherever it runs."""
        payload = {"code": self._SAMPLE_STRATEGY, "symbol": "SPY", "cost_bps": 5.0}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=self._make_bars_store(10)):
                resp = _client.post(
                    "/pilots/ai/research/backtest",
                    json=payload,
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_source"] == "synthetic_demo_data"
        assert body["is_synthetic_data"] is True
        assert body["is_deployable"] is False

    def test_backtest_falls_back_to_synthetic_data_when_bars_fetch_raises(self):
        """Same fallback path, triggered by a raising HistoricalStore
        instead of a too-short DataFrame -- api/pilots_api.py's own
        try/except around HistoricalStore().get_bars(sym) converts either
        failure mode into the same <50-row branch, so both must be covered
        independently rather than assuming one implies the other."""
        class _BoomStore:
            def get_bars(self, symbol, *args, **kwargs):
                raise RuntimeError("simulated DB outage")

        payload = {"code": self._SAMPLE_STRATEGY, "symbol": "SPY", "cost_bps": 5.0}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            with patch.object(pilots_api, "HistoricalStore", return_value=_BoomStore()):
                resp = _client.post(
                    "/pilots/ai/research/backtest",
                    json=payload,
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_source"] == "synthetic_demo_data"
        assert body["is_synthetic_data"] is True
        assert body["is_deployable"] is False

    def test_backtest_empty_code_400(self):
        payload = {"code": "   ", "symbol": "SPY"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/backtest",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 400
        assert "Strategy code cannot be empty" in resp.json()["detail"]

    def test_backtest_missing_code_422(self):
        payload = {"code": "def strategy(df): pass", "cost_bps": -10.0}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/backtest",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_backtest_unsafe_ast_code(self):
        payload = {
            "code": "import os\ndef strategy(df):\n    os.system('echo test')\n    return df['Close']",
            "symbol": "SPY",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/backtest",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_deployable"] is False
        assert body["error"] is not None or len(body["failure_reasons"]) > 0

    def test_backtest_wrong_token_401(self):
        payload = {"code": self._SAMPLE_STRATEGY, "symbol": "SPY"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/backtest",
                json=payload,
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_backtest_fail_closed_without_token(self):
        payload = {"code": self._SAMPLE_STRATEGY, "symbol": "SPY"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/ai/research/backtest",
                json=payload,
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /pilots/options/vol-surface/3d-mesh
# ---------------------------------------------------------------------------


class TestPilotsOptionsVolSurface3DMesh:
    def test_vol_surface_mesh_default_symbol(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface/3d-mesh",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["spot_price"] > 0
        assert isinstance(body["mesh"], list)
        assert len(body["mesh"]) > 0
        point = body["mesh"][0]
        assert "x" in point and "y" in point and "z" in point
        assert "strike" in point and "dte" in point and "iv" in point
        assert isinstance(body["grid"], list)
        assert "smiles" in body
        assert "term_structure" in body

    def test_vol_surface_mesh_custom_symbol(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface/3d-mesh?symbol=AAPL",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert len(body["mesh"]) > 0

    def test_vol_surface_mesh_empty_symbol_422(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface/3d-mesh?symbol=",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code in {400, 422}

    def test_vol_surface_mesh_wrong_token_401(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vol-surface/3d-mesh",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_vol_surface_mesh_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/options/vol-surface/3d-mesh")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# GET /pilots/execution/brokers/status
# ---------------------------------------------------------------------------


class TestPilotsExecutionBrokersStatus:
    def test_brokers_status_success(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/brokers/status",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "active_broker_id" in body
        assert "priority_hierarchy" in body
        assert "brokers" in body
        assert isinstance(body["brokers"], dict)
        assert "alpaca" in body["brokers"]
        assert "total_orders_routed" in body
        assert "total_failovers" in body

    def test_brokers_status_wrong_token_401(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/brokers/status",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_brokers_status_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/execution/brokers/status")
        assert resp.status_code == 200
        assert "brokers" in resp.json()


# ---------------------------------------------------------------------------
# POST /pilots/execution/brokers/failover
# ---------------------------------------------------------------------------


class TestPilotsExecutionBrokersFailover:
    def test_brokers_failover_success(self):
        payload = {
            "target_broker": "interactive_brokers",
            "reason": "Primary broker latency degradation",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/brokers/failover",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["active_broker"] == "interactive_brokers"
        assert body["manual_override"] == "interactive_brokers"
        assert body["reason"] == "Primary broker latency degradation"

    def test_brokers_failover_unregistered_broker_400(self):
        payload = {
            "target_broker": "nonexistent_fake_broker",
            "reason": "Test unknown",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/brokers/failover",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 400
        assert "not registered in gateway" in resp.json()["detail"]

    def test_brokers_failover_missing_target_422(self):
        payload = {"reason": "Missing target"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/brokers/failover",
                json=payload,
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_brokers_failover_wrong_token_401(self):
        payload = {"target_broker": "tradier"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/brokers/failover",
                json=payload,
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_brokers_failover_fail_closed_without_token(self):
        payload = {"target_broker": "tradier"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN):
            resp = _client.post(
                "/pilots/execution/brokers/failover",
                json=payload,
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /pilots/execution/sec-606/report
# ---------------------------------------------------------------------------


class TestPilotsExecutionSec606Report:
    def test_sec_606_report_default_params(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/sec-606/report",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "header" in body
        assert "summary" in body
        assert "order_category_breakdown" in body
        assert "venue_breakdown" in body
        assert body["header"]["year"] == 2026
        assert body["header"]["quarter"] == 1

    def test_sec_606_report_custom_year_quarter_is_option(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/sec-606/report?year=2026&quarter=2&is_option=true",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["header"]["year"] == 2026
        assert body["header"]["quarter"] == 2
        assert body["header"]["is_option"] is True

    def test_sec_606_report_invalid_quarter_422(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/sec-606/report?quarter=5",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code in {400, 422}

    def test_sec_606_report_wrong_token_401(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/execution/sec-606/report",
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401

    def test_sec_606_report_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=None):
            resp = _client.get("/pilots/execution/sec-606/report")
        assert resp.status_code == 200
        assert "header" in resp.json()
        assert "summary" in resp.json()




class TestPostOptionsZeroDteManageExits:
    """POST /pilots/options/0dte/manage-exits -- closes audit finding F5
    (.claude/giant_master_plan_audit.md): evaluate_0dte_exits/execute_0dte_exits
    were correctly implemented and tested but had no live-callable path.
    """

    def test_fails_closed_when_writes_disabled(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
            resp = _client.post(
                "/pilots/options/0dte/manage-exits",
                json={},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            resp = _client.post(
                "/pilots/options/0dte/manage-exits",
                json={},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_calls_through_to_manage_0dte_exits_with_expected_kwargs(self):
        mock_result = {
            "signals": [], "executed_count": 0, "failed_count": 0,
            "executed": [], "failed": [], "reason": "no_0dte_positions_open",
        }
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            with patch("pilots.zero_dte_engine.manage_0dte_exits", return_value=mock_result) as mock_manage:
                resp = _client.post(
                    "/pilots/options/0dte/manage-exits",
                    json={"dry_run": True, "profit_target_pct": 0.5, "stop_loss_pct": 0.25, "hard_exit_time": "15:30"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json() == mock_result
        mock_manage.assert_called_once_with(
            dry_run=True, profit_target_pct=0.5, stop_loss_pct=0.25, hard_exit_time="15:30",
        )

    def test_no_body_uses_defaults(self):
        mock_result = {"signals": [], "executed_count": 0, "failed_count": 0, "executed": [], "failed": [], "reason": "no_0dte_positions_open"}
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            with patch("pilots.zero_dte_engine.manage_0dte_exits", return_value=mock_result) as mock_manage:
                resp = _client.post(
                    "/pilots/options/0dte/manage-exits",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        mock_manage.assert_called_once_with(
            dry_run=False, profit_target_pct=None, stop_loss_pct=None, hard_exit_time=None,
        )

    def test_exception_dead_letters_instead_of_leaking(self):
        with mock_patch_settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
            with patch("pilots.zero_dte_engine.manage_0dte_exits", side_effect=RuntimeError("boom")):
                resp = _client.post(
                    "/pilots/options/0dte/manage-exits",
                    json={},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "boom" not in body["error"]
