"""tests/test_retrospective_schema_and_bridge.py
=============================================
Comprehensive unit and integration test suite for Milestone 1 of the Retrospective
Learning Loop:
- Table creation and idempotent migration of paper_entry_snapshots & paper_closed_trades
- Forward-only snapshot capture at trade open (signal_driven, manual, unknown)
- Historical trades lacking snapshot strictly return None / not-captured
- Durable bridge tracking in _record_closed_trade (bridged, failed, disabled)
- Conviction forwarding to TransactionsStore and calibration_curve integration
- Queryable completeness metric: PaperAccountStore.get_bridge_completeness_metrics()
- Fails-open behavior on forced bridge failures
- get_entry_snapshot query path
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from unittest import mock
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
import transactions_store
import evaluation_engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_db_url(tmp_path):
    """Provides a clean, file-backed SQLite database URL isolated per test."""
    db_file = tmp_path / f"test_retro_m1_{int(time.time() * 1000)}.db"
    return f"sqlite:///{db_file}"


# ---------------------------------------------------------------------------
# Section 1: Schema & Migration Idempotence
# ---------------------------------------------------------------------------

def test_paper_entry_snapshots_table_creation(isolated_db_url):
    """Verify that paper_entry_snapshots table is created with all required columns."""
    store = PaperAccountStore(db_url=isolated_db_url)
    insp = inspect(store.engine)

    assert insp.has_table("paper_entry_snapshots"), "paper_entry_snapshots table must exist"

    cols = {col["name"]: col for col in insp.get_columns("paper_entry_snapshots")}
    expected_cols = [
        "snapshot_id",
        "trade_id",
        "symbol",
        "strategy_id",
        "pilot_id",
        "experiment_arm",
        "entry_ts",
        "entry_price",
        "side",
        "qty",
        "client_order_id",
        "provenance",
        "provenance_tag",
        "conviction",
        "macro_regime",
        "signal_score",
        "raw_forecast",
        "forecast_model",
        "key_indicators_json",
        "decision_rationale",
        "created_at",
    ]
    for col_name in expected_cols:
        assert col_name in cols, f"Missing column {col_name} in paper_entry_snapshots"


def test_paper_closed_trades_additive_columns_migration(isolated_db_url):
    """Verify paper_closed_trades has all 5 new bridge and snapshot audit columns."""
    store = PaperAccountStore(db_url=isolated_db_url)
    insp = inspect(store.engine)

    assert insp.has_table("paper_closed_trades")
    cols = {col["name"]: col for col in insp.get_columns("paper_closed_trades")}

    expected_new_cols = [
        "entry_snapshot_id",
        "bridge_status",
        "bridged_trade_id",
        "bridge_error",
        "bridged_at",
    ]
    for col_name in expected_new_cols:
        assert col_name in cols, f"Missing column {col_name} in paper_closed_trades"


def test_schema_migration_idempotence_and_non_destructive(isolated_db_url):
    """Verify running schema initialization multiple times is idempotent and preserves data."""
    store = PaperAccountStore(db_url=isolated_db_url)

    # Insert a closed trade and an entry snapshot
    with store.Session() as session:
        snap = PaperEntrySnapshot(
            symbol="AAPL",
            strategy_id="s1",
            entry_ts=datetime.now(timezone.utc).replace(tzinfo=None),
            entry_price=150.0,
            side="buy",
            qty=10.0,
            provenance="signal_driven",
            conviction=0.85,
        )
        session.add(snap)
        session.flush()
        snap_id = snap.snapshot_id

        pct = PaperClosedTrade(
            symbol="AAPL",
            side="buy",
            qty=10.0,
            entry_price=150.0,
            exit_price=160.0,
            realized_pnl=100.0,
            close_reason="flatten",
            entry_snapshot_id=snap_id,
            bridge_status="bridged",
        )
        session.add(pct)
        session.commit()

    # Re-trigger account exists migration
    store._ensure_account_exists()

    # Re-run database_setup
    with mock.patch("settings.settings.DATABASE_URL", isolated_db_url):
        database_setup.build_database()

    # Verify data is still intact
    with store.Session() as session:
        loaded_snap = session.query(PaperEntrySnapshot).filter_by(snapshot_id=snap_id).first()
        assert loaded_snap is not None
        assert loaded_snap.symbol == "AAPL"
        assert loaded_snap.conviction == pytest.approx(0.85)

        loaded_pct = session.query(PaperClosedTrade).filter_by(entry_snapshot_id=snap_id).first()
        assert loaded_pct is not None
        assert loaded_pct.bridge_status == "bridged"


# ---------------------------------------------------------------------------
# Section 2: Forward-Only Snapshot Capture at Trade Open
# ---------------------------------------------------------------------------

def test_forward_only_snapshot_capture_signal_driven(isolated_db_url):
    """Signal-driven automated orders capture full quantitative context."""
    store = PaperAccountStore(db_url=isolated_db_url)

    fill_ok = store.apply_fill(
        client_order_id="ord_sig_1",
        symbol="MSFT",
        side="buy",
        qty=15.0,
        fill_price=300.0,
        strategy_id="strat_trend",
        provenance="signal_driven",
        provenance_tag="pilot:options_trend",
        conviction=0.75,
        macro_regime="BULL",
        signal_score=2.1,
        raw_forecast=320.0,
        key_indicators_json='{"rsi": 55.4, "garch_vol": 0.18}',
        decision_rationale="Trend alignment score above threshold",
    )
    assert fill_ok is True

    # Verify snapshot row created
    with store.Session() as session:
        snaps = session.query(PaperEntrySnapshot).filter_by(symbol="MSFT").all()
        assert len(snaps) == 1
        s = snaps[0]
        assert s.strategy_id == "strat_trend"
        assert s.provenance == "signal_driven"
        assert s.provenance_tag == "pilot:options_trend"
        assert s.conviction == pytest.approx(0.75)
        assert s.macro_regime == "BULL"
        assert s.signal_score == pytest.approx(2.1)
        assert s.raw_forecast == pytest.approx(320.0)
        assert s.key_indicators_json == '{"rsi": 55.4, "garch_vol": 0.18}'
        assert s.decision_rationale == "Trend alignment score above threshold"

        # Verify position links to snapshot
        pos = session.query(PaperPosition).filter_by(symbol="MSFT").first()
        assert pos is not None
        assert pos.entry_snapshot_id == s.snapshot_id


def test_forward_only_snapshot_capture_manual_order(isolated_db_url):
    """Manual discretionary ticket captures manual provenance without signal hallucination."""
    store = PaperAccountStore(db_url=isolated_db_url)

    fill_ok = store.apply_fill(
        client_order_id="ord_man_1",
        symbol="GOOGL",
        side="buy",
        qty=10.0,
        fill_price=170.0,
        provenance="manual",
        provenance_tag="manual:ticket",
    )
    assert fill_ok is True

    with store.Session() as session:
        snaps = session.query(PaperEntrySnapshot).filter_by(symbol="GOOGL").all()
        assert len(snaps) == 1
        s = snaps[0]
        assert s.provenance == "manual"
        assert s.provenance_tag == "manual:ticket"
        assert s.conviction is None, "Manual trade must have null conviction"
        assert s.raw_forecast is None
        assert s.signal_score is None


def test_forward_only_snapshot_capture_explicit_unknown_provenance(isolated_db_url):
    """Explicitly tagged unknown provenance fill records snapshot without conviction."""
    store = PaperAccountStore(db_url=isolated_db_url)

    fill_ok = store.apply_fill(
        client_order_id="ord_unk_1",
        symbol="AMZN",
        side="buy",
        qty=5.0,
        fill_price=180.0,
        provenance="unknown",
    )
    assert fill_ok is True

    with store.Session() as session:
        s = session.query(PaperEntrySnapshot).filter_by(symbol="AMZN").first()
        assert s is not None
        assert s.provenance == "unknown"
        assert s.conviction is None


def test_snapshot_trade_id_linked_on_close(isolated_db_url):
    """When a position closes, PaperEntrySnapshot.trade_id is updated to match closed trade PK."""
    store = PaperAccountStore(db_url=isolated_db_url)

    store.apply_fill(
        client_order_id="o_link_open",
        symbol="AAPL",
        side="buy",
        qty=10.0,
        fill_price=150.0,
        provenance="signal_driven",
        conviction=0.9,
    )
    store.apply_fill(
        client_order_id="o_link_close",
        symbol="AAPL",
        side="sell",
        qty=10.0,
        fill_price=160.0,
    )

    closed = store.get_full_closed_trades(symbol="AAPL")
    assert len(closed) == 1
    t = closed[0]
    snap_id = t["entry_snapshot_id"]
    assert snap_id is not None

    snap_dict = store.get_entry_snapshot(snap_id)
    assert snap_dict is not None
    assert snap_dict["trade_id"] == str(t["trade_id"])
    assert snap_dict["conviction"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Section 3: Historical "Not Captured" Guard
# ---------------------------------------------------------------------------

def test_historical_trade_lacking_snapshot_reports_none(isolated_db_url):
    """Pre-feature historical trade has null entry_snapshot_id and is never backfilled."""
    store = PaperAccountStore(db_url=isolated_db_url)

    # Manually insert legacy trade without entry_snapshot_id
    with store.Session() as session:
        legacy_trade = PaperClosedTrade(
            symbol="IBM",
            side="buy",
            qty=10.0,
            entry_price=140.0,
            exit_price=145.0,
            realized_pnl=50.0,
            close_reason="flatten",
            entry_snapshot_id=None,
            bridge_status="disabled",
        )
        session.add(legacy_trade)
        session.commit()

    trades = store.get_full_closed_trades(symbol="IBM")
    assert len(trades) == 1
    t = trades[0]
    assert t["entry_snapshot_id"] is None
    assert t["bridge_status"] == "disabled"

    # get_entry_snapshot with None strictly returns None
    assert store.get_entry_snapshot(None) is None
    assert store.get_entry_snapshot("") is None


def test_historical_plain_fill_does_not_fabricate_snapshot(isolated_db_url):
    """Plain fills with default untagged/no-metadata args do not create entry snapshot rows."""
    store = PaperAccountStore(db_url=isolated_db_url)

    store.apply_fill("ord_plain_1", "NVDA", "buy", 10.0, 100.0)
    store.apply_fill("ord_plain_2", "NVDA", "sell", 10.0, 120.0)

    closed = store.get_full_closed_trades(symbol="NVDA")
    assert len(closed) == 1
    assert closed[0]["entry_snapshot_id"] is None


# ---------------------------------------------------------------------------
# Section 4: Durable Bridge Tracking & Conviction Threading
# ---------------------------------------------------------------------------

def test_bridge_write_success_persists_status_and_threads_conviction(isolated_db_url, monkeypatch):
    """When bridge is enabled, successful close records bridge_status='bridged' and forwards conviction."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    store = PaperAccountStore(db_url=isolated_db_url)
    assert store._transactions_store is not None

    # Open with conviction 0.88
    store.apply_fill(
        client_order_id="b_open",
        symbol="NVDA",
        side="buy",
        qty=10.0,
        fill_price=100.0,
        strategy_id="strat_momentum",
        provenance="signal_driven",
        conviction=0.88,
    )

    # Close
    store.apply_fill(
        client_order_id="b_close",
        symbol="NVDA",
        side="sell",
        qty=10.0,
        fill_price=115.0,
        strategy_id="strat_momentum",
    )

    # Verify PaperClosedTrade
    trades = store.get_full_closed_trades(symbol="NVDA")
    assert len(trades) == 1
    t = trades[0]
    assert t["bridge_status"] == "bridged"
    assert t["bridged_trade_id"] is not None
    assert t["bridged_at"] is not None
    assert t["bridge_error"] is None

    # Verify transactions_store row
    ts = transactions_store.TransactionsStore(db_url=isolated_db_url)
    df = ts.closed_trades_df()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["symbol"] == "NVDA"
    assert row["side"] == "long"
    assert row["conviction"] == pytest.approx(0.88)
    assert row["entry_price"] == pytest.approx(100.0)
    assert row["exit_price"] == pytest.approx(115.0)


def test_bridge_conviction_threading_unstarves_calibration_curve(isolated_db_url, monkeypatch):
    """Bridged trade with conviction is recognized and binned by calibration_curve()."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    store = PaperAccountStore(db_url=isolated_db_url)

    # Execute 5 winning trades with conviction 0.85
    for i in range(5):
        store.apply_fill(
            client_order_id=f"cal_open_{i}",
            symbol=f"SYM{i}",
            side="buy",
            qty=10.0,
            fill_price=50.0,
            provenance="signal_driven",
            conviction=0.85,
        )
        store.apply_fill(
            client_order_id=f"cal_close_{i}",
            symbol=f"SYM{i}",
            side="sell",
            qty=10.0,
            fill_price=55.0,
        )

    ts = transactions_store.TransactionsStore(db_url=isolated_db_url)
    cal_df = evaluation_engine.calibration_curve(ts, n_bins=5, min_trades_per_bin=5)

    assert not cal_df.empty, "calibration_curve should NOT be empty when bridged trades have conviction"
    assert cal_df["count"].sum() == 5


def test_bridge_forced_failure_fails_open_captures_error(isolated_db_url, monkeypatch, caplog):
    """Bridge failure leaves paper close successful, records failed status, and increments failure count."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    store = PaperAccountStore(db_url=isolated_db_url)

    store.apply_fill(
        client_order_id="fail_open",
        symbol="META",
        side="buy",
        qty=10.0,
        fill_price=500.0,
    )

    # Mock bridge record_trade to raise
    def _raise_error(*args, **kwargs):
        raise RuntimeError("simulated disk I/O error during bridge write")

    monkeypatch.setattr(store._transactions_store, "record_trade", _raise_error)

    with caplog.at_level("WARNING"):
        close_ok = store.apply_fill(
            client_order_id="fail_close",
            symbol="META",
            side="sell",
            qty=10.0,
            fill_price=520.0,
        )
        assert close_ok is True, "Paper close must succeed despite bridge failure (fail-open)"

    # Position must be closed and cash updated
    assert len(store.get_open_positions()) == 0
    assert store._transactions_bridge_failures == 1

    trades = store.get_full_closed_trades(symbol="META")
    assert len(trades) == 1
    t = trades[0]
    assert t["bridge_status"] == "failed"
    assert t["bridged_trade_id"] is None
    assert "simulated disk I/O error during bridge write" in t["bridge_error"]

    # Verify metric shows degraded
    metrics = store.get_bridge_completeness_metrics()
    assert metrics["failed_count"] == 1
    assert metrics["status"] == "degraded"
    assert metrics["completeness_pct"] == 0.0
    assert metrics["last_failure"] is not None
    assert metrics["last_failure"]["symbol"] == "META"
    assert "simulated disk I/O error" in metrics["last_failure"]["error"]


def test_bridge_disabled_sets_disabled_status(isolated_db_url):
    """When bridge is disabled (default), closed trades record bridge_status='disabled'."""
    assert settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED is False

    store = PaperAccountStore(db_url=isolated_db_url)
    assert store._transactions_store is None

    store.apply_fill("off_open", "NFLX", "buy", 5.0, 600.0)
    store.apply_fill("off_close", "NFLX", "sell", 5.0, 620.0)

    trades = store.get_full_closed_trades(symbol="NFLX")
    assert len(trades) == 1
    t = trades[0]
    assert t["bridge_status"] == "disabled"
    assert t["bridged_trade_id"] is None
    assert t["bridge_error"] is None

    metrics = store.get_bridge_completeness_metrics()
    assert metrics["bridge_enabled"] is False
    assert metrics["status"] == "disabled"
    assert metrics["disabled_count"] == 1
    assert metrics["bridged_count"] == 0
    assert metrics["failed_count"] == 0
    assert metrics["completeness_pct"] == 100.0


# ---------------------------------------------------------------------------
# Section 5: Completeness Metrics Permutations
# ---------------------------------------------------------------------------

def test_bridge_completeness_metrics_calculation(isolated_db_url, monkeypatch):
    """Verify completeness metric across mixed bridged, failed, and disabled trades."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    store = PaperAccountStore(db_url=isolated_db_url)

    # Seed 9 bridged trades, 1 failed trade, 1 disabled trade directly into table
    with store.Session() as session:
        for i in range(9):
            session.add(PaperClosedTrade(
                symbol=f"S{i}", side="buy", qty=1.0, entry_price=10.0, exit_price=11.0,
                realized_pnl=1.0, close_reason="flatten", bridge_status="bridged",
                bridged_trade_id=i+1,
            ))
        session.add(PaperClosedTrade(
            symbol="FAIL_SYM", side="buy", qty=1.0, entry_price=10.0, exit_price=11.0,
            realized_pnl=1.0, close_reason="flatten", bridge_status="failed",
            bridge_error="Lock contention",
        ))
        session.add(PaperClosedTrade(
            symbol="DIS_SYM", side="buy", qty=1.0, entry_price=10.0, exit_price=11.0,
            realized_pnl=1.0, close_reason="flatten", bridge_status="disabled",
        ))
        session.commit()

    metrics = store.get_bridge_completeness_metrics()
    assert metrics["total_closed_trades"] == 11
    assert metrics["attempted_count"] == 10
    assert metrics["bridged_count"] == 9
    assert metrics["failed_count"] == 1
    assert metrics["disabled_count"] == 1
    assert metrics["completeness_pct"] == pytest.approx(90.0)
    assert metrics["status"] == "degraded"
    assert metrics["last_failure"]["symbol"] == "FAIL_SYM"


def test_bridge_completeness_metrics_zero_closed_trades(isolated_db_url):
    """Verify completeness metric division guard when zero trades have been closed."""
    store = PaperAccountStore(db_url=isolated_db_url)
    metrics = store.get_bridge_completeness_metrics()
    assert metrics["total_closed_trades"] == 0
    assert metrics["attempted_count"] == 0
    assert metrics["bridged_count"] == 0
    assert metrics["failed_count"] == 0
    assert metrics["completeness_pct"] == 100.0
    assert metrics["last_failure"] is None
