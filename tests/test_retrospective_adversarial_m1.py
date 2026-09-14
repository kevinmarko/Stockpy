"""tests/test_retrospective_adversarial_m1.py
============================================
Adversarial Stress Test Suite for Milestone 1:
- WP-C Stress: Empirical verification of forward-only snapshot capture & anti-fabrication
- WP-D Stress: Complex bridge failure mechanics, SAVEPOINT rollback, error truncation, and completeness metrics
- Execution Stress: High-frequency rapid fills, flip-through-zero in both directions, and averaging-in
"""

import os
import time
import uuid
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import inspect, text

from settings import settings
import database_setup
from data.paper_account_store import (
    PaperAccountStore,
    PaperPosition,
    PaperClosedTrade,
    PaperEntrySnapshot,
)
from transactions_store import TransactionsStore


@pytest.fixture
def isolated_db_url(tmp_path):
    """Provides a fresh file-backed SQLite database isolated per test."""
    db_file = tmp_path / f"test_retro_adv_m1_{uuid.uuid4().hex}.db"
    return f"sqlite:///{db_file}"


def get_position(store: PaperAccountStore, symbol: str):
    """Helper to query PaperPosition directly from database."""
    with store.Session() as session:
        return session.query(PaperPosition).filter_by(symbol=symbol.upper()).first()


# ===========================================================================
# 1. WP-C: Forward-Only Snapshot Capture & Anti-Fabrication Tests
# ===========================================================================

class TestAdversarialSnapshotCapture:
    """Stress tests challenging forward-only snapshot capture and anti-fabrication."""

    def test_plain_fill_refusal_across_all_fill_methods(self, isolated_db_url):
        """Plain/legacy fills lacking decision context must NEVER capture snapshots."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # 1. Plain apply_fill
        assert store.apply_fill("ord_plain_1", "AAPL", "buy", 10.0, 150.0)
        pos = get_position(store, "AAPL")
        assert pos is not None
        assert pos.entry_snapshot_id is None, "Plain fill must NOT generate an entry_snapshot_id"

        # Close plain position
        assert store.apply_fill("ord_plain_2", "AAPL", "sell", 10.0, 155.0)
        closed = store.get_full_closed_trades(limit=5)
        assert len(closed) == 1
        assert closed[0]["entry_snapshot_id"] is None
        assert store.get_entry_snapshot(closed[0]["entry_snapshot_id"]) is None

        # 2. Plain apply_multi_leg_fill
        legs = [
            {"symbol": "AAPL 2026-12-18 $160 CALL", "side": "buy", "ratio": 1, "price": 5.0, "qty": 1, "fill_price": 5.0},
        ]
        assert store.apply_multi_leg_fill("ord_mleg_plain", "AAPL", "single_call", 1, legs, net_cash_impact=-500.0, commission_and_fees=0.0)
        pos_opt = get_position(store, "AAPL 2026-12-18 $160 CALL")
        assert pos_opt is not None
        assert pos_opt.entry_snapshot_id is None, "Plain multi-leg fill must NOT generate entry_snapshot_id"

        # 3. Plain apply_roll_fill
        close_legs = [
            {"symbol": "AAPL 2026-12-18 $160 CALL", "side": "sell", "qty": 1, "fill_price": 6.0},
        ]
        open_legs = [
            {"symbol": "AAPL 2027-01-15 $165 CALL", "side": "buy", "qty": 1, "fill_price": 4.0},
        ]
        assert store.apply_roll_fill("ord_roll_plain", "AAPL", close_legs, open_legs, net_cash_impact=200.0, commission_and_fees=0.0)
        pos_rolled = get_position(store, "AAPL 2027-01-15 $165 CALL")
        assert pos_rolled is not None
        assert pos_rolled.entry_snapshot_id is None, "Plain roll fill must NOT generate entry_snapshot_id"

    def test_attempt_to_trick_snapshot_with_empty_or_deceptive_metadata(self, isolated_db_url):
        """Attempt to fool store into snapshot capture using empty or falsy kwargs."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # Deceptive kwargs: None or untagged
        deceptive_attempts = [
            {"strategy_id": "untagged", "pilot_id": None, "provenance": None},
            {"strategy_id": "", "pilot_id": None, "provenance": None},
            {"strategy_id": None, "pilot_id": None, "provenance": None},
            {"strategy_id": "untagged", "conviction": None, "macro_regime": None},
        ]

        for idx, kwargs in enumerate(deceptive_attempts):
            sym = f"SYM{idx}"
            coid = f"ord_trick_{idx}"
            assert store.apply_fill(coid, sym, "buy", 5.0, 100.0, **kwargs)
            pos = get_position(store, sym)
            assert pos is not None
            assert pos.entry_snapshot_id is None, f"Deceptive fill {kwargs} must not create snapshot"

    def test_manual_trade_strictly_strips_synthetic_conviction_and_forecast(self, isolated_db_url):
        """Anti-fabrication: Manual trades must NEVER retain conviction or forecast scores."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # Adversarial attempt: caller tags manual trade but tries to pass high conviction and forecast
        assert store.apply_fill(
            "ord_manual_adv",
            "TSLA",
            "buy",
            10.0,
            200.0,
            provenance="manual",
            provenance_tag="manual:discretionary",
            conviction=0.99,
            raw_forecast=0.85,
            signal_score=0.92,
            macro_regime="BULL_TREND",
        )
        pos = get_position(store, "TSLA")
        assert pos is not None
        assert pos.entry_snapshot_id is not None

        snap = store.get_entry_snapshot(pos.entry_snapshot_id)
        assert snap is not None
        assert snap["provenance"] == "manual"
        # STRICT ANTI-FABRICATION VERIFICATION:
        assert snap["conviction"] is None, "Manual trade must NOT retain conviction"
        assert snap["raw_forecast"] is None, "Manual trade must NOT retain raw_forecast"
        assert snap["signal_score"] is None, "Manual trade must NOT retain signal_score"
        # Non-signal context like macro_regime is preserved
        assert snap["macro_regime"] == "BULL_TREND"

    def test_historical_unmigrated_database_returns_none_without_hallucination(self, isolated_db_url):
        """Simulate pre-existing database rows with NULL entry_snapshot_id."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # Directly insert a pre-existing closed trade row with NULL entry_snapshot_id
        with store.Session() as session:
            historical_trade = PaperClosedTrade(
                trade_id=9999,
                strategy_id="legacy_strat",
                symbol="MSFT",
                side="buy",
                qty=100.0,
                entry_ts=datetime(2025, 1, 1, 10, 0, 0),
                entry_price=400.0,
                exit_ts=datetime(2025, 1, 10, 16, 0, 0),
                exit_price=420.0,
                realized_pnl=2000.0,
                realized_pnl_pct=0.05,
                holding_period_days=9.25,
                close_reason="target_hit",
                entry_snapshot_id=None,
                bridge_status="disabled",
            )
            session.add(historical_trade)
            session.commit()

        # Query back via public methods
        closed_trades = store.get_full_closed_trades(limit=10)
        hist_row = next((t for t in closed_trades if t["trade_id"] == 9999), None)
        assert hist_row is not None
        assert hist_row["entry_snapshot_id"] is None

        # Verify get_entry_snapshot strictly returns None (never fabricates or guesses)
        assert store.get_entry_snapshot(hist_row["entry_snapshot_id"]) is None
        assert store.get_entry_snapshot(None) is None
        assert store.get_entry_snapshot("") is None
        assert store.get_entry_snapshot("non_existent_uuid_12345") is None


# ===========================================================================
# 2. WP-D: Bridge Failure Mechanics & Telemetry Integrity Tests
# ===========================================================================

class TestAdversarialBridgeMechanics:
    """Stress tests challenging bridge fail-open invariants and completeness metrics."""

    def test_bridge_disabled_produces_disabled_status(self, monkeypatch, isolated_db_url):
        """When bridge is disabled, closed trades receive bridge_status='disabled'."""
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", False)
        store = PaperAccountStore(db_url=isolated_db_url)

        store.apply_fill("b_dis_1", "NVDA", "buy", 10.0, 120.0)
        store.apply_fill("b_dis_2", "NVDA", "sell", 10.0, 130.0)

        closed = store.get_full_closed_trades(limit=1)
        assert len(closed) == 1
        assert closed[0]["bridge_status"] == "disabled"
        assert closed[0]["bridged_trade_id"] is None
        assert closed[0]["bridge_error"] is None

        metrics = store.get_bridge_completeness_metrics()
        assert metrics["bridge_enabled"] is False
        assert metrics["status"] == "disabled"
        assert metrics["disabled_count"] == 1
        assert metrics["attempted_count"] == 0
        assert metrics["completeness_pct"] == 100.0

    def test_bridge_uninitialized_companion_fails_open_and_logs(self, monkeypatch, isolated_db_url):
        """When bridge is enabled but companion store is None, fail-open and mark failed."""
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db_url)
        store._transactions_store = None  # force companion None

        starting_cash = store.get_account().cash
        assert store.apply_fill("b_uninit_1", "NVDA", "buy", 10.0, 100.0)
        assert store.apply_fill("b_uninit_2", "NVDA", "sell", 10.0, 110.0)

        # Position should be closed, cash updated (1000 cost basis out, 1100 proceeds in -> +100 cash)
        assert get_position(store, "NVDA") is None
        assert store.get_account().cash == pytest.approx(starting_cash + 100.0)

        closed = store.get_full_closed_trades(limit=1)
        assert len(closed) == 1
        assert closed[0]["bridge_status"] == "failed"
        assert "companion store is None" in closed[0]["bridge_error"]

        metrics = store.get_bridge_completeness_metrics()
        assert metrics["status"] == "degraded"
        assert metrics["failed_count"] == 1
        assert metrics["bridged_count"] == 0
        assert metrics["completeness_pct"] == 0.0
        assert metrics["last_failure"] is not None
        assert metrics["last_failure"]["trade_id"] == closed[0]["trade_id"]

    def test_catastrophic_database_locked_fails_open_and_truncates_error(self, monkeypatch, isolated_db_url):
        """Bridge OperationalError ('database locked') fails open and truncates error to <=500 chars."""
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db_url)

        huge_error_msg = "sqlite3.OperationalError: database is locked! " + ("X" * 2000)
        monkeypatch.setattr(
            store._transactions_store,
            "record_trade",
            MagicMock(side_effect=sqlite3.OperationalError(huge_error_msg)),
        )

        assert store.apply_fill("b_lock_1", "AMZN", "buy", 5.0, 180.0)
        # Should not raise; outer trade succeeds
        assert store.apply_fill("b_lock_2", "AMZN", "sell", 5.0, 190.0)

        closed = store.get_full_closed_trades(limit=1)
        assert len(closed) == 1
        assert closed[0]["bridge_status"] == "failed"
        assert len(closed[0]["bridge_error"]) <= 500
        assert "database is locked" in closed[0]["bridge_error"]

    def test_savepoint_isolation_prevents_orphaned_transactions_trade(self, monkeypatch, isolated_db_url):
        """If close_trade raises exception after record_trade succeeded, SAVEPOINT rolls back both."""
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db_url)

        # Make record_trade succeed, but close_trade fail
        monkeypatch.setattr(
            store._transactions_store,
            "close_trade",
            MagicMock(side_effect=RuntimeError("close_trade crashed violently")),
        )

        assert store.apply_fill("b_iso_1", "META", "buy", 10.0, 500.0)
        assert store.apply_fill("b_iso_2", "META", "sell", 10.0, 520.0)

        # Paper trade succeeded
        assert get_position(store, "META") is None
        closed = store.get_full_closed_trades(limit=1)
        assert len(closed) == 1
        assert closed[0]["bridge_status"] == "failed"
        assert "close_trade crashed violently" in closed[0]["bridge_error"]

        # CRITICAL VERIFICATION: No orphaned trade should exist in transactions_store!
        # Because session.begin_nested() rolled back the SAVEPOINT upon exception.
        with store.Session() as s:
            t_rows = s.execute(text("SELECT count(*) FROM trades WHERE symbol = 'META'")).scalar()
            assert t_rows == 0, "SAVEPOINT rollback must have undone the record_trade call"

    def test_bridge_completeness_metric_exact_ratios_under_stress(self, monkeypatch, isolated_db_url):
        """Stress-test completeness metric under mixed sequence of successful, failed, and disabled bridges."""
        # Step 1: 3 trades while bridge is DISABLED
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", False)
        store = PaperAccountStore(db_url=isolated_db_url)

        for i in range(3):
            store.apply_fill(f"d_{i}_1", "SPY", "buy", 1.0, 500.0)
            store.apply_fill(f"d_{i}_2", "SPY", "sell", 1.0, 505.0)

        m1 = store.get_bridge_completeness_metrics()
        assert m1["total_closed_trades"] == 3
        assert m1["disabled_count"] == 3
        assert m1["bridged_count"] == 0
        assert m1["failed_count"] == 0
        assert m1["completeness_pct"] == 100.0
        assert m1["status"] == "disabled"

        # Step 2: 4 successful bridged trades on enabled store
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store_enabled = PaperAccountStore(db_url=isolated_db_url)

        for i in range(4):
            store_enabled.apply_fill(f"s_{i}_1", "SPY", "buy", 1.0, 500.0)
            store_enabled.apply_fill(f"s_{i}_2", "SPY", "sell", 1.0, 505.0)

        m2 = store_enabled.get_bridge_completeness_metrics()
        assert m2["total_closed_trades"] == 7
        assert m2["disabled_count"] == 3
        assert m2["bridged_count"] == 4
        assert m2["failed_count"] == 0
        assert m2["attempted_count"] == 4
        assert m2["completeness_pct"] == 100.0
        assert m2["status"] == "healthy"

        # Step 3: 2 failed bridged trades (forced exception)
        monkeypatch.setattr(
            store_enabled._transactions_store,
            "record_trade",
            MagicMock(side_effect=RuntimeError("Forced failure")),
        )
        for i in range(2):
            store_enabled.apply_fill(f"f_{i}_1", "SPY", "buy", 1.0, 500.0)
            store_enabled.apply_fill(f"f_{i}_2", "SPY", "sell", 1.0, 505.0)

        m3 = store_enabled.get_bridge_completeness_metrics()
        assert m3["total_closed_trades"] == 9
        assert m3["disabled_count"] == 3
        assert m3["bridged_count"] == 4
        assert m3["failed_count"] == 2
        assert m3["attempted_count"] == 6  # 4 bridged + 2 failed
        # 4 / 6 = 66.67%
        assert m3["completeness_pct"] == 66.67
        assert m3["status"] == "degraded"
        assert m3["last_failure"] is not None
        assert "Forced failure" in m3["last_failure"]["error"]


# ===========================================================================
# 3. High-Frequency Rapid Fills, Flip-Through-Zero, and Averaging-In
# ===========================================================================

class TestAdversarialFillMechanics:
    """Stress tests for averaging-in, flip-through-zero, and rapid execution sequences."""

    def test_averaging_in_strictly_preserves_inception_snapshot_and_entry_ts(self, isolated_db_url):
        """Averaging into an existing position must NOT overwrite snapshot or entry_ts."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # Initial open with decision snapshot A
        assert store.apply_fill(
            "avg_open_1",
            "GOOG",
            "buy",
            10.0,
            100.0,
            strategy_id="strat_alpha",
            provenance="signal_driven",
            provenance_tag="pilot:p1",
            conviction=0.85,
            signal_score=0.75,
        )
        pos = get_position(store, "GOOG")
        snap_a_id = pos.entry_snapshot_id
        entry_ts_initial = pos.entry_ts
        assert snap_a_id is not None

        # Add to position with different adversarial parameters (trying to mutate context)
        time.sleep(0.01)
        assert store.apply_fill(
            "avg_add_2",
            "GOOG",
            "buy",
            10.0,
            120.0,
            strategy_id="strat_alpha",
            provenance="signal_driven",
            provenance_tag="pilot:p2_hijack",
            conviction=0.20,
            signal_score=0.10,
        )

        pos_updated = get_position(store, "GOOG")
        assert pos_updated.qty == 20.0
        assert pos_updated.avg_entry_price == 110.0
        # INCEPTION PRESERVATION INVARIANT:
        assert pos_updated.entry_snapshot_id == snap_a_id, "Averaging in must preserve snapshot ID"
        assert pos_updated.entry_ts == entry_ts_initial, "Averaging in must preserve entry_ts"

        # Partial close (10 shares): closed trade gets snapshot A
        assert store.apply_fill("avg_close_part", "GOOG", "sell", 10.0, 130.0, strategy_id="strat_alpha")
        closed = store.get_full_closed_trades(limit=5)
        assert len(closed) == 1
        assert closed[0]["entry_snapshot_id"] == snap_a_id

        # Remaining position still has snapshot A
        pos_rem = get_position(store, "GOOG")
        assert pos_rem.qty == 10.0
        assert pos_rem.entry_snapshot_id == snap_a_id

        # Final close
        assert store.apply_fill("avg_close_final", "GOOG", "sell", 10.0, 140.0, strategy_id="strat_alpha")
        closed_all = store.get_full_closed_trades(limit=5)
        assert len(closed_all) == 2
        # Both closed parts must carry the inception snapshot
        assert closed_all[0]["entry_snapshot_id"] == snap_a_id
        assert closed_all[1]["entry_snapshot_id"] == snap_a_id

    def test_flip_through_zero_long_to_short_clean_snapshot_lineage(self, isolated_db_url):
        """Flipping through zero from long to short must close long with snap A and create snap B for short."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # 1. Open Long 10 shares @ 100 with Snapshot A
        assert store.apply_fill(
            "flip_ls_1",
            "AMD",
            "buy",
            10.0,
            100.0,
            strategy_id="momentum",
            provenance="signal_driven",
            conviction=0.90,
            signal_score=0.80,
        )
        snap_a_id = get_position(store, "AMD").entry_snapshot_id
        assert snap_a_id is not None

        # 2. Flip through zero: Sell 25 shares @ 120 (closes 10 long, opens 15 short) with Snapshot B
        time.sleep(0.01)
        assert store.apply_fill(
            "flip_ls_2",
            "AMD",
            "sell",
            25.0,
            120.0,
            allow_short=True,
            strategy_id="momentum",
            provenance="signal_driven",
            conviction=0.45,
            signal_score=-0.60,
        )

        # Verify closed long trade
        closed = store.get_full_closed_trades(limit=5)
        assert len(closed) == 1
        long_closed = closed[0]
        assert long_closed["side"].lower() == "buy"
        assert long_closed["qty"] == 10.0
        assert long_closed["entry_price"] == 100.0
        assert long_closed["exit_price"] == 120.0
        assert long_closed["realized_pnl"] == 200.0
        assert long_closed["entry_snapshot_id"] == snap_a_id, "Long close must have snapshot A"

        # Verify new short position
        pos_short = get_position(store, "AMD")
        assert pos_short.qty == -15.0
        assert pos_short.avg_entry_price == 120.0
        snap_b_id = pos_short.entry_snapshot_id
        assert snap_b_id is not None
        assert snap_b_id != snap_a_id, "Flipped short position must have a distinct new snapshot B"

        snap_b = store.get_entry_snapshot(snap_b_id)
        assert snap_b["side"] == "sell"
        assert snap_b["qty"] == 15.0
        assert snap_b["entry_price"] == 120.0
        assert snap_b["conviction"] == 0.45

        # 3. Close the short position: Buy 15 shares @ 110
        assert store.apply_fill(
            "flip_ls_3",
            "AMD",
            "buy",
            15.0,
            110.0,
            strategy_id="momentum",
        )
        assert get_position(store, "AMD") is None

        closed_all = store.get_full_closed_trades(limit=5)
        assert len(closed_all) == 2
        # Most recent closed trade is the short close
        short_closed = closed_all[0]
        assert short_closed["side"].lower() == "sell"
        assert short_closed["qty"] == 15.0
        assert short_closed["entry_price"] == 120.0
        assert short_closed["exit_price"] == 110.0
        assert short_closed["realized_pnl"] == 150.0
        assert short_closed["entry_snapshot_id"] == snap_b_id, "Short close must have snapshot B"

    def test_flip_through_zero_short_to_long_clean_snapshot_lineage(self, isolated_db_url):
        """Flipping through zero from short to long must close short with snap C and create snap D for long."""
        store = PaperAccountStore(db_url=isolated_db_url)

        # 1. Open Short 20 shares @ 200 with Snapshot C
        assert store.apply_fill(
            "flip_sl_1",
            "NFLX",
            "sell",
            20.0,
            200.0,
            allow_short=True,
            strategy_id="reversal",
            provenance="signal_driven",
            conviction=0.75,
            signal_score=-0.50,
        )
        snap_c_id = get_position(store, "NFLX").entry_snapshot_id
        assert snap_c_id is not None

        # 2. Flip through zero: Buy 35 shares @ 180 (closes 20 short, opens 15 long) with Snapshot D
        time.sleep(0.01)
        assert store.apply_fill(
            "flip_sl_2",
            "NFLX",
            "buy",
            35.0,
            180.0,
            strategy_id="reversal",
            provenance="signal_driven",
            conviction=0.88,
            signal_score=0.90,
        )

        closed = store.get_full_closed_trades(limit=5)
        assert len(closed) == 1
        short_closed = closed[0]
        assert short_closed["side"].lower() == "sell"
        assert short_closed["qty"] == 20.0
        assert short_closed["realized_pnl"] == 400.0
        assert short_closed["entry_snapshot_id"] == snap_c_id

        # Verify new long position
        pos_long = get_position(store, "NFLX")
        assert pos_long.qty == 15.0
        assert pos_long.avg_entry_price == 180.0
        snap_d_id = pos_long.entry_snapshot_id
        assert snap_d_id is not None
        assert snap_d_id != snap_c_id

        snap_d = store.get_entry_snapshot(snap_d_id)
        assert snap_d["side"] == "buy"
        assert snap_d["qty"] == 15.0
        assert snap_d["entry_price"] == 180.0
        assert snap_d["conviction"] == 0.88

        # 3. Close the long position
        assert store.apply_fill("flip_sl_3", "NFLX", "sell", 15.0, 195.0, strategy_id="reversal")
        assert get_position(store, "NFLX") is None

        closed_all = store.get_full_closed_trades(limit=5)
        assert len(closed_all) == 2
        long_closed = closed_all[0]
        assert long_closed["side"].lower() == "buy"
        assert long_closed["qty"] == 15.0
        assert long_closed["entry_snapshot_id"] == snap_d_id

    def test_rapid_alternating_execution_sequence_stress_harness(self, monkeypatch, isolated_db_url):
        """Stress-test rapid alternating fills: averaging in, partial closes, flips, and closes."""
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db_url)

        initial_cash = store.get_account().cash
        expected_closed_count = 0

        # Cycle 5 times through a complex 7-step state machine:
        # Step 1: Open long 10 @ 100
        # Step 2: Average in 10 @ 110 (pos=20 @ 105)
        # Step 3: Partial close 5 @ 120 (pos=15 @ 105, closed=1)
        # Step 4: Flip through zero to short: sell 25 @ 130 (closed 15 long, opened 10 short @ 130, closed=2)
        # Step 5: Average In Short (sell 10)
        # Step 6: Flip back to long: buy 30 @ 125 (closed 20 short, opened 10 long @ 125, closed=3)
        # Step 7: Flatten Long (sell 10)
        for cycle in range(5):
            sym = f"CYC{cycle}"

            # Step 1: Open Long
            assert store.apply_fill(
                f"c{cycle}_s1", sym, "buy", 10.0, 100.0,
                strategy_id="stress_strat", provenance="signal_driven", conviction=0.8,
            )

            # Step 2: Average In Long
            assert store.apply_fill(
                f"c{cycle}_s2", sym, "buy", 10.0, 110.0,
                strategy_id="stress_strat", provenance="signal_driven", conviction=0.5,
            )

            # Step 3: Partial Close Long
            assert store.apply_fill(
                f"c{cycle}_s3", sym, "sell", 5.0, 120.0, strategy_id="stress_strat",
            )
            expected_closed_count += 1

            # Step 4: Flip to Short (sell 25)
            assert store.apply_fill(
                f"c{cycle}_s4", sym, "sell", 25.0, 130.0,
                allow_short=True, strategy_id="stress_strat", provenance="signal_driven", conviction=0.7,
            )
            expected_closed_count += 1

            # Step 5: Average In Short (sell 10)
            assert store.apply_fill(
                f"c{cycle}_s5", sym, "sell", 10.0, 140.0,
                allow_short=True, strategy_id="stress_strat", provenance="signal_driven", conviction=0.4,
            )

            # Step 6: Flip to Long (buy 30)
            assert store.apply_fill(
                f"c{cycle}_s6", sym, "buy", 30.0, 125.0,
                strategy_id="stress_strat", provenance="signal_driven", conviction=0.9,
            )
            expected_closed_count += 1

            # Step 7: Flatten Long (sell 10)
            assert store.apply_fill(
                f"c{cycle}_s7", sym, "sell", 10.0, 130.0, strategy_id="stress_strat",
            )
            expected_closed_count += 1

            # Position must be completely flat
            assert get_position(store, sym) is None

        # Verify all closed trades landed
        closed_all = store.get_full_closed_trades(limit=100)
        assert len(closed_all) == expected_closed_count == 20

        # Verify cash conservation:
        # Final Cash == Initial Cash + Sum(Realized PnL) - Sum(Commissions)
        total_realized_pnl = sum(t["realized_pnl"] for t in closed_all)
        total_commissions = sum(t.get("commission", 0.0) or 0.0 for t in closed_all)
        final_cash = store.get_account().cash
        assert final_cash == pytest.approx(initial_cash + total_realized_pnl - total_commissions)

        # Verify bridge metrics: all 20 closed trades bridged successfully
        metrics = store.get_bridge_completeness_metrics()
        assert metrics["total_closed_trades"] == 20
        assert metrics["bridged_count"] == 20
        assert metrics["failed_count"] == 0
        assert metrics["completeness_pct"] == 100.0
        assert metrics["status"] == "healthy"

    def test_multi_leg_fill_flip_through_zero_and_snapshot_preservation(self, isolated_db_url):
        """Multi-leg fills flipping an option leg through zero must generate new snapshot for flipped portion."""
        store = PaperAccountStore(db_url=isolated_db_url)
        sym = "SPY 2026-12-18 $500 CALL"

        # 1. Open short 2 contracts of option with Snapshot ML1
        legs_short = [
            {"symbol": sym, "side": "sell", "qty": 2, "fill_price": 10.0},
        ]
        assert store.apply_multi_leg_fill(
            "ml_short_1", "SPY", "single_leg", 2, legs_short,
            net_cash_impact=2000.0, commission_and_fees=0.0,
            provenance="signal_driven", conviction=0.72,
        )
        pos_short = get_position(store, sym)
        assert pos_short is not None
        assert pos_short.qty == -2.0
        snap_ml1 = pos_short.entry_snapshot_id
        assert snap_ml1 is not None

        # 2. Flip through zero to long: buy 5 contracts of option with Snapshot ML2
        legs_buy = [
            {"symbol": sym, "side": "buy", "qty": 5, "fill_price": 12.0},
        ]
        assert store.apply_multi_leg_fill(
            "ml_flip_2", "SPY", "single_leg", 5, legs_buy,
            net_cash_impact=-6000.0, commission_and_fees=0.0,
            provenance="signal_driven", conviction=0.88,
        )

        # Verify closed trade for short
        closed = store.get_full_closed_trades(limit=5)
        assert len(closed) == 1
        assert closed[0]["side"].lower() == "sell"
        assert closed[0]["qty"] == 2.0
        assert closed[0]["entry_snapshot_id"] == snap_ml1

        # Verify flipped position is long 3 contracts with new snapshot ML2
        pos_long = get_position(store, sym)
        assert pos_long.qty == 3.0
        assert pos_long.avg_entry_price == 12.0
        snap_ml2 = pos_long.entry_snapshot_id
        assert snap_ml2 is not None
        assert snap_ml2 != snap_ml1

        snap_ml2_dict = store.get_entry_snapshot(snap_ml2)
        assert snap_ml2_dict["side"] == "buy"
        assert snap_ml2_dict["qty"] == 3.0
        assert snap_ml2_dict["conviction"] == 0.88

    def test_readonly_store_telemetry_safety_and_cold_start(self, isolated_db_url):
        """Readonly store must query existing data safely and gracefully handle empty/cold databases."""
        # Test on fresh/cold DB before table creation
        cold_store = PaperAccountStore(db_url=isolated_db_url, readonly=True)
        assert cold_store.get_entry_snapshot("any_snap_id") is None
        metrics_cold = cold_store.get_bridge_completeness_metrics()
        assert metrics_cold["total_closed_trades"] == 0
        assert metrics_cold["completeness_pct"] == 100.0

        # Now populate database with writable store
        writer = PaperAccountStore(db_url=isolated_db_url)
        assert writer.apply_fill(
            "w_fill_1", "QQQ", "buy", 10.0, 450.0,
            provenance="signal_driven", conviction=0.91,
        )
        snap_id = get_position(writer, "QQQ").entry_snapshot_id
        assert snap_id is not None
        writer.apply_fill("w_fill_2", "QQQ", "sell", 10.0, 460.0)

        # Read back with readonly store
        reader = PaperAccountStore(db_url=isolated_db_url, readonly=True)
        snap = reader.get_entry_snapshot(snap_id)
        assert snap is not None
        assert snap["symbol"] == "QQQ"
        assert snap["conviction"] == 0.91

        # Attempt to write with readonly store must fail
        with pytest.raises(RuntimeError, match="readonly"):
            reader.record_entry_snapshot("QQQ", "strat", datetime.now(timezone.utc), 450.0, "buy", 10.0)
        with pytest.raises(RuntimeError, match="readonly"):
            reader.apply_fill("r_fail", "QQQ", "buy", 10.0, 450.0)

