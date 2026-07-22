"""No-lookahead test for the sentiment ingestion pipeline (Phases 2-4).

The leakage-critical rule this pipeline exists to enforce: a document
published at/after the US market close cannot influence that trading day's
credibility-weighted sentiment aggregate -- it must be attributed to the
NEXT trading day. This exercises the real chain end-to-end (not just each
layer's own unit tests): CompositeSentimentSource._archive() -> credibility
scoring -> HistoricalStore.save_sentiment_documents() ->
HistoricalStore.get_sentiment_aggregate_by_symbol(), the same read path
NewsCatalystSignal.pre_compute() uses.

Per the repo convention (one dedicated file per subsystem's no-lookahead
guarantee -- see tests/test_pairs_lookahead.py, tests/test_hmm_no_lookahead.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data.historical_store import HistoricalStore
from data.sentiment_sources import CompositeSentimentSource, SentimentDocument


def _doc(as_of: datetime, score: float) -> SentimentDocument:
    return SentimentDocument(
        as_of=as_of,
        symbol="AAPL",
        source_name="finnhub",
        text_content="test headline",
        raw_sentiment_score=score,
    )


class TestSentimentPipelinePITLookahead:
    def test_post_close_document_excluded_from_same_day_aggregate(self, tmp_path):
        db = str(tmp_path / "sentiment_pit.db")

        same_day_doc = _doc(datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), score=0.9)  # 10:00 ET
        post_close_doc = _doc(datetime(2026, 7, 21, 20, 1, tzinfo=timezone.utc), score=-0.9)  # 16:01 ET

        with patch("data.historical_store.HistoricalStore", lambda: HistoricalStore(db_path=db)):
            CompositeSentimentSource._archive([same_day_doc, post_close_doc])

        store = HistoricalStore(db_path=db)
        today = store.get_sentiment_aggregate_by_symbol("2026-07-21")
        tomorrow = store.get_sentiment_aggregate_by_symbol("2026-07-22")

        # Only the same-day document's score reaches today's aggregate.
        assert today["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(0.9)
        # The post-close document's negative score is fully attributed to
        # the NEXT trading day, never diluting or flipping today's aggregate.
        assert tomorrow["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(-0.9)

    def test_news_catalyst_reads_only_todays_bucket(self, tmp_path):
        """End-to-end through the actual signal's read path: pre_compute's
        _read_sentiment_credibility_aggregate must resolve "today" via the
        same resolve_trading_day() rule the write side used, so a post-close
        document from yesterday's session never leaks into today's cycle."""
        from signals.news_catalyst import NewsCatalystSignal

        db = str(tmp_path / "sentiment_pit.db")
        # A document that landed on trading_day "2026-07-21" (via the write-side
        # roll) must NOT appear when the signal resolves "now" to 2026-07-22.
        with patch("data.historical_store.HistoricalStore", lambda: HistoricalStore(db_path=db)):
            CompositeSentimentSource._archive([
                _doc(datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), score=0.9),
            ])

        sig = object.__new__(NewsCatalystSignal)
        sig._news_scores = {}
        sig._earnings_dt = {}
        sig._sentiment_credibility = {}

        fixed_now = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)  # next session
        with patch("data.historical_store.HistoricalStore", lambda: HistoricalStore(db_path=db)):
            with patch("signals.news_catalyst.datetime") as mock_dt:
                mock_dt.now.return_value = fixed_now
                sig._read_sentiment_credibility_aggregate()

        # 2026-07-21's document must not appear in 2026-07-22's read.
        assert sig._sentiment_credibility == {}
