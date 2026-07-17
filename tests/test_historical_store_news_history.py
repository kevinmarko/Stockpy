"""Tests for data/historical_store.py's news_history table (forward-archive only).

No backtest reads this table today -- see the news_history DDL comment and
pilots/catalog.py's News Catalyst entry. These tests only cover the write
path: DDL idempotency, round-trip persistence, and dead-letter behavior.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

import pytest

from data.historical_store import HistoricalStore


class TestNewsHistoryDDL:
    def test_table_created_on_init(self, tmp_path):
        db = str(tmp_path / "news.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='news_history'"
            ).fetchone()
        assert row is not None

    def test_ensure_tables_idempotent(self, tmp_path):
        """Constructing a second HistoricalStore against the same DB must not raise."""
        db = str(tmp_path / "news.db")
        HistoricalStore(db_path=db)
        HistoricalStore(db_path=db)  # should not raise


class TestSaveNewsSentiment:
    def test_round_trip(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)

        store.save_news_sentiment({"AAPL": 0.42, "MSFT": -0.15}, as_of, source="finbert")

        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT symbol, as_of, score, source FROM news_history ORDER BY symbol"
            ).fetchall()
        assert rows == [
            ("AAPL", "2026-07-17", pytest.approx(0.42), "finbert"),
            ("MSFT", "2026-07-17", pytest.approx(-0.15), "finbert"),
        ]

    def test_nan_score_stored_as_null(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment({"AAPL": float("nan")}, datetime.now(timezone.utc))

        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT score FROM news_history WHERE symbol='AAPL'").fetchone()
        assert row[0] is None

    def test_upsert_overwrites_same_symbol_and_date(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)

        store.save_news_sentiment({"AAPL": 0.1}, as_of)
        store.save_news_sentiment({"AAPL": 0.9}, as_of)

        with sqlite3.connect(db) as conn:
            rows = conn.execute("SELECT score FROM news_history WHERE symbol='AAPL'").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(0.9)

    def test_empty_scores_is_a_noop(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment({}, datetime.now(timezone.utc))  # must not raise

        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM news_history").fetchone()[0]
        assert count == 0

    def test_write_failure_is_swallowed(self, tmp_path, monkeypatch):
        """CONSTRAINT #6: a write failure must never raise out of this method."""
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(store, "_now_utc_iso", _boom)

        store.save_news_sentiment({"AAPL": 0.5}, datetime.now(timezone.utc))  # must not raise
