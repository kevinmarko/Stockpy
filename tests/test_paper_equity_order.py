"""
tests/test_paper_equity_order.py
================================
Tests for pilots/paper_equity_order.py (manual equity paper orders) and the
POST /pilots/paper-broker/order endpoint that the Quick Trade ticket uses.

The module exists so equity paper trading has no dependency on the options
desk; the import-isolation test below pins that.
"""

import subprocess
import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api
from data.paper_account_store import PaperAccountStore
from pilots.paper_equity_order import estimate_commission, execute_equity_order
from settings import settings

_client = TestClient(pilots_api.app, client=("127.0.0.1", 54125))
_CMD_TOKEN = "equity-order-cmd-tok"


@contextmanager
def _settings(**kwargs):
    with ExitStack() as stack:
        for key, value in kwargs.items():
            stack.enter_context(patch.object(settings, key, value))
        yield


# ---------------------------------------------------------------------------
# execute_equity_order
# ---------------------------------------------------------------------------


def test_live_mode_rejected():
    res = execute_equity_order("AAPL", is_live=True)
    assert res["ok"] is False
    assert "Advisory-Only" in res["message"]


def test_rejects_bad_side_and_blank_symbol():
    assert execute_equity_order("AAPL", side="short")["ok"] is False
    assert execute_equity_order("  ", side="buy")["ok"] is False


def test_commission_min_and_per_share():
    assert estimate_commission(10) == 1.0
    assert estimate_commission(1000) == 5.0


@patch("pilots.paper_equity_order.get_current_price", return_value=0.0)
def test_missing_quote_rejects_instead_of_fabricating(_price):
    res = execute_equity_order("AAPL", side="buy", quantity=1)
    assert res["ok"] is False
    assert "No live quote" in res["message"]


def test_buy_then_sell_round_trip_against_real_store():
    """Real PaperAccountStore (conftest isolates it to a temp DB)."""
    store = PaperAccountStore()
    cash0 = store.get_account().cash

    buy = execute_equity_order("AAPL", side="buy", dollar_amount=1000.0, limit_price=200.0)
    assert buy["ok"] is True, buy
    assert buy["order_id"].startswith("eq_ord_")
    positions = {p.symbol: p for p in PaperAccountStore().get_open_positions()}
    assert positions["AAPL"].qty == pytest.approx(5.0)
    assert positions["AAPL"].strategy_id == "Manual Trade"
    assert PaperAccountStore().get_account().cash == pytest.approx(cash0 - 1000.0 - 1.0)

    sell = execute_equity_order("AAPL", side="sell", quantity=5.0, limit_price=210.0)
    assert sell["ok"] is True, sell
    assert "AAPL" not in {p.symbol for p in PaperAccountStore().get_open_positions()}
    assert PaperAccountStore().get_account().cash == pytest.approx(cash0 - 1001.0 + 1050.0 - 1.0)


def test_sell_without_inventory_is_rejected_not_shorted():
    res = execute_equity_order("MSFT", side="sell", quantity=3.0, limit_price=400.0)
    assert res["ok"] is False
    assert "Insufficient" in res["message"]
    assert "MSFT" not in {p.symbol for p in PaperAccountStore().get_open_positions()}


def test_module_does_not_import_options_desk():
    code = (
        "import sys; sys.modules['pilots.options_risk'] = None; "
        "sys.modules['pilots.paper_broker_options_order'] = None; "
        "import pilots.paper_equity_order, pilots.paper_broker, execution.fmp_paper_broker; print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")


# ---------------------------------------------------------------------------
# POST /pilots/paper-broker/order
# ---------------------------------------------------------------------------


def test_endpoint_fills_with_token_and_writes_enabled():
    with _settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
        resp = _client.post(
            "/pilots/paper-broker/order",
            json={"symbol": "agnc", "side": "buy", "dollar_amount": 500.0, "limit_price": 10.0},
            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, body
    assert "50.00 shares of AGNC" in body["message"]


def test_endpoint_fails_closed_when_writes_disabled():
    with _settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=False):
        resp = _client.post(
            "/pilots/paper-broker/order",
            json={"symbol": "AGNC", "dollar_amount": 500.0, "limit_price": 10.0},
            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
        )
    assert resp.status_code in (403, 503)


def test_endpoint_requires_command_token():
    with _settings(FOLLOW_API_TOKEN=_CMD_TOKEN, PAPER_BROKER_WRITES_ENABLED=True):
        resp = _client.post(
            "/pilots/paper-broker/order",
            json={"symbol": "AGNC", "dollar_amount": 500.0, "limit_price": 10.0},
        )
    assert resp.status_code in (401, 403)
