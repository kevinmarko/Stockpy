"""Tests for data/trade_decision_snapshot_store.py -- the forward-only
decision-context capture backing the Retrospective Learning Loop (Trade
Journal), and its wiring into data/paper_account_store.py's apply_fill /
apply_multi_leg_fill / the transactions_store bridge.

conftest.py's `_isolate_trade_decision_snapshot_db_in_tests` autouse fixture
points the default resolver at sqlite:///:memory: for every test in this
suite unless a test passes its own db_url -- these tests use an explicit
tmp_path-backed file DB (matching test_paper_account_store.py's existing
convention) precisely where cross-store consistency (same physical file)
matters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from data.paper_account_store import PaperAccountStore
from data.trade_decision_snapshot_store import TradeDecisionSnapshotStore


def test_record_and_get_snapshot_roundtrip(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'snap.db'}"
    store = TradeDecisionSnapshotStore(db_url=db_url)
    entry_ts = datetime(2026, 1, 5, 14, 30, 0)

    store.record_snapshot(
        symbol="aapl",
        strategy_id="earnings-crush",
        entry_ts=entry_ts,
        provenance="automated:options_auto_scan",
        conviction=0.72,
        regime="RISK_ON",
        factors={"ivr": 61.4, "vrp": 0.031, "trend_bias": "Bullish"},
        notes="test",
    )

    snap = store.get_snapshot(symbol="AAPL", strategy_id="earnings-crush", entry_ts=entry_ts)
    assert snap is not None
    assert snap["symbol"] == "AAPL"
    assert snap["provenance"] == "automated:options_auto_scan"
    assert snap["conviction"] == pytest.approx(0.72)
    assert snap["regime"] == "RISK_ON"
    assert snap["factors"] == {"ivr": 61.4, "vrp": 0.031, "trend_bias": "Bullish"}


def test_get_snapshot_returns_none_when_no_row_exists(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'snap.db'}"
    store = TradeDecisionSnapshotStore(db_url=db_url)
    result = store.get_snapshot(symbol="TSLA", strategy_id="untagged", entry_ts=datetime.now(timezone.utc))
    assert result is None


def test_get_snapshot_never_raises_on_missing_table(tmp_path):
    """A fresh readonly store against a DB that has never seen a write-mode
    construction has no table at all -- must degrade to None, not raise
    (CONSTRAINT #6)."""
    db_url = f"sqlite:///{tmp_path / 'never_written.db'}"
    store = TradeDecisionSnapshotStore(db_url=db_url, readonly=True)
    assert store.get_snapshot(symbol="X", strategy_id="Y", entry_ts=datetime.now(timezone.utc)) is None
    assert store.get_snapshots_batch([("X", "Y", datetime.now(timezone.utc))]) == {}


def test_record_snapshot_raises_on_readonly_instance(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'snap.db'}"
    # Seed the table via a write-mode instance first so the readonly engine
    # has something real to connect to.
    TradeDecisionSnapshotStore(db_url=db_url)
    readonly_store = TradeDecisionSnapshotStore(db_url=db_url, readonly=True)
    with pytest.raises(RuntimeError):
        readonly_store.record_snapshot(
            symbol="X", strategy_id="Y", entry_ts=datetime.now(timezone.utc), provenance="manual"
        )


def test_record_snapshot_requires_provenance(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'snap.db'}"
    store = TradeDecisionSnapshotStore(db_url=db_url)
    with pytest.raises(ValueError):
        store.record_snapshot(symbol="X", strategy_id="Y", entry_ts=datetime.now(timezone.utc), provenance="")


def test_non_json_serializable_factor_is_dropped_not_fabricated(tmp_path):
    """A factor value that can't round-trip through JSON must be dropped,
    never silently coerced into a placeholder (CONSTRAINT #4)."""
    db_url = f"sqlite:///{tmp_path / 'snap.db'}"
    store = TradeDecisionSnapshotStore(db_url=db_url)
    entry_ts = datetime(2026, 2, 1)

    class Unserializable:
        pass

    store.record_snapshot(
        symbol="MSFT",
        strategy_id="untagged",
        entry_ts=entry_ts,
        provenance="manual",
        factors={"ok_value": 1.5, "bad_value": Unserializable()},
    )
    snap = store.get_snapshot(symbol="MSFT", strategy_id="untagged", entry_ts=entry_ts)
    assert snap["factors"] == {"ok_value": 1.5}


def test_get_snapshots_batch_only_returns_matching_keys(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'snap.db'}"
    store = TradeDecisionSnapshotStore(db_url=db_url)
    entry_ts_a = datetime(2026, 1, 1)
    entry_ts_b = datetime(2026, 1, 2)
    store.record_snapshot(symbol="AAPL", strategy_id="s1", entry_ts=entry_ts_a, provenance="manual")

    result = store.get_snapshots_batch(
        [("AAPL", "s1", entry_ts_a), ("AAPL", "s2", entry_ts_b), ("TSLA", "s1", entry_ts_a)]
    )
    assert set(result.keys()) == {("AAPL", "s1", entry_ts_a)}


# ---------------------------------------------------------------------- #
# Wiring into PaperAccountStore.apply_fill / apply_multi_leg_fill / the
# transactions_store bridge.
# ---------------------------------------------------------------------- #


def test_apply_fill_with_no_decision_context_writes_nothing(tmp_path):
    """Byte-identical-to-before-this-feature default: omitting
    decision_context must never write a snapshot row."""
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)
    assert store.apply_fill("o1", "AAPL", "buy", 10.0, 100.0, strategy_id="s1") is True

    snap_store = TradeDecisionSnapshotStore(db_url=db_url, readonly=True)
    # There is exactly one open position now; find its entry_ts via the store.
    positions = store.get_open_positions()
    entry_ts = positions[0].entry_ts if hasattr(positions[0], "entry_ts") else None
    # Regardless of how entry_ts is exposed, no snapshot should exist for
    # this symbol/strategy under any entry_ts.
    assert snap_store.get_snapshots_batch([("AAPL", "s1", entry_ts or datetime.now(timezone.utc))]) == {}


def test_apply_fill_with_decision_context_captures_a_snapshot_on_new_open(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)

    assert store.apply_fill(
        "o1", "AAPL", "buy", 10.0, 100.0, strategy_id="earnings-crush",
        decision_context={"provenance": "automated:options_auto_scan", "conviction": 0.65},
    ) is True

    snap_store = TradeDecisionSnapshotStore(db_url=db_url, readonly=True)
    with store.Session() as session:
        from data.paper_account_store import PaperPosition
        pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="earnings-crush").first()
        entry_ts = pos.entry_ts

    snap = snap_store.get_snapshot(symbol="AAPL", strategy_id="earnings-crush", entry_ts=entry_ts)
    assert snap is not None
    assert snap["provenance"] == "automated:options_auto_scan"
    assert snap["conviction"] == pytest.approx(0.65)


def test_averaging_in_does_not_recapture_a_snapshot(tmp_path):
    """entry_ts is left untouched while averaging in, so a second fill with
    a DIFFERENT decision_context must not overwrite/duplicate the original
    open's snapshot (matching PaperPosition.entry_ts's own "left untouched"
    convention exactly)."""
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)
    store.apply_fill(
        "o1", "AAPL", "buy", 10.0, 100.0, strategy_id="s1",
        decision_context={"provenance": "manual"},
    )
    store.apply_fill(
        "o2", "AAPL", "buy", 5.0, 110.0, strategy_id="s1",
        decision_context={"provenance": "automated:options_auto_scan", "conviction": 0.9},
    )

    with store.Session() as session:
        from data.paper_account_store import PaperPosition
        pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="s1").first()
        entry_ts = pos.entry_ts

    snap_store = TradeDecisionSnapshotStore(db_url=db_url, readonly=True)
    snap = snap_store.get_snapshot(symbol="AAPL", strategy_id="s1", entry_ts=entry_ts)
    assert snap["provenance"] == "manual"  # the ORIGINAL open, not the average-in


def test_flip_through_zero_captures_a_fresh_snapshot(tmp_path):
    """A short fully closed and flipped into a new long IS a genuinely new
    entry_ts (per PaperAccountStore's own existing convention), so it gets
    its own fresh snapshot."""
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)
    store.apply_fill(
        "o1", "AAPL", "sell", 10.0, 100.0, strategy_id="s1", allow_short=True,
        decision_context={"provenance": "manual"},
    )
    # Buy 20 -> closes the 10-share short and opens a fresh 10-share long.
    store.apply_fill(
        "o2", "AAPL", "buy", 20.0, 90.0, strategy_id="s1",
        decision_context={"provenance": "automated:options_auto_scan", "conviction": 0.4},
    )

    with store.Session() as session:
        from data.paper_account_store import PaperPosition
        pos = session.query(PaperPosition).filter_by(symbol="AAPL", strategy_id="s1").first()
        assert pos.qty == pytest.approx(10.0)
        entry_ts = pos.entry_ts

    snap_store = TradeDecisionSnapshotStore(db_url=db_url, readonly=True)
    snap = snap_store.get_snapshot(symbol="AAPL", strategy_id="s1", entry_ts=entry_ts)
    assert snap["provenance"] == "automated:options_auto_scan"
    assert snap["conviction"] == pytest.approx(0.4)


def test_decision_snapshot_failure_never_blocks_the_paper_fill(tmp_path, monkeypatch):
    """A snapshot-store failure must fail OPEN: the fill itself still
    succeeds (CONSTRAINT #6)."""
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated snapshot-store failure")

    import data.trade_decision_snapshot_store as tdss_module

    monkeypatch.setattr(tdss_module, "build_snapshot_row", _boom)

    assert store.apply_fill(
        "o1", "AAPL", "buy", 10.0, 100.0, strategy_id="s1",
        decision_context={"provenance": "manual"},
    ) is True


def test_bridge_threads_conviction_through_when_snapshot_exists(tmp_path, monkeypatch):
    """Closing a trade that had a captured snapshot must pass its
    conviction into transactions_store.record_trade -- the exact gap this
    feature exists to close (see CLAUDE.md's Retrospective Learning Loop
    §0 finding: conviction never survived the bridge before this fix)."""
    import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)
    assert store._transactions_store is not None

    store.apply_fill(
        "o1", "AAPL", "buy", 10.0, 100.0, strategy_id="s1",
        decision_context={"provenance": "automated:options_auto_scan", "conviction": 0.81},
    )
    store.apply_fill("o2", "AAPL", "sell", 10.0, 110.0, strategy_id="s1")

    from transactions_store import TransactionsStore

    ts = TransactionsStore(db_url=db_url, readonly=True)
    trades = ts.get_trade_history("AAPL")
    assert len(trades) == 1
    assert trades.iloc[0]["conviction"] == pytest.approx(0.81)


def test_bridge_leaves_conviction_none_when_no_snapshot_was_captured(tmp_path, monkeypatch):
    """A manual/un-wired/pre-feature trade with no snapshot must bridge
    with conviction=None -- never a fabricated value."""
    import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    db_url = f"sqlite:///{tmp_path / 'acct.db'}"
    store = PaperAccountStore(db_url=db_url)

    store.apply_fill("o1", "AAPL", "buy", 10.0, 100.0, strategy_id="Manual Trade")
    store.apply_fill("o2", "AAPL", "sell", 10.0, 110.0, strategy_id="Manual Trade")

    from transactions_store import TransactionsStore

    ts = TransactionsStore(db_url=db_url, readonly=True)
    trades = ts.get_trade_history("AAPL")
    assert len(trades) == 1
    assert trades.iloc[0]["conviction"] is None or trades.iloc[0]["conviction"] != trades.iloc[0]["conviction"]  # NaN-safe
