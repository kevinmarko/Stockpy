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
        mock_vol.return_value = {
            "symbol": "SPY",
            "spot_price": 500.0,
            "base_iv": 0.22,
            "surface": [
                {"strike": 500.0, "moneyness": 1.0, "dte": 30, "expiration": "2026-09-18", "iv": 0.22, "delta": 0.50, "option_type": "call"}
            ],
            "term_structure": [
                {"dte": 30, "expiration": "2026-09-18", "atm_iv": 0.22}
            ],
            "skew_25d": {
                "put_iv": 0.24,
                "call_iv": 0.20,
                "skew": 0.04,
            },
            "vrp_cone": [
                {"window_days": 30, "realized_vol": 0.18, "implied_vol": 0.22, "vrp": 0.04}
            ],
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
        assert len(body["surface"]) == 1
        assert len(body["term_structure"]) == 1
        assert body["skew_25d"]["skew"] == 0.04
        assert len(body["vrp_cone"]) == 1

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
        mock_get_candidates.return_value = [
            {
                "symbol": "NVDA",
                "earnings_date": "2026-08-20",
                "earnings_timing": "AMC",
                "days_to_earnings": 2,
                "spot_price": 125.0,
                "atm_iv": 0.65,
                "expected_move_dollar": 11.20,
                "expected_move_pct": 0.0896,
                "historical_median_move_pct": 0.055,
                "crush_edge_ratio": 1.63,
                "qualifies_edge": True,
                "recommended_strategy": "Iron Condor",
                "strikes": {
                    "long_put": 110.0,
                    "short_put": 114.0,
                    "short_call": 136.0,
                    "long_call": 140.0,
                },
                "estimated_credit": 1.40,
                "max_loss": 2.60,
                "estimated_roi_pct": 53.8,
                "historical_moves": [],
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
        assert body["candidates"][0]["symbol"] == "NVDA"
        assert body["candidates"][0]["crush_edge_ratio"] == 1.63
        assert body["candidates"][0]["qualifies_edge"] is True

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
                json={"symbol": "NVDA", "is_live": True},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "Advisory-Only" in body["message"]


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

    def test_get_unusual_flow_fails_closed_with_wrong_token(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/flow/unusual",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    @patch("pilots.unusual_options_flow.get_flow_sentiment")
    def test_get_flow_sentiment_success(self, mock_sentiment):
        mock_sentiment.return_value = {
            "symbol": "NVDA",
            "sentiment_score": 0.72,
            "sentiment_label": "VERY BULLISH",
            "call_volume": 45000,
            "put_volume": 12000,
            "call_put_ratio": 3.75,
            "total_bullish_notional": 18500000.0,
            "total_bearish_notional": 3000000.0,
            "total_notional": 21500000.0,
            "top_active_strikes": [{"strike": 130.0, "volume": 15000}],
            "record_count": 8,
            "as_of": "2026-08-14T15:00:00Z",
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
        assert body["sentiment_label"] == "VERY BULLISH"
        assert body["call_put_ratio"] == 3.75
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
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/har-rv?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "forecast_annualized_vol" in body
        assert body["forecast_annualized_vol"] is not None and body["forecast_annualized_vol"] > 0
        assert "forecast_rv_1d" in body
        assert "forecast_rv_5d" in body
        assert "forecast_rv_22d" in body
        assert "model_fit" in body
        fit = body["model_fit"]
        assert "beta_0" in fit and "beta_d" in fit and "beta_w" in fit and "beta_m" in fit
        assert fit["beta_d"] >= 0 and fit["beta_w"] >= 0 and fit["beta_m"] >= 0

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
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/forecast/mispricing?symbol=SPY",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "spot_price" in body
        assert "fair_atm_iv" in body
        assert "market_atm_iv" in body
        assert "iv_mispricing_spread" in body
        assert "regime_bias" in body
        assert "rich_candidates" in body
        assert "cheap_candidates" in body
        assert "strike_mispricings" in body
        assert len(body["strike_mispricings"]) > 0

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
    def test_post_gamma_scalp_simulate_default(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.post(
                "/pilots/options/gamma-scalp/simulate",
                json={},
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "rebalance_count" in body
        assert "stock_pnl" in body
        assert "option_pnl" in body
        assert "total_pnl" in body
        assert "attribution" in body
        attr = body["attribution"]
        assert "gamma_rent" in attr
        assert "theta_decay" in attr
        assert "transaction_costs" in attr
        assert "trades" in body
        assert "path_history" in body

    def test_post_gamma_scalp_simulate_custom_path(self):
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
        assert body["success"] is True
        assert body["symbol"] == "SPY"
        assert body["rebalance_count"] >= 1
        assert len(body["trades"]) >= 1
        assert len(body["path_history"]) == 6

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
        assert resp.json()["success"] is True


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
        assert "basket" in opp
        basket = opp["basket"]
        assert "index_symbol" in basket
        assert "constituent_symbols" in basket
        assert "basket_vega" in basket
        assert "vega_neutrality_ratio" in basket

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
        assert "spot" in body
        assert "signal" in body
        assert "opening_range" in body
        assert "high" in body["opening_range"]
        assert "low" in body["opening_range"]
        assert "squeeze" in body
        assert "risk_parameters" in body
        assert body["risk_parameters"]["profit_target_pct"] == 0.75
        assert body["risk_parameters"]["stop_loss_pct"] == 0.30

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
    def test_get_vpin_metrics_success(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vpin/metrics?symbol=SPY&num_buckets=20",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert "vpin" in body
        assert 0.0 <= body["vpin"] <= 1.0
        assert body["toxicity_regime"] in ["LOW", "MODERATE", "HIGH_TOXICITY"]
        assert isinstance(body["is_toxic"], bool)
        assert "bucket_history" in body
        assert len(body["bucket_history"]) > 0
        assert "recommended_spread_concession" in body
        assert "sample_time" in body

    def test_get_vpin_metrics_missing_symbol_422(self):
        with mock_patch_settings(STATE_API_TOKEN=_READ_TOKEN):
            resp = _client.get(
                "/pilots/options/vpin/metrics",
                headers={"Authorization": f"Bearer {_READ_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_get_vpin_metrics_fail_open_without_token(self):
        with mock_patch_settings(STATE_API_TOKEN=""):
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
        assert body["valid"] is True
        assert body["symbol"] == "SPY"
        assert body["legs_count"] == 2
        assert "cob_pricing" in body
        assert "synthetic_legging" in body
        assert body["recommended_policy"] in ["COB_NET_PACKAGE", "LEG_PASSIVE_FIRST", "SPLIT_DIRECT"]
        assert "policy_rationale" in body
        assert "policies_comparison" in body

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
        assert body["valid"] is False
        assert body["legs_count"] == 0
        assert body["recommended_policy"] == "COB_NET_PACKAGE"

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
        assert body["valid"] is True
        assert body["num_simulations"] == 500
        assert body["latency_seconds"] == 2.0
        assert 0.0 <= body["hung_leg_probability"] <= 1.0
        assert "expected_slippage" in body
        assert "expected_net_savings" in body
        assert "distribution" in body
        assert "percentiles" in body["distribution"]
        assert body["recommended_policy"] in ["COB_NET_PACKAGE", "LEG_PASSIVE_FIRST", "SPLIT_DIRECT"]

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
        assert body["valid"] is False
        assert body["hung_leg_probability"] == 0.0
        assert body["recommended_policy"] == "COB_NET_PACKAGE"


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
        assert body["gamma_regime"] in ["POSITIVE_GAMMA", "NEGATIVE_GAMMA", "NEUTRAL_GAMMA"]
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



