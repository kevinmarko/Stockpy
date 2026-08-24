import sqlite3
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from data.paper_account_store import PaperAccountStore, PaperClosedTrade, PaperOrder, PaperPosition
from db_config import session_scope
from execution.broker_base import OrderStatus
from settings import settings

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
    assert len(orders) == 1
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
    assert len(orders) == 1
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
    assert len(orders) == 1
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
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.REJECTED


# ─────────────────────────────────────────────────────────────────────────────
# PR 872 remediation: closed-trade PnL arithmetic regression tests
# ─────────────────────────────────────────────────────────────────────────────

def test_option_realized_pnl_is_per_contract_not_100x(store):
    """Bug 1 regression: option entry/exit prices passed to apply_fill are
    already per-CONTRACT dollar amounts (matching every real writer in this
    codebase -- see apply_roll_fill's own docstring). _record_closed_trade
    must NOT re-apply a x100 multiplier on top of that.

    1 contract, BUY open @ $500/contract, SELL close @ $502/contract,
    commission on the closing leg = $0.65. Expected realized_pnl:
        (502 - 500) * 1 - 0.65 == 1.35
    NOT 200.0 (the old, un-fixed x100-multiplied number) and NOT 2.0 - 0.65
    misapplied some other way -- exactly 1.35.
    """
    symbol = "AAPL 2026-09-18 $150.00 CALL"
    success = store.apply_fill(
        "opt_buy_1", symbol, "buy", 1.0, 500.0,
        commission_and_fees=0.0, status=OrderStatus.FILLED,
    )
    assert success is True

    commission = 0.65
    success = store.apply_fill(
        "opt_sell_1", symbol, "sell", 1.0, 502.0,
        commission_and_fees=commission, status=OrderStatus.FILLED,
    )
    assert success is True

    with session_scope(store.Session) as session:
        row = (
            session.query(PaperClosedTrade)
            .filter_by(symbol=symbol, close_reason="flatten")
            .first()
        )
        assert row is not None
        assert row.realized_pnl == pytest.approx(1.35)
        assert row.realized_pnl != pytest.approx(200.0)


def test_short_equity_close_sign_non_regression(store):
    """Non-regression: the audit found the long/short sign logic in
    _record_closed_trade was already correct pre-fix; only the option x100
    multiplier and the realized_pnl_pct guard changed. A short equity
    position that is bought back at a lower price must realize a POSITIVE
    PnL: entry $100/share short, cover $90/share, 10 shares, no commission
    -> (100 - 90) * 10 == 100.0.
    """
    success = store.apply_fill(
        "short_open", "TSLA", "sell", 10.0, 100.0,
        commission_and_fees=0.0, allow_short=True,
    )
    assert success is True

    success = store.apply_fill(
        "short_close", "TSLA", "buy", 10.0, 90.0,
        commission_and_fees=0.0,
    )
    assert success is True

    with session_scope(store.Session) as session:
        row = session.query(PaperClosedTrade).filter_by(symbol="TSLA").first()
        assert row is not None
        assert row.side == "sell"
        assert row.realized_pnl == pytest.approx(100.0)


def test_settle_expired_options_profit_matches_cash_credit(store):
    """Bug 2 regression: settle_expired_options's `intrinsic` is a per-SHARE
    dollar amount (proven by cash_settlement's own *100.0 conversion), but
    pos.avg_entry_price is per-CONTRACT -- it must be converted to
    per-contract before being handed to _record_closed_trade as exit_price,
    so the closed-trade ledger row and the real cash credit agree.

    1 contract, BUY open @ $500/contract. Expires ITM with spot=$157,
    strike=$150 -> intrinsic = $7.00/share -> cash credit = 7.00*1*100 =
    $700.00. Expected realized_pnl = (700 - 500) * 1 = $200.00, matching
    the SIGN and rough MAGNITUDE of the real $700 cash credit (not the old
    buggy apples-to-oranges (7 - 500) subtraction).
    """
    symbol = "AAPL 2020-01-17 $150.00 CALL"
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[{"symbol": "AAPL", "price": 157.0}]):
        success = store.apply_fill(
            "settle_buy", symbol, "buy", 1.0, 500.0,
            commission_and_fees=0.0, status=OrderStatus.FILLED,
        )
        assert success is True

        cash_before = store.get_account().cash

        class MockQuote:
            price = 157.0

        class MockMarketProvider:
            def get_latest_quote(self, ticker):
                return MockQuote()

        from datetime import date
        settled = store.settle_expired_options(
            market_provider=MockMarketProvider(),
            current_date=date(2024, 1, 1),
        )
        assert len(settled) == 1
        assert settled[0]["cash_settlement"] == pytest.approx(700.0)

        cash_after = store.get_account().cash

    # Real cash credit actually applied to the account.
    assert cash_after - cash_before == pytest.approx(700.0)

    with session_scope(store.Session) as session:
        row = (
            session.query(PaperClosedTrade)
            .filter_by(symbol=symbol, close_reason="expiry_settlement")
            .first()
        )
        assert row is not None
        assert row.realized_pnl > 0
        assert row.realized_pnl == pytest.approx(200.0)


def test_realized_pnl_pct_none_for_degenerate_avg_entry_price(store):
    """Bug 3 regression: a position with avg_entry_price <= 0 (real production
    rows can have this -- see scripts/purge_corrupt_paper_options.py) must
    close with realized_pnl_pct == None, never a fabricated 0.0 (CONSTRAINT
    #4). Guard threshold must be `>= 1e-12`, never an exact `> 0` check.
    """
    corrupt_pos = PaperPosition(
        symbol="CORRUPT", strategy_id="untagged", pilot_id=None,
        experiment_arm=None, qty=10.0, avg_entry_price=0.0, entry_ts=None,
    )
    with session_scope(store.Session) as session:
        store._record_closed_trade(session, corrupt_pos, 10.0, 50.0, "flatten", 0.0)

    with session_scope(store.Session) as session:
        row = session.query(PaperClosedTrade).filter_by(symbol="CORRUPT").first()
        assert row is not None
        assert row.realized_pnl_pct is None


def test_closed_trade_has_real_entry_ts_and_positive_holding_period(store):
    """Bug 4 regression: PaperPosition.entry_ts is set at open time and must
    survive into the closed-trade ledger row (not falsified to exit_ts /
    "now"), and holding_period_days must be computed from the real gap.
    """
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        success = store.apply_fill("ht_buy", "AAPL", "buy", 10.0, 100.0, 0.0)
        assert success is True

        # Simulate a real 5-day holding period by moving the position's
        # recorded entry_ts back in time (apply_fill itself always stamps a
        # real "now" on open -- this only manufactures a time gap for the
        # test, it does not touch the code path under test).
        with session_scope(store.Session) as session:
            pos = (
                session.query(PaperPosition)
                .filter_by(symbol="AAPL", strategy_id="untagged")
                .first()
            )
            assert pos.entry_ts is not None
            pos.entry_ts = pos.entry_ts - timedelta(days=5)

        success = store.apply_fill("ht_sell", "AAPL", "sell", 10.0, 110.0, 0.0)
        assert success is True

    with session_scope(store.Session) as session:
        row = (
            session.query(PaperClosedTrade)
            .filter_by(symbol="AAPL", close_reason="flatten")
            .first()
        )
        assert row is not None
        assert row.entry_ts is not None
        assert row.exit_ts is not None
        assert row.entry_ts < row.exit_ts
        assert row.holding_period_days is not None
        assert row.holding_period_days > 0
        assert row.holding_period_days == pytest.approx(5.0, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# PR 872 remediation, Agent 3: migration safety (Bug 6) + untagged fallback
# semantics (Bug 7) + the stray strategy_name-in-symbol rejection label.
# ─────────────────────────────────────────────────────────────────────────────


def _create_legacy_paper_positions_db(db_path, rows):
    """Hand-build a pre-strategy_id `paper_positions` table (single-column
    PK, nullable qty/avg_entry_price -- matching the real legacy shape
    before the strategy_id/composite-PK rebuild existed) plus a minimal
    `paper_account` row, entirely via raw sqlite3 so PaperAccountStore's
    own migration code is never invoked while seeding the fixture.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE paper_positions ("
            "symbol VARCHAR(64) PRIMARY KEY, "
            "qty REAL, "
            "avg_entry_price REAL"
            ")"
        )
        conn.execute("CREATE TABLE paper_account (id INTEGER PRIMARY KEY, cash_balance REAL NOT NULL)")
        conn.execute("INSERT INTO paper_account (id, cash_balance) VALUES (1, 100000.0)")
        for symbol, qty, avg_entry_price in rows:
            conn.execute(
                "INSERT INTO paper_positions (symbol, qty, avg_entry_price) VALUES (?, ?, ?)",
                (symbol, qty, avg_entry_price),
            )
        conn.commit()
    finally:
        conn.close()


def test_migration_success_preserves_legacy_positions(tmp_path):
    """Happy path: a legacy single-PK paper_positions table with valid rows
    is rebuilt into the composite (symbol, strategy_id) PK schema, and the
    rows survive, tagged 'untagged' (since their real strategy is
    genuinely unknown)."""
    db_file = tmp_path / "legacy_ok.db"
    _create_legacy_paper_positions_db(db_file, [("AAPL", 10.0, 150.0), ("MSFT", -5.0, 300.0)])

    store = PaperAccountStore(db_url=f"sqlite:///{db_file}")

    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        positions = {p.symbol: p for p in store.get_open_positions()}
    assert set(positions) == {"AAPL", "MSFT"}
    assert positions["AAPL"].qty == pytest.approx(10.0)
    assert positions["AAPL"].strategy_id == "untagged"
    assert positions["MSFT"].qty == pytest.approx(-5.0)

    # A second construction against the same (now-migrated) DB must be a
    # no-op, not attempt the destructive rebuild again.
    store2 = PaperAccountStore(db_url=f"sqlite:///{db_file}")
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        positions2 = {p.symbol: p for p in store2.get_open_positions()}
    assert set(positions2) == {"AAPL", "MSFT"}


def test_migration_partial_failure_preserves_original_data(tmp_path):
    """Bug 6 regression: a legacy row with a NULL qty (violates the new
    table's NOT NULL constraint) must cause the WHOLE migration to roll
    back -- not leave an empty new paper_positions table with the real
    data silently dropped in an orphaned old_paper_positions. Construction
    must raise loudly (fail closed, CONSTRAINT #6), and the ORIGINAL data
    must still be present and readable afterward."""
    db_file = tmp_path / "legacy_corrupt.db"
    _create_legacy_paper_positions_db(
        db_file,
        [("AAPL", 10.0, 150.0), ("MSFT", None, 300.0)],
    )

    with pytest.raises(RuntimeError, match="paper_positions migration failed"):
        PaperAccountStore(db_url=f"sqlite:///{db_file}")

    # Original data intact and readable -- NOT silently lost. No orphaned
    # old_paper_positions table should be left behind either (the whole
    # transaction rolled back, including the RENAME).
    conn = sqlite3.connect(str(db_file))
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "paper_positions" in tables
        assert "old_paper_positions" not in tables

        rows = sorted(
            conn.execute("SELECT symbol, qty, avg_entry_price FROM paper_positions").fetchall()
        )
    finally:
        conn.close()
    assert rows == [("AAPL", 10.0, 150.0), ("MSFT", None, 300.0)]


def test_migration_raises_on_unsupported_backend(monkeypatch):
    """Bug 6 point 3: a non-SQLite backend that still needs the legacy ->
    composite-PK migration must raise loudly instead of the old
    SQLite-only PRAGMA check silently no-op'ing (swallowed by the former
    broad `except`), which would otherwise leave the ORM's composite-PK
    model disagreeing with a live single-PK table."""
    import data.paper_account_store as pas_module

    store = PaperAccountStore.__new__(PaperAccountStore)

    class FakeURL:
        database = "ignored"

        def get_backend_name(self):
            return "postgresql"

    class FakeEngine:
        url = FakeURL()

    store.engine = FakeEngine()

    class FakeInspector:
        def has_table(self, name):
            return True

        def get_columns(self, name):
            # Legacy shape: no strategy_id column.
            return [{"name": "symbol"}, {"name": "qty"}, {"name": "avg_entry_price"}]

    monkeypatch.setattr(pas_module, "inspect", lambda engine: FakeInspector())

    with pytest.raises(RuntimeError, match="postgresql"):
        store._migrate_paper_positions_schema()


def test_untagged_fallback_does_not_silently_close_by_default(store):
    """Bug 7 regression: a strategy_A sell-to-open request with no existing
    strategy_A position must NOT silently reinterpret an existing
    'untagged' long as the position being closed, under the new default
    (allow_untagged_fallback=False). The untagged position stays
    untouched and strategy_A gets its own new short position instead."""
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        # Legacy untagged long position (e.g. from a Bug 6 migration).
        assert store.apply_fill("legacy_buy", "AAPL", "buy", 10.0, 100.0, 0.0) is True

        # strategy_A sells AAPL with no existing strategy_A position --
        # this must open a NEW short for strategy_A, not close 'untagged'.
        success = store.apply_fill(
            "stratA_sell", "AAPL", "sell", 5.0, 150.0, 0.0,
            allow_short=True, strategy_id="strategy_A",
        )
        assert success is True

    # Assertions run inside the session scope -- SQLAlchemy expires ORM
    # attributes on commit, so reading them after the session has closed
    # (and committed) raises DetachedInstanceError.
    with session_scope(store.Session) as session:
        untagged_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="untagged").first()
        strat_a_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="strategy_A").first()
        closed_untagged = session.query(PaperClosedTrade).filter_by(strategy_id="untagged").all()

        assert untagged_pos is not None
        assert untagged_pos.qty == pytest.approx(10.0)  # untouched

        assert strat_a_pos is not None
        assert strat_a_pos.qty == pytest.approx(-5.0)  # its own new short

        assert closed_untagged == []  # untagged's position was never closed


def test_untagged_fallback_works_when_explicitly_requested(store):
    """The untagged fallback still works when a caller explicitly opts in
    via allow_untagged_fallback=True, and the resulting closed trade is
    correctly attributed to 'untagged' (the actual position owner being
    closed), not to the calling strategy_id."""
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        assert store.apply_fill("legacy_buy2", "AAPL", "buy", 10.0, 100.0, 0.0) is True

        success = store.apply_fill(
            "stratA_sell2", "AAPL", "sell", 5.0, 150.0, 0.0,
            strategy_id="strategy_A", allow_untagged_fallback=True,
        )
        assert success is True

    with session_scope(store.Session) as session:
        untagged_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="untagged").first()
        strat_a_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="strategy_A").first()
        closed = session.query(PaperClosedTrade).filter_by(symbol="AAPL").all()

        # The untagged long was reduced (closed against) -- no new
        # strategy_A position was opened.
        assert untagged_pos is not None
        assert untagged_pos.qty == pytest.approx(5.0)
        assert strat_a_pos is None

        assert len(closed) == 1
        assert closed[0].strategy_id == "untagged"


def test_untagged_fallback_multi_leg_gated_by_default(store):
    """The same strict opt-in gate applies to apply_multi_leg_fill's
    per-leg untagged fallback -- a strategy_A multi-leg open must not
    silently borrow an untagged single-leg position on one of its legs."""
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        # Legacy untagged short call position on the leg strategy_A is
        # about to "buy" (which, under the old unconditional fallback,
        # would have been reinterpreted as closing this untagged short).
        assert store.apply_fill(
            "legacy_short_call", "AAPL 2026-09-18 $150.00 CALL", "sell", 2.0, 100.0, 0.0,
            allow_short=True,
        ) is True

        legs = [
            {"symbol": "AAPL 2026-09-18 $150.00 CALL", "side": "buy", "qty": 2.0, "fill_price": 250.0},
            {"symbol": "AAPL 2026-09-18 $155.00 CALL", "side": "sell", "qty": 2.0, "fill_price": 100.0},
        ]
        commission = 2.60
        net_cash_impact = -(300.0 + commission)

        success = store.apply_multi_leg_fill(
            client_order_id="stratA_multi",
            symbol="AAPL",
            strategy_name="Bull Call Spread",
            contracts=2,
            legs=legs,
            net_cash_impact=net_cash_impact,
            commission_and_fees=commission,
            strategy_id="strategy_A",
        )
        assert success is True

    with session_scope(store.Session) as session:
        untagged_short = (
            session.query(PaperPosition)
            .filter_by(symbol="AAPL 2026-09-18 $150.00 CALL", strategy_id="untagged")
            .first()
        )
        strat_a_long = (
            session.query(PaperPosition)
            .filter_by(symbol="AAPL 2026-09-18 $150.00 CALL", strategy_id="strategy_A")
            .first()
        )
        closed_untagged = session.query(PaperClosedTrade).filter_by(strategy_id="untagged").all()

        assert untagged_short is not None
        assert untagged_short.qty == pytest.approx(-2.0)  # untouched
        assert strat_a_long is not None
        assert strat_a_long.qty == pytest.approx(2.0)  # its own new long leg
        assert closed_untagged == []


def test_multi_leg_reject_uses_bare_symbol_not_strategy_prefixed(store):
    """The multi-leg price-validation-rejection path used to insert the
    REJECTED order under f"{strategy_name} {symbol}" instead of the bare
    symbol -- inconsistent with every other rejection/success path in this
    file (strategy attribution lives in strategy_id, not the symbol
    string)."""
    legs = [
        {"symbol": "AAPL 2026-09-18 $150.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 0.0},
    ]
    success = store.apply_multi_leg_fill(
        client_order_id="bad_price_order",
        symbol="AAPL",
        strategy_name="Bull Call Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=-500.0,
        commission_and_fees=1.30,
    )
    assert success is False

    with session_scope(store.Session) as session:
        po = session.query(PaperOrder).filter_by(client_order_id="bad_price_order").first()
        assert po is not None
        assert po.symbol == "AAPL"
        assert "Bull Call Spread" not in po.symbol


def test_retag_position_simple_move(store):
    """retag_position moves a position with no existing target row."""
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        assert store.apply_fill("retag_buy", "AAPL", "buy", 10.0, 150.0, 0.0) is True

    moved = store.retag_position("AAPL", "untagged", "strategy_B")
    assert moved is True

    with session_scope(store.Session) as session:
        untagged_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="untagged").first()
        strat_b_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="strategy_B").first()

        assert untagged_pos is None
        assert strat_b_pos is not None
        assert strat_b_pos.qty == pytest.approx(10.0)
        assert strat_b_pos.avg_entry_price == pytest.approx(150.0)


def test_retag_position_returns_false_when_no_source(store):
    assert store.retag_position("AAPL", "untagged", "strategy_B") is False


def test_retag_position_merges_with_existing_target_same_sign(store):
    """When the target (symbol, to_strategy_id) already has a same-sign
    position, retag_position weighted-averages the two cost bases rather
    than raising a PK collision."""
    with patch("data.paper_account_store.fmp_client.batch_quote", return_value=[]):
        # untagged: 10 shares @ 100
        assert store.apply_fill("merge_untagged_buy", "AAPL", "buy", 10.0, 100.0, 0.0) is True
        # strategy_B already has its own: 10 shares @ 200
        assert store.apply_fill(
            "merge_stratb_buy", "AAPL", "buy", 10.0, 200.0, 0.0, strategy_id="strategy_B",
        ) is True

    moved = store.retag_position("AAPL", "untagged", "strategy_B")
    assert moved is True

    with session_scope(store.Session) as session:
        untagged_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="untagged").first()
        strat_b_pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="strategy_B").first()

        assert untagged_pos is None
        assert strat_b_pos is not None
        assert strat_b_pos.qty == pytest.approx(20.0)
        # Weighted average: (10*100 + 10*200) / 20 == 150
        assert strat_b_pos.avg_entry_price == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# transactions_store bridge (PR 872 remediation, Task 1: same-process WAL
# contention repro + fix, non-atomicity fix, fails-open visibility fix).
# ---------------------------------------------------------------------------


def test_transactions_store_bridge_lands_row_fast_and_correctly(tmp_path, monkeypatch):
    """A bridged closed trade must land in transactions_store's real `trades`
    table (not just "no exception raised"), on the SAME db_url the
    PaperAccountStore itself was constructed with, and without an anomalous
    multi-second stall (the empirically-confirmed same-process WAL-writer
    contention this fix closes -- see _record_closed_trade's own comment for
    the full repro writeup). Uses a real, file-backed SQLite DB (not
    `:memory:`) so this actually exercises db_config.py's WAL + busy_timeout
    PRAGMAs, matching the incident's real conditions."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    db_path = tmp_path / "bridge_fast.db"
    db_url = f"sqlite:///{db_path}"
    store = PaperAccountStore(db_url=db_url)
    assert store._transactions_store is not None

    assert store.apply_fill("bridge_buy", "TEST", "buy", 10.0, 100.0, strategy_id="bridge_strat") is True

    t0 = time.time()
    assert store.apply_fill("bridge_sell", "TEST", "sell", 10.0, 110.0, strategy_id="bridge_strat") is True
    elapsed = time.time() - t0

    assert store._transactions_bridge_failures == 0
    # Generous bound: a genuinely-contended second connection under this
    # file's own busy_timeout=5000ms PRAGMA would take multiple seconds;
    # the fixed (session-shared) path costs the same single write as any
    # other apply_fill call, empirically ~5ms.
    assert elapsed < 1.0, f"bridge write took {elapsed:.3f}s -- possible regression to lock contention"

    import transactions_store
    ts = transactions_store.TransactionsStore(db_url=db_url)
    df = ts.closed_trades_df()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["symbol"] == "TEST"
    assert row["strategy"] == "bridge_strat"
    assert row["entry_price"] == pytest.approx(100.0)
    assert row["exit_price"] == pytest.approx(110.0)
    assert row["shares"] == pytest.approx(10.0)


def test_transactions_store_bridge_failure_is_non_fatal_and_visible(monkeypatch, caplog):
    """A bridge write that fails must (a) leave the paper close fully
    successful (position closed, cash updated) and (b) be OBSERVABLE --
    both the in-process failure counter and a greppable WARNING log."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    store = PaperAccountStore(db_url="sqlite:///:memory:")
    assert store._transactions_store is not None

    def _boom(*args, **kwargs):
        raise RuntimeError("forced bridge failure for test")

    monkeypatch.setattr(store._transactions_store, "record_trade", _boom)

    assert store.apply_fill("boom_buy", "TEST", "buy", 5.0, 50.0, strategy_id="s1") is True

    with caplog.at_level("WARNING"):
        assert store.apply_fill("boom_sell", "TEST", "sell", 5.0, 55.0, strategy_id="s1") is True

    # (a) The paper close itself is unaffected by the bridge failure.
    assert store.get_open_positions() == []
    account = store.get_account()
    assert account.cash == pytest.approx(100000.0 - 5.0 * 50.0 + 5.0 * 55.0)

    # (b) Visible: counter incremented, and a stable greppable log message.
    assert store._transactions_bridge_failures == 1
    assert any(
        "transactions_store bridge failed" in rec.message for rec in caplog.records
    ), "expected a greppable 'transactions_store bridge failed' WARNING log"


def test_transactions_store_bridge_disabled_by_default(tmp_path):
    """PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED defaults False -- a
    PaperAccountStore constructed with today's real default settings must
    never construct the bridge companion store at all (today's exact
    pre-feature behavior preserved)."""
    assert settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED is False
    db_url = f"sqlite:///{tmp_path / 'bridge_off.db'}"
    store = PaperAccountStore(db_url=db_url)
    assert store._transactions_store is None
