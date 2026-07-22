"""Tests for data/historical_store.py's sentiment_ingestion_audit table
(Sentiment Pipeline Phase 2) -- per-document audit trail, DDL wiring,
the trading-day roll (leakage-critical), and the write path.

Mirrors tests/test_historical_store_news_history.py's coverage shape.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from data.historical_store import HistoricalStore


class TestSentimentAuditDDL:
    def test_table_created_on_init(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='sentiment_ingestion_audit'"
            ).fetchone()
        assert row is not None

    def test_ensure_tables_idempotent(self, tmp_path):
        """Constructing a second HistoricalStore against the same DB must not raise."""
        db = str(tmp_path / "sentiment.db")
        HistoricalStore(db_path=db)
        HistoricalStore(db_path=db)  # should not raise

    def test_no_foreign_key_on_symbol(self, tmp_path):
        """C2 fix: symbol is a free-text dimension, no FK to account_positions."""
        db = str(tmp_path / "sentiment.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            fks = conn.execute("PRAGMA foreign_key_list(sentiment_ingestion_audit)").fetchall()
        assert fks == []


class TestResolveTradingDay:
    def test_intraday_timestamp_same_day(self):
        # 10:00 AM ET on a Tuesday -- well before close, same trading day.
        as_of = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)  # 10:00 ET (EDT, UTC-4)
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-21"

    def test_post_close_rolls_to_next_day(self):
        # 16:01 ET must roll to t+1, not stay on t -- the leakage-critical rule.
        as_of = datetime(2026, 7, 21, 20, 1, tzinfo=timezone.utc)  # 16:01 ET (EDT)
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-22"

    def test_exactly_at_close_rolls_to_next_day(self):
        # 16:00 ET exactly is treated as post-close (>=), not pre-close.
        as_of = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)  # 16:00 ET (EDT)
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-22"

    def test_just_before_close_stays_same_day(self):
        as_of = datetime(2026, 7, 21, 19, 59, tzinfo=timezone.utc)  # 15:59 ET (EDT)
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-21"

    def test_friday_post_close_rolls_to_monday(self):
        # 2026-07-24 is a Friday. Post-close Friday must roll over the weekend.
        as_of = datetime(2026, 7, 24, 20, 1, tzinfo=timezone.utc)  # Fri 16:01 ET
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-27"  # Monday

    def test_saturday_timestamp_rolls_to_monday(self):
        as_of = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)  # Saturday
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-27"

    def test_naive_datetime_assumed_utc(self):
        as_of = datetime(2026, 7, 21, 14, 0)  # naive, no tzinfo
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-07-21"

    def test_winter_est_offset_handled(self):
        # January -- EST (UTC-5), not EDT. 16:01 ET = 21:01 UTC.
        as_of = datetime(2026, 1, 21, 21, 1, tzinfo=timezone.utc)  # 16:01 EST
        assert HistoricalStore.resolve_trading_day(as_of) == "2026-01-22"


class TestSaveSentimentDocuments:
    def _base_doc(self, **overrides):
        doc = {
            "as_of": datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),  # 10:00 ET
            "symbol": "aapl",
            "source_name": "finnhub",
            "text_content": "Apple beats earnings expectations",
            "raw_sentiment_score": 0.6,
        }
        doc.update(overrides)
        return doc

    def test_round_trip_minimal_fields(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([self._base_doc()])

        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT symbol, source_name, trading_day, raw_sentiment_score, "
                "s_authority, credibility_weight, is_bot, final_weighted_score "
                "FROM sentiment_ingestion_audit"
            ).fetchone()
        assert row[0] == "AAPL"  # uppercased
        assert row[1] == "finnhub"
        assert row[2] == "2026-07-21"
        assert row[3] == pytest.approx(0.6)
        assert row[4] is None  # s_authority not supplied -> NULL, never fabricated
        assert row[5] is None  # credibility_weight not supplied -> NULL
        assert row[6] == 0  # is_bot defaults to 0
        assert row[7] == pytest.approx(0.6)  # defaults to raw_sentiment_score

    def test_full_credibility_fields_persisted(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([self._base_doc(
            author_handle="some_user",
            s_authority=0.8, s_humanity=0.9, s_verification=0.7,
            credibility_weight=0.75, is_bot=1,
            final_weighted_score=0.45,
        )])

        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT author_handle, s_authority, s_humanity, s_verification, "
                "credibility_weight, is_bot, final_weighted_score "
                "FROM sentiment_ingestion_audit"
            ).fetchone()
        assert row == ("some_user", pytest.approx(0.8), pytest.approx(0.9),
                        pytest.approx(0.7), pytest.approx(0.75), 1, pytest.approx(0.45))

    def test_multiple_documents_batch_insert(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            self._base_doc(symbol="AAPL"),
            self._base_doc(symbol="MSFT", raw_sentiment_score=-0.3),
        ])
        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM sentiment_ingestion_audit").fetchone()[0]
        assert count == 2

    def test_empty_documents_is_a_noop(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([])  # must not raise

        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM sentiment_ingestion_audit").fetchone()[0]
        assert count == 0

    def test_write_failure_is_swallowed(self, tmp_path, monkeypatch):
        """CONSTRAINT #6: a write failure must never raise out of this method."""
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(store, "_now_utc_iso", _boom)

        store.save_sentiment_documents([self._base_doc()])  # must not raise

    def test_post_close_document_lands_on_next_trading_day(self, tmp_path):
        """End-to-end: a document ingested after close is archived under t+1,
        not t -- the leakage-critical guarantee this table exists to provide."""
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([self._base_doc(
            as_of=datetime(2026, 7, 21, 20, 1, tzinfo=timezone.utc),  # 16:01 ET
        )])

        with sqlite3.connect(db) as conn:
            trading_day = conn.execute(
                "SELECT trading_day FROM sentiment_ingestion_audit"
            ).fetchone()[0]
        assert trading_day == "2026-07-22"


class TestGetSentimentAggregateBySymbol:
    """Sentiment Pipeline Phase 4 -- per-symbol read aggregation."""

    def _seed(self, store, rows):
        store.save_sentiment_documents(rows)

    def _doc(self, **overrides):
        base = dict(
            as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),  # 10:00 ET
            symbol="AAPL",
            source_name="finnhub",
            text_content="test",
            raw_sentiment_score=0.5,
        )
        base.update(overrides)
        return base

    def test_no_rows_returns_empty_dict(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        assert store.get_sentiment_aggregate_by_symbol("2026-07-21") == {}

    def test_aggregates_mean_final_weighted_score(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        self._seed(store, [
            self._doc(final_weighted_score=0.4),
            self._doc(final_weighted_score=0.8),
        ])
        result = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        assert result["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(0.6)

    def test_aggregates_bot_activity_ratio(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        self._seed(store, [
            self._doc(is_bot=1),
            self._doc(is_bot=0),
            self._doc(is_bot=0),
            self._doc(is_bot=0),
        ])
        result = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        assert result["AAPL"]["bot_activity_ratio"] == pytest.approx(0.25)

    def test_aggregates_source_credibility(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        self._seed(store, [
            self._doc(credibility_weight=0.5),
            self._doc(credibility_weight=1.0),
        ])
        result = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        assert result["AAPL"]["aggregated_source_credibility"] == pytest.approx(0.75)

    def test_null_credibility_weight_yields_nan_not_crash(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        self._seed(store, [self._doc()])  # credibility_weight defaults to None/NULL
        result = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        import math
        assert math.isnan(result["AAPL"]["aggregated_source_credibility"])

    def test_separate_symbols_aggregated_independently(self, tmp_path):
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        self._seed(store, [
            self._doc(symbol="AAPL", final_weighted_score=0.9),
            self._doc(symbol="MSFT", final_weighted_score=-0.2),
        ])
        result = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert result["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(0.9)
        assert result["MSFT"]["credibility_weighted_sentiment"] == pytest.approx(-0.2)

    def test_scoped_strictly_to_trading_day_no_leakage(self, tmp_path):
        """Leakage-critical: a document whose as_of rolled to t+1 at write
        time must be invisible to a read for trading day t."""
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)
        self._seed(store, [
            self._doc(final_weighted_score=0.9),  # trading_day 2026-07-21
            self._doc(
                as_of=datetime(2026, 7, 21, 20, 1, tzinfo=timezone.utc),  # 16:01 ET -> rolls to 07-22
                final_weighted_score=-0.9,
            ),
        ])
        today_result = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        tomorrow_result = store.get_sentiment_aggregate_by_symbol("2026-07-22")
        assert today_result["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(0.9)
        assert tomorrow_result["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(-0.9)

    def test_read_failure_returns_empty_dict(self, tmp_path, monkeypatch):
        """CONSTRAINT #6: a read failure must never raise."""
        db = str(tmp_path / "sentiment.db")
        store = HistoricalStore(db_path=db)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(store, "Session", _boom)
        result = store.get_sentiment_aggregate_by_symbol("2026-07-21")  # must not raise
        assert result == {}
