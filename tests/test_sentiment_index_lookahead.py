"""No-lookahead test for signals/sentiment_index.py's composite sentiment
index S_t.

compute_sentiment_index is a thin per-day wrapper over HistoricalStore.
get_sentiment_daily_by_source_class (Sentiment Source Class Phase 0),
which already enforces the leakage-critical trading_day roll
(tests/test_historical_store_sentiment_audit.py). This file proves that
wrapping introduces no NEW leakage -- a document dated after the requested
end_day must never influence a day at/before end_day's S_t.

Per the repo convention (one dedicated file per subsystem's no-lookahead
guarantee)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data.historical_store import HistoricalStore
from signals.sentiment_index import compute_sentiment_index


def _doc(**overrides):
    base = dict(
        as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),  # 10:00 ET
        symbol="AAPL",
        source_name="gdelt",
        text_content="test",
        raw_sentiment_score=0.5,
    )
    base.update(overrides)
    return base


class TestNoLookahead:
    def test_document_after_end_day_never_appears_in_result(self, tmp_path):
        db = str(tmp_path / "s_t.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), final_weighted_score=0.3),
            _doc(as_of=datetime(2026, 7, 25, 14, 0, tzinfo=timezone.utc), final_weighted_score=-0.9),
        ])
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        assert set(result["AAPL"].keys()) == {"2026-07-21"}
        assert result["AAPL"]["2026-07-21"]["news_score"] == pytest.approx(0.3)

    def test_post_close_document_scores_the_next_trading_day_not_today(self, tmp_path):
        db = str(tmp_path / "s_t.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 7, 21, 20, 1, tzinfo=timezone.utc),  # 16:01 ET
                 final_weighted_score=0.9),
        ])
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-22", w1=0.4, w2=0.1, historical_store=store
            )
        assert "2026-07-21" not in result["AAPL"]
        assert result["AAPL"]["2026-07-22"]["news_score"] == pytest.approx(0.9)

    def test_widening_end_day_never_changes_an_earlier_days_score(self, tmp_path):
        """Requesting a wider [start, end] range must not retroactively
        change an already-computed earlier day's S_t -- each day's score
        is independent of the query window's boundaries."""
        db = str(tmp_path / "s_t.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc), final_weighted_score=0.2),
            _doc(as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), final_weighted_score=0.8),
        ])
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            narrow = compute_sentiment_index(
                ["AAPL"], "2026-07-10", "2026-07-10", w1=0.4, w2=0.1, historical_store=store
            )
            wide = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        assert narrow["AAPL"]["2026-07-10"]["news_score"] == wide["AAPL"]["2026-07-10"]["news_score"]
