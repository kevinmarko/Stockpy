import pytest
from unittest.mock import patch, MagicMock
from pilots.paper_broker import get_account, get_positions, get_orders

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
