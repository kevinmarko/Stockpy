"""tests/test_retrospective_learning_loop_m1_challenger2.py
=========================================================
Adversarial empirical challenge suite for Milestone 1:
- Bridge conviction forwarding & calibration_curve integration across boundary values (0.0, 0.5, 1.0, None)
- Side normalization ("long" / "short") and side-aware win rate math
- Completeness metrics edge cases: 0 closed trades, 100% failures, 100% successes, disabled bridge, mixed states
- Multi-leg option fills (lifecycle, snapshot attribution, conviction forwarding, side normalization)
- Roll option fills (close leg preservation of original conviction vs new leg snapshot attribution)
- Strict anti-fabrication guards for historical and manual multi-leg/roll fills
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest import mock
import numpy as np
import pandas as pd
import pytest

from settings import settings
import database_setup
from data.paper_account_store import (
    PaperAccountStore,
    PaperPosition,
    PaperClosedTrade,
    PaperEntrySnapshot,
)
import transactions_store
import evaluation_engine
import pilots.calibration as pilots_cal


@pytest.fixture
def isolated_db(tmp_path):
    """Provides a fresh SQLite file-backed DB URL for complete test isolation."""
    db_file = tmp_path / f"test_retro_challenger2_{int(time.time() * 1000)}.db"
    return f"sqlite:///{db_file}"


# ===========================================================================
# Group 1: Bridge Conviction Forwarding & Calibration Curve Integration
# ===========================================================================

def test_conviction_boundary_values_binned_correctly(isolated_db, monkeypatch):
    """Verify conviction values 0.0, 0.25, 0.5, 0.75, 1.0, and None are correctly handled.

    Convictions 0.0 and 1.0 must be captured in the lowest and highest bins,
    while None (manual trade) must be cleanly dropped without error.
    """
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    test_cases = [
        ("T_ZERO", 0.0, "signal_driven"),
        ("T_Q1", 0.25, "signal_driven"),
        ("T_MID", 0.50, "signal_driven"),
        ("T_Q3", 0.75, "signal_driven"),
        ("T_ONE", 1.00, "signal_driven"),
        ("T_MAN", None, "manual"),
    ]

    for sym, conv, prov in test_cases:
        store.apply_fill(
            client_order_id=f"open_{sym}",
            symbol=sym,
            side="buy",
            qty=10.0,
            fill_price=100.0,
            provenance=prov,
            conviction=conv,
        )
        store.apply_fill(
            client_order_id=f"close_{sym}",
            symbol=sym,
            side="sell",
            qty=10.0,
            fill_price=110.0,
        )

    ts = transactions_store.TransactionsStore(db_url=isolated_db)
    df_closed = ts.closed_trades_df()
    assert len(df_closed) == 6

    # Verify trades table values
    zero_row = df_closed[df_closed["symbol"] == "T_ZERO"].iloc[0]
    assert zero_row["conviction"] == pytest.approx(0.0)

    one_row = df_closed[df_closed["symbol"] == "T_ONE"].iloc[0]
    assert one_row["conviction"] == pytest.approx(1.0)

    man_row = df_closed[df_closed["symbol"] == "T_MAN"].iloc[0]
    assert np.isnan(man_row["conviction"]) or man_row["conviction"] is None

    # Run calibration_curve with 10 bins, min_trades_per_bin=1
    cal_df = evaluation_engine.calibration_curve(ts, n_bins=10, min_trades_per_bin=1)
    assert not cal_df.empty
    assert len(cal_df) == 10

    # Total trades scored must be exactly 5 (the manual trade with None was dropped)
    assert cal_df["count"].sum() == 5

    # Check lowest bin [0.0, 0.1]: must contain T_ZERO
    bin_lowest = cal_df.iloc[0]
    assert bin_lowest["bin_low"] <= 0.0 <= bin_lowest["bin_high"]
    assert bin_lowest["count"] >= 1
    assert bin_lowest["conviction_mean"] == pytest.approx(0.0)

    # Check highest bin (0.9, 1.0]: must contain T_ONE
    bin_highest = cal_df.iloc[-1]
    assert bin_highest["bin_low"] < 1.0 <= bin_highest["bin_high"]
    assert bin_highest["count"] >= 1
    assert bin_highest["conviction_mean"] == pytest.approx(1.0)


def test_side_normalization_long_and_short_win_rate_math(isolated_db, monkeypatch):
    """Adversarial stress test on side normalization:

    Ensures long and short positions are bridged as 'long' and 'short' (not 'buy'/'sell'),
    and calibration_curve correctly computes side-aware win rates:
    - Long win: exit_price > entry_price
    - Short win: exit_price < entry_price
    - Scratch trade (exit == entry): neither is a win (win=False)
    """
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "DATABASE_URL", isolated_db)
    store = PaperAccountStore(db_url=isolated_db)


    # 1. Ten long trades at conviction 0.8: 5 wins, 5 losses
    for i in range(5):
        # Long winner
        store.apply_fill(f"lw_o_{i}", f"LW{i}", "buy", 10.0, 100.0, conviction=0.8, provenance="signal_driven")
        store.apply_fill(f"lw_c_{i}", f"LW{i}", "sell", 10.0, 120.0)
        # Long loser
        store.apply_fill(f"ll_o_{i}", f"LL{i}", "buy", 10.0, 100.0, conviction=0.8, provenance="signal_driven")
        store.apply_fill(f"ll_c_{i}", f"LL{i}", "sell", 10.0, 80.0)

    # 2. Ten short trades at conviction 0.3: 5 wins, 5 losses
    for i in range(5):

        # Short winner (exit < entry)
        store.apply_fill(f"sw_o_{i}", f"SW{i}", "sell", 10.0, 100.0, conviction=0.3, provenance="signal_driven", allow_short=True)
        store.apply_fill(f"sw_c_{i}", f"SW{i}", "buy", 10.0, 80.0)
        # Short loser (exit > entry)
        store.apply_fill(f"sl_o_{i}", f"SL{i}", "sell", 10.0, 100.0, conviction=0.3, provenance="signal_driven", allow_short=True)
        store.apply_fill(f"sl_c_{i}", f"SL{i}", "buy", 10.0, 120.0)

    # 3. Two scratch trades at conviction 0.5: exit == entry
    # Long scratch
    store.apply_fill("scr_l_o", "L_SCRATCH", "buy", 10.0, 100.0, conviction=0.5, provenance="signal_driven")
    store.apply_fill("scr_l_c", "L_SCRATCH", "sell", 10.0, 100.0)
    # Short scratch
    store.apply_fill("scr_s_o", "S_SCRATCH", "sell", 10.0, 100.0, conviction=0.5, provenance="signal_driven", allow_short=True)
    store.apply_fill("scr_s_c", "S_SCRATCH", "buy", 10.0, 100.0)


    ts = transactions_store.TransactionsStore(db_url=isolated_db)
    df_closed = ts.closed_trades_df()
    assert len(df_closed) == 22

    # Check side normalization in DB
    long_rows = df_closed[df_closed["symbol"].str.startswith("L")]
    assert (long_rows["side"] == "long").all(), "Long trades must be bridged as 'long'"

    short_rows = df_closed[df_closed["symbol"].str.startswith("S")]
    assert (short_rows["side"] == "short").all(), "Short trades must be bridged as 'short'"

    # Run calibration_curve with n_bins=10, min_trades_per_bin=5
    cal_df = evaluation_engine.calibration_curve(ts, n_bins=10, min_trades_per_bin=5)

    # Find the bin covering conviction 0.8: must have count=10, win_rate=0.5
    bin_08 = cal_df[(cal_df["bin_low"] <= 0.8) & (cal_df["bin_high"] >= 0.8)].iloc[0]
    assert bin_08["count"] == 10
    assert bin_08["win_rate"] == pytest.approx(0.5)

    # Find the bin covering conviction 0.3: must have count=10, win_rate=0.5 (from shorts!)
    bin_03 = cal_df[(cal_df["bin_low"] <= 0.3) & (cal_df["bin_high"] >= 0.3)].iloc[0]
    assert bin_03["count"] == 10
    assert bin_03["win_rate"] == pytest.approx(0.5)

    # Find the bin covering conviction 0.5: count=2, but min_trades_per_bin=5 -> win_rate must be NaN
    bin_05 = cal_df[(cal_df["bin_low"] <= 0.5) & (cal_df["bin_high"] >= 0.5)].iloc[0]
    assert bin_05["count"] == 2
    assert np.isnan(bin_05["win_rate"])

    # Test calibration_view integration in pilots/calibration.py
    monkeypatch.setattr(transactions_store, "resolve_database_url", lambda: isolated_db)
    cal_summary = pilots_cal.calibration_view(n_bins=10, min_trades_per_bin=5)
    assert cal_summary["total"] == 22
    assert cal_summary["n_scored_bins"] == 2
    assert cal_summary["overall_win_rate"] == pytest.approx(0.5)
    assert cal_summary["reason"] is None




def test_manual_trade_conviction_null_guarantee(isolated_db, monkeypatch):
    """Anti-fabrication challenge: manual trades must never retain conviction even if explicitly passed."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    store.apply_fill(
        client_order_id="man_try_conv",
        symbol="DIS",
        side="buy",
        qty=10.0,
        fill_price=100.0,
        provenance="manual",
        conviction=0.95,  # Adversarial injection: manual trade with conviction
        raw_forecast=120.0,
        signal_score=3.5,
    )
    store.apply_fill(
        client_order_id="man_try_close",
        symbol="DIS",
        side="sell",
        qty=10.0,
        fill_price=105.0,
    )

    # Snapshot check
    snap = store.get_entry_snapshot(store.get_full_closed_trades("DIS")[0]["entry_snapshot_id"])
    assert snap is not None
    assert snap["provenance"] == "manual"
    assert snap["conviction"] is None, "Manual trade must have conviction stripped"
    assert snap["raw_forecast"] is None
    assert snap["signal_score"] is None

    # TransactionsStore bridge check
    ts = transactions_store.TransactionsStore(db_url=isolated_db)
    df = ts.closed_trades_df()
    assert len(df) == 1
    assert df.iloc[0]["conviction"] is None or pd.isna(df.iloc[0]["conviction"])



# ===========================================================================
# Group 2: Completeness Metrics Math Under Edge Conditions
# ===========================================================================

def test_completeness_metrics_zero_closed_trades(isolated_db, monkeypatch):
    """Zero closed trades must report 100.0% completeness and zero counts without division by zero."""
    store = PaperAccountStore(db_url=isolated_db)

    # 1. Bridge disabled
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", False)
    m_off = store.get_bridge_completeness_metrics()
    assert m_off["bridge_enabled"] is False
    assert m_off["total_closed_trades"] == 0
    assert m_off["attempted_count"] == 0
    assert m_off["bridged_count"] == 0
    assert m_off["failed_count"] == 0
    assert m_off["disabled_count"] == 0
    assert m_off["completeness_pct"] == 100.0
    assert m_off["status"] == "disabled"
    assert m_off["last_failure"] is None

    # 2. Bridge enabled, but zero trades have ever been attempted through it
    # -- CONSTRAINT #4: this is genuinely unmeasured, not a fabricated
    # all-clear. "100%/healthy" is the honest reading ONLY when the bridge is
    # deliberately off (nothing to bridge, case 1 above); with the bridge ON
    # and nothing yet attempted, the honest answer is "unknown", not "100%".
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    m_on = store.get_bridge_completeness_metrics()
    assert m_on["bridge_enabled"] is True
    assert m_on["total_closed_trades"] == 0
    assert m_on["completeness_pct"] is None
    assert m_on["status"] == "unknown"


def test_completeness_metrics_100_percent_failures(isolated_db, monkeypatch):
    """100% bridge failures must yield completeness_pct=0.0 and status='degraded'."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    # Force bridge write failure
    def _always_fail(*args, **kwargs):
        raise RuntimeError("database table locked or disk full")

    monkeypatch.setattr(store._transactions_store, "record_trade", _always_fail)

    for i in range(5):
        store.apply_fill(f"f_o_{i}", f"F{i}", "buy", 10.0, 50.0)
        store.apply_fill(f"f_c_{i}", f"F{i}", "sell", 10.0, 55.0)

    m = store.get_bridge_completeness_metrics()
    assert m["total_closed_trades"] == 5
    assert m["attempted_count"] == 5
    assert m["bridged_count"] == 0
    assert m["failed_count"] == 5
    assert m["disabled_count"] == 0
    assert m["completeness_pct"] == 0.0
    assert m["status"] == "degraded"
    assert m["last_failure"] is not None
    assert "database table locked" in m["last_failure"]["error"]


def test_completeness_metrics_100_percent_successes(isolated_db, monkeypatch):
    """100% bridge successes must yield completeness_pct=100.0 and status='healthy'."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    for i in range(8):
        store.apply_fill(f"s_o_{i}", f"S{i}", "buy", 10.0, 50.0)
        store.apply_fill(f"s_c_{i}", f"S{i}", "sell", 10.0, 55.0)

    m = store.get_bridge_completeness_metrics()
    assert m["total_closed_trades"] == 8
    assert m["attempted_count"] == 8
    assert m["bridged_count"] == 8
    assert m["failed_count"] == 0
    assert m["disabled_count"] == 0
    assert m["completeness_pct"] == 100.0
    assert m["status"] == "healthy"
    assert m["last_failure"] is None


def test_completeness_metrics_disabled_bridge_with_trades(isolated_db, monkeypatch):
    """Closed trades under disabled bridge must record status='disabled' with completeness_pct=100.0."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", False)
    store = PaperAccountStore(db_url=isolated_db)

    for i in range(4):
        store.apply_fill(f"d_o_{i}", f"D{i}", "buy", 10.0, 50.0)
        store.apply_fill(f"d_c_{i}", f"D{i}", "sell", 10.0, 55.0)

    m = store.get_bridge_completeness_metrics()
    assert m["bridge_enabled"] is False
    assert m["total_closed_trades"] == 4
    assert m["attempted_count"] == 0
    assert m["bridged_count"] == 0
    assert m["failed_count"] == 0
    assert m["disabled_count"] == 4
    assert m["completeness_pct"] == 100.0
    assert m["status"] == "disabled"


def test_completeness_metrics_mixed_states_and_error_truncation(isolated_db, monkeypatch):
    """Mixed states (bridged, failed, disabled) and adversarial long error truncation."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    # 1. Three bridged
    for i in range(3):
        store.apply_fill(f"m_b_o_{i}", f"MB{i}", "buy", 10.0, 50.0)
        store.apply_fill(f"m_b_c_{i}", f"MB{i}", "sell", 10.0, 55.0)

    # 2. Two disabled directly inserted
    with store.Session() as s:
        for i in range(2):
            s.add(PaperClosedTrade(
                symbol=f"MD{i}", side="buy", qty=10.0, entry_price=50.0, exit_price=55.0,
                realized_pnl=50.0, close_reason="flatten", bridge_status="disabled",
            ))
        s.commit()

    # 3. One failed with a 2000-character adversarial error
    long_error = "CRITICAL_FAILURE_" + ("X" * 2000)

    def _raise_long(*args, **kwargs):
        raise RuntimeError(long_error)

    monkeypatch.setattr(store._transactions_store, "record_trade", _raise_long)
    store.apply_fill("m_f_o", "MFAIL", "buy", 10.0, 50.0)
    store.apply_fill("m_f_c", "MFAIL", "sell", 10.0, 55.0)

    # Verify error truncation in paper_closed_trades
    fail_trade = store.get_full_closed_trades("MFAIL")[0]
    assert fail_trade["bridge_status"] == "failed"
    assert len(fail_trade["bridge_error"]) <= 500
    assert fail_trade["bridge_error"].startswith("CRITICAL_FAILURE_")

    m = store.get_bridge_completeness_metrics()
    assert m["total_closed_trades"] == 6
    assert m["bridged_count"] == 3
    assert m["failed_count"] == 1
    assert m["disabled_count"] == 2
    assert m["attempted_count"] == 4
    # completeness = (3 / 4) * 100 = 75.0%
    assert m["completeness_pct"] == pytest.approx(75.0)
    assert m["status"] == "degraded"
    assert m["last_failure"]["symbol"] == "MFAIL"


def test_completeness_metrics_readonly_uninitialized(isolated_db):
    """Readonly store against empty/uninitialized database returns graceful defaults."""
    store = PaperAccountStore(db_url=isolated_db, readonly=True)
    m = store.get_bridge_completeness_metrics()
    assert m["total_closed_trades"] == 0
    assert m["completeness_pct"] == 100.0


# ===========================================================================
# Group 3: Multi-leg and Roll Executions
# ===========================================================================

def test_multi_leg_option_fill_lifecycle_and_bridge(isolated_db, monkeypatch):
    """Stress test multi-leg fills:

    1. Open a 2-leg Bull Call Spread via apply_multi_leg_fill.
    2. Verify each leg receives its own unique PaperEntrySnapshot with accurate side and conviction.
    3. Close both legs via apply_multi_leg_fill with bridge enabled.
    4. Verify both legs record closed trades, link to opening snapshots, and bridge to TransactionsStore.
    """
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    call_long = "SPY 260918C00500000"
    call_short = "SPY 260918C00510000"

    open_legs = [
        {"symbol": call_long, "side": "buy", "qty": 2, "fill_price": 10.0},
        {"symbol": call_short, "side": "sell", "qty": 2, "fill_price": 4.0},
    ]

    open_ok = store.apply_multi_leg_fill(
        client_order_id="ml_spread_open",
        symbol="SPY",
        strategy_name="bull_call_spread",
        contracts=2,
        legs=open_legs,
        net_cash_impact=-12.0,  # Debit: (10.0 - 4.0) * 2 = 12.0
        commission_and_fees=1.30,
        strategy_id="strat_options",
        provenance="signal_driven",
        provenance_tag="pilot:spread_pilot",
        conviction=0.78,
        macro_regime="BULL",
        signal_score=2.2,
    )
    assert open_ok is True

    # Check positions in PaperPosition table
    with store.Session() as s:
        positions = s.query(PaperPosition).all()
        assert len(positions) == 2
        pos_long = next(p for p in positions if p.symbol == call_long)
        pos_short = next(p for p in positions if p.symbol == call_short)

        assert pos_long.qty == 2
        assert pos_short.qty == -2
        assert pos_long.entry_snapshot_id is not None
        assert pos_short.entry_snapshot_id is not None
        assert pos_long.entry_snapshot_id != pos_short.entry_snapshot_id
        snap_long_id = pos_long.entry_snapshot_id
        snap_short_id = pos_short.entry_snapshot_id

    # Verify snapshots
    snap_long = store.get_entry_snapshot(snap_long_id)
    snap_short = store.get_entry_snapshot(snap_short_id)

    assert snap_long["side"] == "buy"
    assert snap_long["conviction"] == pytest.approx(0.78)
    assert snap_short["side"] == "sell"
    assert snap_short["conviction"] == pytest.approx(0.78)

    # Now close both legs via multi_leg_fill
    close_legs = [
        {"symbol": call_long, "side": "sell", "qty": 2, "fill_price": 15.0},
        {"symbol": call_short, "side": "buy", "qty": 2, "fill_price": 6.0},
    ]

    close_ok = store.apply_multi_leg_fill(
        client_order_id="ml_spread_close",
        symbol="SPY",
        strategy_name="bull_call_spread",
        contracts=2,
        legs=close_legs,
        net_cash_impact=18.0,  # Credit: (15.0 - 6.0) * 2 = 18.0
        commission_and_fees=1.30,
        strategy_id="strat_options",
    )
    assert close_ok is True

    # Both positions must be closed
    with store.Session() as s:
        assert s.query(PaperPosition).count() == 0

    # Both closed trades must exist and be bridged
    closed_long = store.get_full_closed_trades(call_long)
    closed_short = store.get_full_closed_trades(call_short)
    assert len(closed_long) == 1
    assert len(closed_short) == 1

    t_long = closed_long[0]
    t_short = closed_short[0]

    assert t_long["bridge_status"] == "bridged"
    assert t_long["entry_snapshot_id"] == snap_long["snapshot_id"]
    assert t_short["bridge_status"] == "bridged"
    assert t_short["entry_snapshot_id"] == snap_short["snapshot_id"]

    # Verify TransactionsStore rows
    ts = transactions_store.TransactionsStore(db_url=isolated_db)
    df = ts.closed_trades_df()
    assert len(df) == 2

    # Check side and conviction forwarding
    row_l = df[df["symbol"] == call_long].iloc[0]
    assert row_l["side"] == "long"
    assert row_l["conviction"] == pytest.approx(0.78)
    assert row_l["entry_price"] == pytest.approx(10.0)
    assert row_l["exit_price"] == pytest.approx(15.0)

    row_s = df[df["symbol"] == call_short].iloc[0]
    assert row_s["side"] == "short"
    assert row_s["conviction"] == pytest.approx(0.78)
    assert row_s["entry_price"] == pytest.approx(4.0)
    assert row_s["exit_price"] == pytest.approx(6.0)


def test_roll_fill_closes_old_and_opens_new_with_distinct_snapshots(isolated_db, monkeypatch):
    """Stress test apply_roll_fill:

    1. Open initial option position with conviction 0.85 (snap_orig).
    2. Roll position via apply_roll_fill with conviction 0.60 (snap_rolled).
    3. Verify closed trade preserves snap_orig and bridges with conviction 0.85.
    4. Verify new rolled position receives snap_rolled with conviction 0.60.
    5. Close the rolled position; verify it bridges with conviction 0.60.
    """
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    old_sym = "TSLA 260918C00200000"
    new_sym = "TSLA 261016C00220000"

    # 1. Open initial position
    open_ok = store.apply_fill(
        client_order_id="tsla_open_init",
        symbol=old_sym,
        side="buy",
        qty=1.0,
        fill_price=12.0,
        strategy_id="strat_roll_test",
        provenance="signal_driven",
        conviction=0.85,
        macro_regime="BULL",
    )
    assert open_ok is True

    with store.Session() as s:
        init_pos = s.query(PaperPosition).filter_by(symbol=old_sym).first()
        assert init_pos is not None
        snap_orig_id = init_pos.entry_snapshot_id
    assert snap_orig_id is not None

    # 2. Execute Roll
    roll_ok = store.apply_roll_fill(
        client_order_id="tsla_roll_ord",
        symbol="TSLA",
        close_legs=[{"symbol": old_sym, "side": "sell", "qty": 1, "fill_price": 16.0}],
        open_legs=[{"symbol": new_sym, "side": "buy", "qty": 1, "fill_price": 10.0}],
        net_cash_impact=5.35,  # Credit from closing higher than opening new
        commission_and_fees=1.30,
        strategy_id="strat_roll_test",
        provenance="signal_driven",
        conviction=0.60,
        macro_regime="CHOPPY",
    )
    assert roll_ok is True

    # 3. Check closed trade on old leg
    closed_old = store.get_full_closed_trades(old_sym)
    assert len(closed_old) == 1
    t_old = closed_old[0]
    assert t_old["close_reason"] == "roll"
    assert t_old["entry_snapshot_id"] == snap_orig_id
    assert t_old["bridge_status"] == "bridged"

    # Check new position on rolled leg
    with store.Session() as s:
        new_pos = s.query(PaperPosition).filter_by(symbol=new_sym).first()
        assert new_pos is not None
        snap_new_id = new_pos.entry_snapshot_id
    assert snap_new_id is not None
    assert snap_new_id != snap_orig_id

    # Verify new snapshot
    snap_new = store.get_entry_snapshot(snap_new_id)
    assert snap_new["conviction"] == pytest.approx(0.60)
    assert snap_new["macro_regime"] == "CHOPPY"

    # 4. Check TransactionsStore for old trade: must have original conviction 0.85!
    ts = transactions_store.TransactionsStore(db_url=isolated_db)
    df = ts.closed_trades_df()
    assert len(df) == 1
    old_row = df.iloc[0]
    assert old_row["symbol"] == old_sym
    assert old_row["conviction"] == pytest.approx(0.85)

    # 5. Close the rolled position
    close_rolled_ok = store.apply_fill(
        client_order_id="tsla_close_rolled",
        symbol=new_sym,
        side="sell",
        qty=1.0,
        fill_price=14.0,
        strategy_id="strat_roll_test",
    )
    assert close_rolled_ok is True

    # Check second bridged trade: must have roll conviction 0.60!
    df2 = ts.closed_trades_df()
    assert len(df2) == 2
    new_row = df2[df2["symbol"] == new_sym].iloc[0]
    assert new_row["conviction"] == pytest.approx(0.60)
    assert new_row["entry_price"] == pytest.approx(10.0)
    assert new_row["exit_price"] == pytest.approx(14.0)


def test_multi_leg_roll_spread_lifecycle(isolated_db, monkeypatch):
    """Stress test rolling a 2-leg vertical spread (2 close legs, 2 open legs)."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    c1_old = "NVDA 260918C00100000"
    c2_old = "NVDA 260918C00110000"
    c1_new = "NVDA 261016C00105000"
    c2_new = "NVDA 261016C00115000"

    # 1. Open original spread with conviction 0.82
    store.apply_multi_leg_fill(
        client_order_id="nvda_spread_open",
        symbol="NVDA",
        strategy_name="bull_spread",
        contracts=1,
        legs=[
            {"symbol": c1_old, "side": "buy", "qty": 1, "fill_price": 10.0},
            {"symbol": c2_old, "side": "sell", "qty": 1, "fill_price": 5.0},
        ],
        net_cash_impact=-5.0,
        commission_and_fees=1.30,
        strategy_id="strat_spread",
        provenance="signal_driven",
        conviction=0.82,
    )

    # 2. Roll entire spread into new month with conviction 0.71
    roll_ok = store.apply_roll_fill(
        client_order_id="nvda_spread_roll",
        symbol="NVDA",
        close_legs=[
            {"symbol": c1_old, "side": "sell", "qty": 1, "fill_price": 12.0},
            {"symbol": c2_old, "side": "buy", "qty": 1, "fill_price": 6.0},
        ],
        open_legs=[
            {"symbol": c1_new, "side": "buy", "qty": 1, "fill_price": 9.0},
            {"symbol": c2_new, "side": "sell", "qty": 1, "fill_price": 4.5},
        ],
        net_cash_impact=1.0,
        commission_and_fees=2.60,
        strategy_id="strat_spread",
        provenance="signal_driven",
        conviction=0.71,
    )
    assert roll_ok is True

    # Verify both old legs are closed and bridged with conviction 0.82
    ts = transactions_store.TransactionsStore(db_url=isolated_db)
    df_closed = ts.closed_trades_df()
    assert len(df_closed) == 2
    for _, row in df_closed.iterrows():
        assert row["conviction"] == pytest.approx(0.82)

    # Verify both new legs exist in open positions with new snapshots
    with store.Session() as s:
        open_pos = s.query(PaperPosition).all()
        assert len(open_pos) == 2
        for pos in open_pos:
            snap = store.get_entry_snapshot(pos.entry_snapshot_id)
            assert snap["conviction"] == pytest.approx(0.71)


def test_historical_roll_and_multi_leg_no_fabrication(isolated_db, monkeypatch):
    """Anti-fabrication: multi-leg and roll fills without metadata preserve NULL snapshot IDs."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db)

    # 1. Plain multi-leg without metadata
    open_ok = store.apply_multi_leg_fill(
        client_order_id="plain_ml",
        symbol="QQQ",
        strategy_name="plain_spread",
        contracts=1,
        legs=[
            {"symbol": "QQQ 260918C00400000", "side": "buy", "qty": 1, "fill_price": 10.0},
        ],
        net_cash_impact=-10.0,
        commission_and_fees=0.65,
    )
    assert open_ok is True

    with store.Session() as s:
        pos = s.query(PaperPosition).filter_by(symbol="QQQ 260918C00400000").first()
        assert pos.entry_snapshot_id is None, "Plain multi-leg fill must not fabricate snapshot ID"

    # 2. Plain roll without metadata
    roll_ok = store.apply_roll_fill(
        client_order_id="plain_roll",
        symbol="QQQ",
        close_legs=[{"symbol": "QQQ 260918C00400000", "side": "sell", "qty": 1, "fill_price": 12.0}],
        open_legs=[{"symbol": "QQQ 261016C00410000", "side": "buy", "qty": 1, "fill_price": 8.0}],
    )
    assert roll_ok is True

    # Closed trade on QQQ 260918C00400000 must have None snapshot
    closed = store.get_full_closed_trades("QQQ 260918C00400000")[0]
    assert closed["entry_snapshot_id"] is None

    # New position must also have None snapshot
    with store.Session() as s:
        new_pos = s.query(PaperPosition).filter_by(symbol="QQQ 261016C00410000").first()
        assert new_pos.entry_snapshot_id is None

        # Total snapshots in DB must be exactly ZERO
        snap_count = s.query(PaperEntrySnapshot).count()
        assert snap_count == 0, "No snapshots should have been fabricated"

