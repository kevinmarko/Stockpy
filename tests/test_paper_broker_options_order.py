"""
tests/test_paper_broker_options_order.py
========================================
Tests for pilots/paper_broker_options_order.py paper execution.
"""

from unittest.mock import patch, MagicMock
from pilots.paper_broker_options_order import execute_paper_order


def test_live_mode_rejected_in_advisory_mode():
    res = execute_paper_order("AAPL", is_live=True)
    assert res["ok"] is False
    assert "Advisory-Only" in res["message"]


@patch("pilots.paper_broker_options_order.PaperAccountStore")
def test_stock_order_by_dollar_amount_fills(mock_store_cls):
    mock_store = mock_store_cls.return_value
    mock_store.apply_fill.return_value = True

    res = execute_paper_order(
        "AAPL",
        asset_type="stock",
        side="buy",
        dollar_amount=1000.0,
        limit_price=200.0,
    )
    assert res["ok"] is True
    assert "5.00 shares" in res["message"]
    mock_store.apply_fill.assert_called_once()
    _, kwargs = mock_store.apply_fill.call_args
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["qty"] == 5.0
    assert kwargs["fill_price"] == 200.0


@patch("pilots.paper_broker_options_order.PaperAccountStore")
def test_option_order_single_leg_fills(mock_store_cls):
    mock_store = mock_store_cls.return_value
    mock_store.apply_fill.return_value = True

    legs = [{
        "contract": {"strike": 150.0, "ask": 2.50, "bid": 2.40, "lastPrice": 2.45},
        "type": "call",
        "action": "Buy"
    }]

    res = execute_paper_order(
        "AAPL",
        asset_type="option",
        expiration="2026-09-18",
        legs=legs,
        quantity=3,
    )
    assert res["ok"] is True
    assert "3 contract(s)" in res["message"]
    mock_store.apply_fill.assert_called_once()
    _, kwargs = mock_store.apply_fill.call_args
    assert "AAPL 2026-09-18 $150.00 CALL" in kwargs["symbol"]
    assert kwargs["qty"] == 3.0
    assert kwargs["fill_price"] == 250.0  # $2.50 * 100
    assert kwargs["commission_and_fees"] == 3 * 0.65


@patch("pilots.paper_broker_options_order.PaperAccountStore")
def test_insufficient_funds_rejection(mock_store_cls):
    mock_store = mock_store_cls.return_value
    mock_store.apply_fill.return_value = False

    res = execute_paper_order(
        "AAPL",
        asset_type="stock",
        side="buy",
        quantity=100,
        limit_price=200.0,
    )
    assert res["ok"] is False
    assert "Insufficient funds" in res["message"]
