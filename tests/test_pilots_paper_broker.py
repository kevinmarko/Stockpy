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





