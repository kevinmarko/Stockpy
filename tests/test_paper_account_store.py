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
    assert len(orders) > 0
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
    assert len(orders) > 0
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


def test_reset_account_with_custom_starting_cash(tmp_path):
    from settings import settings
    from data.paper_account_store import PaperAccountStore
    db_path = tmp_path / "test_reset_custom_cash.db"
    store = PaperAccountStore(f"sqlite:///{db_path}")

    store.apply_fill("custom_cash_1", "AAPL", "buy", 10, 150.0, 0.0)

    # Reset with an explicit override -- must NOT fall back to
    # settings.FMP_PAPER_STARTING_CASH.
    custom_cash = 25000.0
    store.reset_account(starting_cash=custom_cash)

    assert len(store.get_open_positions()) == 0
    assert len(store.get_orders()) == 0
    account = store.get_account()
    assert account.cash == custom_cash
    assert account.cash != settings.FMP_PAPER_STARTING_CASH

    # Reset again with no argument -- must fall back to the default.
    store.apply_fill("custom_cash_2", "MSFT", "buy", 5, 300.0, 0.0)
    store.reset_account()

    assert len(store.get_open_positions()) == 0
    assert len(store.get_orders()) == 0
    account = store.get_account()
    assert account.cash == settings.FMP_PAPER_STARTING_CASH


def test_apply_multi_leg_debit_spread_fill(store):
    """Multi-leg debit spread fills atomically, deducting net debit + commission,
    and creating long and short leg positions."""
    initial_cash = store.get_account().cash

    legs = [
        {"symbol": "AAPL 2026-09-18 $150.00 CALL", "side": "buy", "qty": 2.0, "fill_price": 250.0},
        {"symbol": "AAPL 2026-09-18 $155.00 CALL", "side": "sell", "qty": 2.0, "fill_price": 100.0},
    ]
    # Net debit: (2.50 - 1.00) * 100 * 2 = $300.00 debit. Commission: 0.65 * 2 * 2 = $2.60
    commission = 2.60
    net_debit = 300.0
    net_cash_impact = -(net_debit + commission)

    success = store.apply_multi_leg_fill(
        client_order_id="multi_order_1",
        symbol="AAPL",
        strategy_name="Bull Call Spread",
        contracts=2,
        legs=legs,
        net_cash_impact=net_cash_impact,
        commission_and_fees=commission,
    )
    assert success is True

    acc = store.get_account()
    assert acc.cash == initial_cash + net_cash_impact

    positions = store.get_open_positions()
    assert len(positions) == 2

    long_pos = next(p for p in positions if "$150.00" in p.symbol)
    short_pos = next(p for p in positions if "$155.00" in p.symbol)

    assert long_pos.qty == 2.0
    assert short_pos.qty == -2.0

    # Orders check: parent + 2 legs recorded
    orders = store.get_orders()
    assert len(orders) == 3
    parent = next(o for o in orders if o.client_order_id == "multi_order_1")
    assert parent.status == OrderStatus.FILLED


def test_apply_multi_leg_credit_spread_fill(store):
    """Multi-leg credit spread fills atomically, adding net credit - commission,
    and creating short and long leg positions."""
    initial_cash = store.get_account().cash

    legs = [
        {"symbol": "AAPL 2026-09-18 $145.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 200.0},
        {"symbol": "AAPL 2026-09-18 $140.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 80.0},
    ]
    # Net credit: (2.00 - 0.80) * 100 * 1 = $120.00 credit. Commission: 0.65 * 1 * 2 = $1.30
    commission = 1.30
    net_credit = 120.0
    net_cash_impact = net_credit - commission
    # Max risk collateral: (145 - 140 - 1.20) * 100 = $380.00
    collateral = 380.0

    success = store.apply_multi_leg_fill(
        client_order_id="credit_order_1",
        symbol="AAPL",
        strategy_name="Bull Put Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=net_cash_impact,
        commission_and_fees=commission,
        collateral_required=collateral,
    )
    assert success is True

    acc = store.get_account()
    assert acc.cash == initial_cash + net_cash_impact

    positions = store.get_open_positions()
    assert len(positions) == 2
    short_put = next(p for p in positions if "$145.00" in p.symbol)
    assert short_put.qty == -1.0


def test_apply_multi_leg_insufficient_cash(store):
    """Order is rejected if account cash is insufficient for the debit or collateral."""
    legs = [
        {"symbol": "AAPL 2026-09-18 $150.00 CALL", "side": "buy", "qty": 1000.0, "fill_price": 500.0},
    ]
    success = store.apply_multi_leg_fill(
        client_order_id="too_big_order",
        symbol="AAPL",
        strategy_name="Call",
        contracts=1000,
        legs=legs,
        net_cash_impact=-1000000.0,
        commission_and_fees=1000.0,
    )
    assert success is False

    orders = store.get_orders()
    assert len(orders) > 0
    assert orders[0].status == OrderStatus.REJECTED


def test_settle_expired_options(store):
    """Expired options are settled at intrinsic value and removed from open positions."""
    from datetime import date
    # Add an in-the-money Call option that expired in the past
    store.apply_fill(
        client_order_id="expired_call_order",
        symbol="AAPL 2023-01-20 $150.00 CALL",
        side="buy",
        qty=2.0,
        fill_price=5.0,
        status="FILLED",
    )
    # Add an out-of-the-money Put option that expired in the past
    store.apply_fill(
        client_order_id="expired_put_order",
        symbol="AAPL 2023-01-20 $100.00 PUT",
        side="buy",
        qty=1.0,
        fill_price=2.0,
        status="FILLED",
    )


    class MockQuote:
        price = 160.0

    class MockMarketProvider:
        def get_latest_quote(self, ticker):
            return MockQuote()

    # Settle with current date in 2024 (past expiration)
    settled = store.settle_expired_options(
        market_provider=MockMarketProvider(),
        current_date=date(2024, 1, 1),
    )

    assert len(settled) == 2
    call_settle = next(s for s in settled if s["option_type"] == "CALL")
    assert call_settle["intrinsic_per_share"] == 10.0  # 160 - 150
    assert call_settle["cash_settlement"] == 2000.0  # 10.0 * 2 * 100
    assert call_settle["status"] == "SETTLED"

    put_settle = next(s for s in settled if s["option_type"] == "PUT")
    assert put_settle["intrinsic_per_share"] == 0.0  # max(0, 100 - 160)
    assert put_settle["cash_settlement"] == 0.0
    assert put_settle["status"] == "EXPIRED"

    # All positions should now be closed
    assert len(store.get_open_positions()) == 0


def test_apply_fill_reject_zero_price(store):
    """Fills with zero or negative price must be rejected."""
    success = store.apply_fill("client_order_zero", "AAPL", "buy", 10.0, 0.0, 0.0)
    assert success is False
    orders = store.get_orders()
    assert len(orders) > 0
    assert orders[0].status == OrderStatus.REJECTED

def test_apply_multi_leg_reject_zero_price(store):
    """Multi-leg orders with any zero price leg must be rejected entirely."""
    legs = [
        {"symbol": "AAPL 2026-09-18 $150.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 500.0},
        {"symbol": "AAPL 2026-09-18 $155.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 0.0},
    ]
    success = store.apply_multi_leg_fill(
        client_order_id="multi_zero",
        symbol="AAPL",
        strategy_name="Bull Call Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=-500.0,
        commission_and_fees=1.30,
    )
    assert success is False
    orders = store.get_orders()
    assert len(orders) > 0
    assert orders[0].status == OrderStatus.REJECTED

def test_apply_roll_fill_reject_zero_price(store):
    """Roll orders with any zero price leg must be rejected entirely."""
    close_legs = [
        {"symbol": "AAPL 2026-09-18 $150.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 0.0},
    ]
    open_legs = [
        {"symbol": "AAPL 2026-10-16 $150.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 600.0},
    ]
    success = store.apply_roll_fill(
        client_order_id="roll_zero",
        symbol="AAPL",
        close_legs=close_legs,
        open_legs=open_legs,
        contracts=1,
    )
    assert success is False
    orders = store.get_orders()
    assert len(orders) > 0
    assert orders[0].status == OrderStatus.REJECTED

def test_strategy_id_persistence_on_rejected_order(store):
    store.reset_account()
    res = store.apply_fill("rejected-id", "AAPL", "BUY", 10.0, -5.0, strategy_id="test-strat")
    assert not res
    
    orders = store.get_orders()
    assert len(orders) > 0
    assert orders[0].client_order_id == "rejected-id"
    assert orders[0].status == "rejected"
    

def test_untagged_fallback_closing_action(store):
    store.reset_account()
    store.apply_fill("id-1", "SPY", "BUY", 10.0, 100.0, strategy_id="untagged")
    
    pos = store.get_open_positions()
    assert len(pos) == 1
    assert pos[0].strategy_id == "untagged"
    
    store.apply_fill("id-2", "SPY", "SELL", 10.0, 110.0, strategy_id="new-strat")
    
    pos = store.get_open_positions()
    assert len(pos) == 0
    orders = store.get_orders()
    assert len(orders) == 2
    
    with store.engine.begin() as conn:
        res = conn.execute(__import__("sqlalchemy").text("SELECT * FROM paper_closed_trades")).fetchall()
        closed = res
    assert len(closed) == 1
    assert closed[0][1] == "untagged"  # index of strategy_id  # Closed trade gets the strategy_id of the position it closed
