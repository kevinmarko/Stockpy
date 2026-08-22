"""Unit tests for Options Lifecycle Management & Position Exit Engine.

Tests:
1. Profit target auto-exit (50% max profit threshold)
2. Stop loss auto-exit (200% / 2.0x max loss threshold)
3. 21-DTE gamma management auto-exit
4. Atomic roll fills (closing near-term legs & opening next cycle in single transaction)
5. Roll rejection on insufficient funds
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from data.paper_account_store import PaperAccountStore
from execution.options_paper_executor import OptionsPaperExecutor, _price_option_contract
from settings import settings


@pytest.fixture
def store():
    """Provides an isolated in-memory PaperAccountStore instance."""
    s = PaperAccountStore(db_url="sqlite:///:memory:")
    s.reset_account(starting_cash=100000.0)
    return s


@pytest.fixture
def executor(store):
    """Provides an OptionsPaperExecutor bound to the in-memory store."""
    return OptionsPaperExecutor(store=store)


# ---------------------------------------------------------------------------
# Test 1: Profit Target Auto-Exit
# ---------------------------------------------------------------------------

def test_profit_target_exit(store, executor):
    """
    Tests that a credit spread reaching >= 50% profit triggers PROFIT_TARGET exit
    and executes closing fills cleanly.
    """
    today = date(2026, 8, 14)
    exp_str = (today + timedelta(days=45)).strftime("%Y-%m-%d")

    # Open a Put Credit Spread: Short $150 Put @ $2.00 ($200), Long $145 Put @ $0.50 ($50)
    # Net Initial Credit = $150.00
    legs = [
        {"symbol": f"AAPL {exp_str} $150.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 200.0},
        {"symbol": f"AAPL {exp_str} $145.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
    ]
    open_success = store.apply_multi_leg_fill(
        client_order_id="OPEN-PCS-1",
        symbol="AAPL",
        strategy_name="Put Credit Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=150.0,
        commission_and_fees=1.30,
    )
    assert open_success is True
    assert len(store.get_open_positions()) == 2

    # Spot price is $170 (far OTM), so option prices collapse towards 0.
    # Spot map: AAPL @ 170.0
    spot_map = {"AAPL": 170.0}

    # Evaluate exits
    exits = executor.evaluate_position_exits(
        spot_map=spot_map,
        current_date=today,
        profit_target_pct=0.50,
        stop_loss_multiple=2.0,
        manage_dte_threshold=21,
    )

    assert len(exits) == 1
    exit_order = exits[0]
    assert exit_order["symbol"] == "AAPL"
    assert exit_order["trigger_reason"] == "PROFIT_TARGET"
    assert exit_order["profit_pct"] >= 0.50
    assert len(exit_order["legs"]) == 2

    # Execute auto exits with force=True
    res = executor.execute_auto_exits(exit_candidates=exits, force=True)
    assert res["executed_count"] == 1
    assert res["failed_count"] == 0

    # Verify positions are closed in store
    remaining_positions = store.get_open_positions()
    assert len(remaining_positions) == 0


# ---------------------------------------------------------------------------
# Test 2: Stop Loss Auto-Exit
# ---------------------------------------------------------------------------

def test_stop_loss_exit(store, executor):
    """
    Tests that a credit spread with unrealized loss >= 200% (2.0x initial credit)
    triggers STOP_LOSS exit.
    """
    today = date(2026, 8, 14)
    exp_str = (today + timedelta(days=45)).strftime("%Y-%m-%d")

    # Open a Put Credit Spread: Short $150 Put @ $1.50 ($150), Long $140 Put @ $0.50 ($50)
    # Net Initial Credit = $100.00
    legs = [
        {"symbol": f"MSFT {exp_str} $150.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 150.0},
        {"symbol": f"MSFT {exp_str} $140.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
    ]
    open_success = store.apply_multi_leg_fill(
        client_order_id="OPEN-PCS-MSFT",
        symbol="MSFT",
        strategy_name="Put Credit Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=100.0,
        commission_and_fees=1.30,
    )
    assert open_success is True

    # Spot price plunges to $130 (Deep ITM, short put worth ~ $2000, long put worth ~ $1000, spread loss ~ $1000 >> $200)
    spot_map = {"MSFT": 130.0}

    exits = executor.evaluate_position_exits(
        spot_map=spot_map,
        current_date=today,
        profit_target_pct=0.50,
        stop_loss_multiple=2.0,
        manage_dte_threshold=21,
    )

    assert len(exits) == 1
    exit_order = exits[0]
    assert exit_order["symbol"] == "MSFT"
    assert exit_order["trigger_reason"] == "STOP_LOSS"
    assert exit_order["loss_multiple"] >= 2.0

    # Execute auto-exit
    res = executor.execute_auto_exits(exit_candidates=exits, force=True)
    assert res["executed_count"] == 1
    assert len(store.get_open_positions()) == 0


# ---------------------------------------------------------------------------
# Test 3: 21-DTE Gamma Management Auto-Exit
# ---------------------------------------------------------------------------

def test_21_dte_management_exit(store, executor):
    """
    Tests that a position with DTE <= 21 days triggers DTE_MANAGEMENT exit
    even if profit target or stop loss has not been reached.
    """
    today = date(2026, 8, 14)
    # Expiration is 14 days out (<= 21 DTE threshold)
    exp_str = (today + timedelta(days=14)).strftime("%Y-%m-%d")

    legs = [
        {"symbol": f"NVDA {exp_str} $120.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 300.0},
        {"symbol": f"NVDA {exp_str} $125.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 100.0},
    ]
    store.apply_multi_leg_fill(
        client_order_id="OPEN-CCS-NVDA",
        symbol="NVDA",
        strategy_name="Call Credit Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=200.0,
        commission_and_fees=1.30,
    )

    # Spot price is near ATM ($118), so profit is small (< 50%) and loss is small (< 200%)
    spot_map = {"NVDA": 118.0}

    exits = executor.evaluate_position_exits(
        spot_map=spot_map,
        current_date=today,
        profit_target_pct=0.50,
        stop_loss_multiple=2.0,
        manage_dte_threshold=21,
    )

    assert len(exits) == 1
    assert exits[0]["symbol"] == "NVDA"
    assert exits[0]["trigger_reason"] == "DTE_MANAGEMENT"
    assert exits[0]["dte"] == 14

    # Execute auto exits
    res = executor.execute_auto_exits(exit_candidates=exits, force=True)
    assert res["executed_count"] == 1
    assert len(store.get_open_positions()) == 0


# ---------------------------------------------------------------------------
# Test 4: Atomic Roll Fills
# ---------------------------------------------------------------------------

def test_atomic_roll_fill_success(store):
    """
    Tests apply_roll_fill: atomically closes expiring legs and opens new cycle legs.
    """
    initial_cash = store.get_account().cash

    near_exp = "2026-08-21"
    far_exp = "2026-09-18"

    # Seed near-term open position: Short $150 Put, Long $145 Put
    store.apply_multi_leg_fill(
        client_order_id="INITIAL-PCS",
        symbol="AAPL",
        strategy_name="Put Credit Spread",
        contracts=1,
        legs=[
            {"symbol": f"AAPL {near_exp} $150.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 200.0},
            {"symbol": f"AAPL {near_exp} $145.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
        ],
        net_cash_impact=150.0,
        commission_and_fees=1.30,
    )

    open_pos = store.get_open_positions()
    assert len(open_pos) == 2
    assert any(near_exp in p.symbol for p in open_pos)

    # Perform atomic roll:
    # Close near-term legs: buy back short $150 Put @ $80, sell long $145 Put @ $10 (Net debit to close = $70)
    # Open far-term legs: sell $150 Put @ $250, buy $145 Put @ $70 (Net credit to open = $180)
    # Total net cash impact = +$110 credit - $2.60 fees = +$107.40
    close_legs = [
        {"symbol": f"AAPL {near_exp} $150.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 80.0},
        {"symbol": f"AAPL {near_exp} $145.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 10.0},
    ]
    open_legs = [
        {"symbol": f"AAPL {far_exp} $150.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 250.0},
        {"symbol": f"AAPL {far_exp} $145.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 70.0},
    ]

    roll_success = store.apply_roll_fill(
        client_order_id="ROLL-AAPL-1",
        symbol="AAPL",
        close_legs=close_legs,
        open_legs=open_legs,
        net_cash_impact=107.40,
        commission_and_fees=2.60,
    )

    assert roll_success is True

    # Check positions: near-term positions must be gone, far-term positions must exist
    new_positions = store.get_open_positions()
    assert len(new_positions) == 2
    for p in new_positions:
        assert far_exp in p.symbol
        assert near_exp not in p.symbol

    # Cash balance reflects initial credit + roll credit
    account = store.get_account()
    expected_cash = initial_cash + 150.0 + 107.40
    assert abs(account.cash - expected_cash) < 1e-3


def test_atomic_roll_fill_insufficient_funds_rejection(store):
    """
    Tests that a roll requiring more cash than available balance is rejected
    and leaves positions completely unmodified.
    """
    store.reset_account(starting_cash=50.0)

    near_exp = "2026-08-21"
    far_exp = "2026-09-18"

    # Seed position
    store.apply_fill(
        client_order_id="SEED-POS",
        symbol=f"SPY {near_exp} $500.00 CALL",
        side="buy",
        qty=1.0,
        fill_price=40.0,
    )

    assert len(store.get_open_positions()) == 1

    # Attempt a roll requiring $500 net debit (more than $10 cash balance)
    close_legs = [{"symbol": f"SPY {near_exp} $500.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 30.0}]
    open_legs = [{"symbol": f"SPY {far_exp} $510.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 530.0}]

    roll_success = store.apply_roll_fill(
        client_order_id="ROLL-EXPENSIVE",
        symbol="SPY",
        close_legs=close_legs,
        open_legs=open_legs,
        net_cash_impact=-500.0,
        commission_and_fees=1.30,
    )

    assert roll_success is False

    # Position is unchanged
    pos = store.get_open_positions()
    assert len(pos) == 1
    assert near_exp in pos[0].symbol


# ---------------------------------------------------------------------------
# Test 5: Earnings Crush Trade Execution
# ---------------------------------------------------------------------------

def test_earnings_crush_trade_execution(store, executor):
    """
    Tests execute_earnings_crush_trade:
    1. Executes an Iron Condor candidate with custom legs.
    2. Executes a strike-based candidate (Short Straddle).
    3. Verifies atomic fills, 'Earnings Crush' strategy tag, and account balance updates.
    """
    initial_cash = store.get_account().cash

    candidate = {
        "symbol": "NVDA",
        "strategy": "Iron Condor",
        "expiration": "2026-08-21",
        "earnings_date": "2026-08-20",
        "legs": [
            {"symbol": "NVDA 2026-08-21 $110.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
            {"symbol": "NVDA 2026-08-21 $115.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 180.0},
            {"symbol": "NVDA 2026-08-21 $125.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 200.0},
            {"symbol": "NVDA 2026-08-21 $130.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 60.0},
        ],
        "net_credit": 2.70,
    }

    res = executor.execute_earnings_crush_trade(candidate, contracts=2)

    assert res["success"] is True
    assert res["symbol"] == "NVDA"
    assert res["strategy"] == "Earnings Crush"
    assert res["contracts"] == 2
    assert len(res["legs"]) == 4

    open_pos = store.get_open_positions()
    assert len(open_pos) == 4

    # Net credit = $2.70 * 100 * 2 contracts = $540.00 - fees (0.65 * 4 * 2 = $5.20) = $534.80
    expected_cash = initial_cash + 534.80
    assert abs(store.get_account().cash - expected_cash) < 1e-2

    # Test strike-based Short Straddle candidate
    straddle_candidate = {
        "symbol": "AAPL",
        "strategy": "Short Straddle",
        "expiration": "2026-08-21",
        "spot": 150.0,
        "atm_strike": 150.0,
        "earnings_date": "2026-08-20",
    }
    res_straddle = executor.execute_earnings_crush_trade(straddle_candidate, contracts=1)
    assert res_straddle["success"] is True
    assert res_straddle["symbol"] == "AAPL"
    assert res_straddle["strategy"] == "Earnings Crush"
    assert len(store.get_open_positions()) == 6  # 4 NVDA legs + 2 AAPL legs


# ---------------------------------------------------------------------------
# Test 6: Settle Post-Earnings Trades
# ---------------------------------------------------------------------------

def test_settle_post_earnings_trades(store, executor):
    """
    Tests settle_post_earnings_trades:
    1. Enters an Earnings Crush Iron Condor on pre-earnings day T.
    2. Advances to day T+1 (post-earnings announcement).
    3. Executes settle_post_earnings_trades to close all open legs at market open.
    4. Verifies all positions are closed and IV crush profit is harvested into cash balance.
    """
    today = datetime.now(timezone.utc).date()
    settle_date = today + timedelta(days=1)

    candidate = {
        "symbol": "NVDA",
        "strategy": "Iron Condor",
        "expiration": "2026-08-21",
        "earnings_date": str(today),
        "legs": [
            {"symbol": "NVDA 2026-08-21 $110.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
            {"symbol": "NVDA 2026-08-21 $115.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 180.0},
            {"symbol": "NVDA 2026-08-21 $125.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 200.0},
            {"symbol": "NVDA 2026-08-21 $130.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 60.0},
        ],
        "net_credit": 2.70,
    }

    exec_res = executor.execute_earnings_crush_trade(candidate, contracts=1)
    assert exec_res["success"] is True
    assert len(store.get_open_positions()) == 4

    # Post-earnings spot price is $120 (well within the $115-$125 inner strikes)
    # At market open next day, implied volatility collapses (IV crush).
    settle_res = executor.settle_post_earnings_trades(
        current_date=settle_date,
        spot_map={"NVDA": 120.0},
    )

    assert settle_res["settled_count"] == 1
    assert settle_res["failed_count"] == 0
    assert len(settle_res["settled"]) == 1
    assert settle_res["settled"][0]["symbol"] == "NVDA"

    # All positions must be cleanly closed
    assert len(store.get_open_positions()) == 0

    # Verify orders recorded in store
    orders = store.get_full_orders()
    nvda_ec_orders = [o for o in orders if o["symbol"] == "NVDA" and o.get("strategy_id") == "Earnings Crush" and o.get("order_kind") == "parent"]
    assert len(nvda_ec_orders) == 2, f"Expected 2 parent orders for NVDA EC, found {len(nvda_ec_orders)}"
