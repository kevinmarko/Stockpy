"""
tests/test_decision_log.py — Manual execution decision journal (Tier 1 / 1.3).

Covers:
  - DecisionEntry frozen dataclass
  - append_decision / read_decisions round-trip
  - decisions_df schema and Int64 trade_id
  - Corrupt / blank lines skipped, others returned
  - join_to_store: match within window, None outside, None on failure
  - log_decision: field wiring, "passed" skips join, "acted" joins
  - Injectable now_fn for deterministic timestamps

All tests use in-memory SQLite (no production DB touched) and
tmp_path (no output/decision_log.jsonl written).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from gui.decision_log import (
    DEFAULT_LOG_PATH,
    ActionTaken,
    DecisionEntry,
    _SCHEMA,
    append_decision,
    decisions_df,
    join_to_store,
    log_decision,
    read_decisions,
)
from transactions_store import TransactionsStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem_store() -> TransactionsStore:
    return TransactionsStore(db_url="sqlite:///:memory:")


def _add_closed(store: TransactionsStore, *, symbol="AAPL", entry_price=100.0,
                exit_price=110.0, conviction=None, days_ago=1) -> int:
    now = datetime.utcnow()
    tid = store.record_trade(
        symbol=symbol, side="long",
        entry_ts=now - timedelta(days=days_ago + 1),
        entry_price=entry_price, shares=1.0, conviction=conviction,
    )
    store.close_trade(tid, exit_ts=now - timedelta(days=days_ago), exit_price=exit_price)
    return tid


def _fixed_now() -> str:
    return "2026-06-26T12:00:00+00:00"


# ---------------------------------------------------------------------------
# TestDecisionEntry
# ---------------------------------------------------------------------------

class TestDecisionEntry:
    """DecisionEntry is a frozen dataclass with the correct fields."""

    def test_frozen(self):
        e = DecisionEntry("AAPL", "acted", "BUY", 0.8, "", _fixed_now(), "")
        with pytest.raises((AttributeError, TypeError)):
            e.symbol = "MSFT"  # type: ignore[misc]

    def test_default_trade_id_is_none(self):
        e = DecisionEntry("AAPL", "acted", "BUY", 0.8, "", _fixed_now(), "")
        assert e.trade_id is None

    def test_all_fields_present(self):
        e = DecisionEntry(
            symbol="MSFT", action_taken="passed", signal_action="HOLD",
            conviction=0.6, notes="too small", timestamp=_fixed_now(), signal_ts="", trade_id=42,
        )
        d = asdict(e)
        for field in ("symbol", "action_taken", "signal_action", "conviction",
                      "notes", "timestamp", "signal_ts", "trade_id"):
            assert field in d

    def test_action_taken_values(self):
        for action in ("acted", "passed", "modified"):
            e = DecisionEntry("AAPL", action, "BUY", 0.7, "", _fixed_now(), "")
            assert e.action_taken == action


# ---------------------------------------------------------------------------
# TestAppendAndRead
# ---------------------------------------------------------------------------

class TestAppendAndRead:
    """append_decision / read_decisions JSONL round-trip."""

    def test_single_entry_round_trip(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        e = DecisionEntry("AAPL", "acted", "BUY", 0.85, "", _fixed_now(), "")
        append_decision(e, log_path=log)
        result = read_decisions(log)
        assert len(result) == 1
        assert result[0].symbol == "AAPL"
        assert result[0].action_taken == "acted"
        assert result[0].conviction == pytest.approx(0.85)

    def test_multiple_entries_order_preserved(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        symbols = ["AAPL", "MSFT", "NVDA"]
        for sym in symbols:
            append_decision(
                DecisionEntry(sym, "passed", "HOLD", 0.55, "", _fixed_now(), ""),
                log_path=log,
            )
        result = read_decisions(log)
        assert [r.symbol for r in result] == symbols

    def test_missing_file_returns_empty(self, tmp_path: Path):
        result = read_decisions(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_corrupt_line_skipped_others_returned(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        good = DecisionEntry("AAPL", "acted", "BUY", 0.9, "", _fixed_now(), "")
        log.write_text(
            '{"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY", '
            '"conviction": 0.9, "notes": "", "timestamp": "' + _fixed_now() + '", "signal_ts": ""}\n'
            "not-json-at-all!!!\n"
            '{"symbol": "MSFT", "action_taken": "passed", "signal_action": "HOLD", '
            '"conviction": 0.6, "notes": "", "timestamp": "' + _fixed_now() + '", "signal_ts": ""}\n',
            encoding="utf-8",
        )
        result = read_decisions(log)
        assert len(result) == 2
        assert result[0].symbol == "AAPL"
        assert result[1].symbol == "MSFT"

    def test_blank_lines_skipped(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        log.write_text(
            "\n\n"
            '{"symbol":"AAPL","action_taken":"passed","signal_action":"BUY",'
            '"conviction":0.7,"notes":"","timestamp":"' + _fixed_now() + '","signal_ts":""}\n'
            "\n",
            encoding="utf-8",
        )
        result = read_decisions(log)
        assert len(result) == 1

    def test_trade_id_round_trip(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        e = DecisionEntry("AAPL", "acted", "BUY", 0.9, "", _fixed_now(), "", trade_id=77)
        append_decision(e, log_path=log)
        result = read_decisions(log)
        assert result[0].trade_id == 77

    def test_none_conviction_round_trip(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        e = DecisionEntry("TSLA", "passed", "SELL", None, "", _fixed_now(), "")
        append_decision(e, log_path=log)
        result = read_decisions(log)
        assert result[0].conviction is None

    def test_creates_parent_dir(self, tmp_path: Path):
        log = tmp_path / "sub" / "deep" / "dl.jsonl"
        append_decision(
            DecisionEntry("AAPL", "passed", "HOLD", 0.5, "", _fixed_now(), ""),
            log_path=log,
        )
        assert log.exists()


# ---------------------------------------------------------------------------
# TestDecisionsDf
# ---------------------------------------------------------------------------

class TestDecisionsDf:
    """decisions_df returns a typed DataFrame."""

    def test_empty_schema(self, tmp_path: Path):
        df = decisions_df(tmp_path / "nonexistent.jsonl")
        assert df.empty
        assert list(df.columns) == list(_SCHEMA.keys())

    def test_trade_id_is_int64(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        e = DecisionEntry("AAPL", "acted", "BUY", 0.9, "", _fixed_now(), "", trade_id=5)
        append_decision(e, log_path=log)
        df = decisions_df(log)
        assert str(df["trade_id"].dtype) == "Int64"

    def test_trade_id_nullable_int64_allows_na(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        append_decision(
            DecisionEntry("AAPL", "passed", "HOLD", 0.6, "", _fixed_now(), ""),
            log_path=log,
        )
        df = decisions_df(log)
        assert pd.isna(df["trade_id"].iloc[0])

    def test_row_count_matches_entries(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        for sym in ["AAPL", "MSFT", "GOOG"]:
            append_decision(
                DecisionEntry(sym, "passed", "HOLD", 0.55, "", _fixed_now(), ""),
                log_path=log,
            )
        df = decisions_df(log)
        assert len(df) == 3


# ---------------------------------------------------------------------------
# TestJoinToStore
# ---------------------------------------------------------------------------

class TestJoinToStore:
    """join_to_store links an entry to a matching trade record."""

    def test_finds_match_within_window(self):
        store = _mem_store()
        now = datetime.utcnow()
        # Trade entered 1 hour ago — comfortably within the 24 h window
        tid = store.record_trade("AAPL", "long", now - timedelta(hours=1), 100.0, 1.0)
        store.close_trade(tid, now, 110.0)
        entry = DecisionEntry(
            "AAPL", "acted", "BUY", 0.9, "", datetime.now(timezone.utc).isoformat(), ""
        )
        result = join_to_store(entry, store, window_hours=24.0)
        assert result == tid

    def test_returns_none_outside_window(self):
        store = _mem_store()
        _add_closed(store, symbol="AAPL", days_ago=5)  # 5 days ago — beyond 24 h
        entry = DecisionEntry(
            "AAPL", "acted", "BUY", 0.9, "", datetime.now(timezone.utc).isoformat(), ""
        )
        result = join_to_store(entry, store, window_hours=24.0)
        assert result is None

    def test_returns_none_when_symbol_not_found(self):
        store = _mem_store()
        _add_closed(store, symbol="AAPL")
        entry = DecisionEntry(
            "MSFT", "acted", "BUY", 0.9, "", datetime.now(timezone.utc).isoformat(), ""
        )
        result = join_to_store(entry, store, window_hours=24.0)
        assert result is None

    def test_picks_closest_when_multiple(self):
        store = _mem_store()
        now = datetime.utcnow()
        # Two trades: one 1 h ago, one 12 h ago
        t_close = store.record_trade("AAPL", "long", now - timedelta(hours=1), 100.0, 1.0)
        store.close_trade(t_close, now, 110.0)
        t_far = store.record_trade("AAPL", "long", now - timedelta(hours=12), 100.0, 1.0)
        store.close_trade(t_far, now - timedelta(hours=11), 105.0)

        entry = DecisionEntry("AAPL", "acted", "BUY", 0.9, "", now.isoformat(), "")
        result = join_to_store(entry, store, window_hours=24.0)
        assert result == t_close

    def test_store_failure_returns_none(self):
        class _BrokenStore:
            def get_trade_history(self, _sym):
                raise RuntimeError("DB down")

        entry = DecisionEntry("AAPL", "acted", "BUY", 0.9, "", _fixed_now(), "")
        result = join_to_store(entry, _BrokenStore(), window_hours=24.0)
        assert result is None

    def test_case_insensitive_symbol_match(self):
        store = _mem_store()
        now = datetime.utcnow()
        tid = store.record_trade("AAPL", "long", now, 100.0, 1.0)
        store.close_trade(tid, now + timedelta(hours=1), 110.0)
        # Entry uses lowercase
        entry = DecisionEntry("aapl", "acted", "BUY", 0.9, "", now.isoformat(), "")
        result = join_to_store(entry, store, window_hours=24.0)
        assert result == tid


# ---------------------------------------------------------------------------
# TestLogDecision
# ---------------------------------------------------------------------------

class TestLogDecision:
    """log_decision orchestrates entry creation, join, and append."""

    def test_fields_wired_correctly(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        entry = log_decision(
            symbol="aapl",
            action_taken="passed",
            signal_action="BUY",
            conviction=0.75,
            notes="too risky today",
            signal_ts="2026-06-26T10:00:00+00:00",
            log_path=log,
            now_fn=_fixed_now,
        )
        assert entry.symbol == "AAPL"
        assert entry.action_taken == "passed"
        assert entry.signal_action == "BUY"
        assert entry.conviction == pytest.approx(0.75)
        assert entry.notes == "too risky today"
        assert entry.timestamp == _fixed_now()
        assert entry.signal_ts == "2026-06-26T10:00:00+00:00"

    def test_entry_appended_to_log(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        log_decision("AAPL", "passed", "BUY", 0.8, log_path=log, now_fn=_fixed_now)
        entries = read_decisions(log)
        assert len(entries) == 1
        assert entries[0].symbol == "AAPL"

    def test_passed_does_not_join_store(self, tmp_path: Path):
        store = _mem_store()
        _add_closed(store, symbol="AAPL", days_ago=0)
        log = tmp_path / "dl.jsonl"
        entry = log_decision(
            "AAPL", "passed", "BUY", 0.8,
            transactions_store=store, log_path=log, now_fn=_fixed_now,
        )
        # Even though a matching trade exists, "passed" never joins
        assert entry.trade_id is None

    def test_modified_does_not_join_store(self, tmp_path: Path):
        store = _mem_store()
        _add_closed(store, symbol="AAPL", days_ago=0)
        log = tmp_path / "dl.jsonl"
        entry = log_decision(
            "AAPL", "modified", "BUY", 0.8, notes="used limit",
            transactions_store=store, log_path=log, now_fn=_fixed_now,
        )
        assert entry.trade_id is None

    def test_acted_joins_when_trade_within_window(self, tmp_path: Path):
        store = _mem_store()
        now = datetime.utcnow()
        tid = store.record_trade("AAPL", "long", now, 100.0, 1.0)
        store.close_trade(tid, now + timedelta(hours=1), 110.0)
        log = tmp_path / "dl.jsonl"
        entry = log_decision(
            "AAPL", "acted", "BUY", 0.8,
            transactions_store=store, log_path=log,
            now_fn=lambda: now.isoformat(),
        )
        assert entry.trade_id == tid

    def test_acted_no_trade_id_when_no_match(self, tmp_path: Path):
        store = _mem_store()  # empty store — no trades
        log = tmp_path / "dl.jsonl"
        entry = log_decision(
            "AAPL", "acted", "BUY", 0.8,
            transactions_store=store, log_path=log, now_fn=_fixed_now,
        )
        assert entry.trade_id is None

    def test_acted_without_store_arg(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        entry = log_decision(
            "AAPL", "acted", "BUY", 0.8,
            transactions_store=None, log_path=log, now_fn=_fixed_now,
        )
        assert entry.trade_id is None

    def test_none_conviction_accepted(self, tmp_path: Path):
        log = tmp_path / "dl.jsonl"
        entry = log_decision("AAPL", "passed", "HOLD", None, log_path=log, now_fn=_fixed_now)
        assert entry.conviction is None
        persisted = read_decisions(log)
        assert persisted[0].conviction is None
