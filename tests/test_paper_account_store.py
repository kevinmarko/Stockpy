from unittest.mock import patch

import pytest
from data.paper_account_store import PaperAccountStore
from execution.broker_base import OrderStatus

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
    # settings.FMP_PAPER_STARTING_CASH default (see settings.py)
    assert account.cash == 100000.0
    assert account.equity == account.cash

def test_apply_fill_buy_and_sell(store):
    # Mocked so this test never depends on real network reachability --
    # get_account()/get_open_positions() call fmp_client.batch_quote to
    # mark open positions to market.
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
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

def test_sell_full_position_removes_it(store):
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        store.apply_fill("client_order_5", "AAPL", "buy", 10.0, 150.0, 0.0)
        success = store.apply_fill("client_order_6", "AAPL", "sell", 10.0, 150.0, 0.0)
        assert success is True
        assert store.get_open_positions() == []

def test_insufficient_funds(store):
    success = store.apply_fill("client_order_3", "TSLA", "buy", 10000.0, 1000.0, 0.0)
    assert success is False
    # Check rejection order is recorded
    orders = store.get_orders()
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.REJECTED

def test_sell_full_position_with_float_drift_succeeds(store):
    """A full-position sell where pos.qty has drifted to
    12.499999999999998 (float noise) for a requested qty=12.5 must succeed,
    not be wrongly rejected by an exact `<` comparison (Finding 28)."""
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        # Buy in three fractional chunks so the summed qty carries the same
        # kind of float noise a real fill sequence would produce.
        store.apply_fill("drift_buy_1", "AAPL", "buy", 4.166666666666666, 150.0, 0.0)
        store.apply_fill("drift_buy_2", "AAPL", "buy", 4.166666666666666, 150.0, 0.0)
        store.apply_fill("drift_buy_3", "AAPL", "buy", 4.166666666666666, 150.0, 0.0)

        success = store.apply_fill("drift_sell", "AAPL", "sell", 12.5, 150.0, 0.0)
        assert success is True
        assert store.get_open_positions() == []

        orders = store.get_orders()
        sell_order = next(o for o in orders if o.client_order_id == "drift_sell")
        assert sell_order.status == OrderStatus.FILLED


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

def test_reset_account_readonly():
    from data.paper_account_store import PaperAccountStore
    store = PaperAccountStore(readonly=True)
    with pytest.raises(RuntimeError, match="Cannot reset account in readonly mode"):
        store.reset_account()

def test_reset_account_clears_data(tmp_path):
    import os
    from settings import settings
    from data.paper_account_store import PaperAccountStore
    db_path = tmp_path / "test_reset.db"
    store = PaperAccountStore(f"sqlite:///{db_path}")
    
    # Needs to ensure table exists and can apply fill
    store.apply_fill("123", "AAPL", "buy", 10, 150.0, 0.0)
    
    # Reset
    store.reset_account()
    
    assert len(store.get_open_positions()) == 0
    assert len(store.get_orders()) == 0
    account = store.get_account()
    assert account.cash == settings.FMP_PAPER_STARTING_CASH
