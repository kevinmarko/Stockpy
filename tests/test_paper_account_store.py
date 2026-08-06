import os
import pytest
from datetime import datetime, timezone
from data.paper_account_store import PaperAccountStore
from execution.broker_base import OrderResult, OrderStatus, OrderSide

# We will use an in-memory SQLite DB for tests
TEST_DB_URL = "sqlite:///:memory:"

@pytest.fixture
def store():
    # Use write mode to create tables in memory
    s = PaperAccountStore(db_url=TEST_DB_URL)
    yield s
    
@pytest.fixture
def readonly_store(tmp_path):
    # Use a non-existent file in a temporary directory
    db_file = tmp_path / "missing.db"
    s = PaperAccountStore(db_url=f"sqlite:///{db_file}", readonly=True)
    yield s

def test_paper_account_creation(store):
    account = store.get_account()
    # By default it's 100000.0 but we just check it's > 0
    assert account.cash > 0
    assert account.equity == account.cash

def test_apply_fill_buy_and_sell(store):
    # Buy 10 AAPL at 150
    initial_cash = store.get_account().cash
    success = store.apply_fill("client_order_1", "AAPL", "buy", 10.0, 150.0, 5.0)
    assert success is True
    
    account = store.get_account()
    assert account.cash == initial_cash - (1500.0 + 5.0)
    
    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].qty == 10.0
    assert positions[0].avg_entry_price == 150.0
    
    # Sell 5 AAPL at 160
    success = store.apply_fill("client_order_2", "AAPL", "sell", 5.0, 160.0, 5.0)
    assert success is True
    
    account = store.get_account()
    assert account.cash == initial_cash - 1505.0 + (800.0 - 5.0)
    
    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].qty == 5.0
    assert positions[0].avg_entry_price == 150.0

def test_insufficient_funds(store):
    success = store.apply_fill("client_order_3", "TSLA", "buy", 10000.0, 1000.0, 0.0)
    assert success is False
    # Check rejection order is recorded
    orders = store.get_orders()
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.REJECTED

def test_insufficient_inventory(store):
    success = store.apply_fill("client_order_4", "AAPL", "sell", 100.0, 150.0, 0.0)
    assert success is False
    orders = store.get_orders()
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.REJECTED

def test_readonly_degradation(readonly_store):
    # Should not crash, just return empty/0
    account = readonly_store.get_account()
    assert account.cash == 0.0
    
    pos = readonly_store.get_open_positions()
    assert len(pos) == 0
    
    orders = readonly_store.get_orders()
    assert len(orders) == 0
