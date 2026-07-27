"""End-to-end proof that the Review term genuinely lights up once real
comment-class documents exist -- tying together Sentiment Source Class
Phase 0 (data/sentiment_source_class.py's classify_source, PR #441),
Sector Selection's Sector Heat Factor (data/sector_selection_heat.py,
PR #442), and this PR's StockTwitsSource / Reddit-classification wiring.

Before this test existed, the "Review is unavailable" degraded path
(tests/test_sector_selection_heat.py) and the classifier's "reddit ->
comment" unit test (tests/test_sentiment_source_class.py) were verified
independently, but nothing proved the full pipe: real Reddit/StockTwits-
sourced sentiment_ingestion_audit rows -> get_sentiment_daily_by_source_
class's comment bucket -> compute_spec_sector_heat's honest, non-degraded
review_volume."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data.historical_store import HistoricalStore
from data.sector_selection_heat import compute_spec_sector_heat


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


class TestReviewTermGenuinelyPopulated:
    def test_reddit_sourced_documents_clear_the_degraded_flag(self, tmp_path):
        db = str(tmp_path / "review_populated.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(source_name="gdelt", final_weighted_score=0.4),
            _doc(source_name="reddit", final_weighted_score=0.2),
            _doc(source_name="reddit", final_weighted_score=-0.1),
        ])

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"],
                ticker_sector_map={"AAPL": "Technology"},
                as_of=datetime(2026, 7, 21, 20, 30, tzinfo=timezone.utc),  # post-close
                historical_store=store,
            )

        assert result["Technology"]["degraded_reason"] is None
        assert result["Technology"]["review_volume"] == pytest.approx(2.0)
        assert not math.isnan(result["Technology"]["shf"])

    def test_stocktwits_sourced_documents_also_clear_the_degraded_flag(self, tmp_path):
        """StockTwits (this PR's new source) is classified as 'comment'
        just as reliably as Reddit -- classify_source doesn't special-case
        either name beyond membership in SENTIMENT_COMMENT_SOURCES."""
        db = str(tmp_path / "review_populated.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(source_name="gdelt", final_weighted_score=0.4),
            _doc(source_name="stocktwits", final_weighted_score=0.3),
        ])

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"],
                ticker_sector_map={"AAPL": "Technology"},
                as_of=datetime(2026, 7, 21, 20, 30, tzinfo=timezone.utc),
                historical_store=store,
            )

        assert result["Technology"]["degraded_reason"] is None
        assert result["Technology"]["review_volume"] == pytest.approx(1.0)

    def test_without_any_comment_source_the_flag_still_degrades(self, tmp_path):
        """Control: the SAME setup with only news-class rows must still
        show the honest degraded path -- proving the two prior tests pass
        BECAUSE of the comment rows, not despite them."""
        db = str(tmp_path / "review_populated.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(source_name="gdelt", final_weighted_score=0.4),
            _doc(source_name="yahoo_rss", final_weighted_score=0.1),
        ])

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"],
                ticker_sector_map={"AAPL": "Technology"},
                as_of=datetime(2026, 7, 21, 20, 30, tzinfo=timezone.utc),
                historical_store=store,
            )

        assert result["Technology"]["degraded_reason"] == "review_unavailable"
        assert math.isnan(result["Technology"]["review_volume"])

    def test_review_volume_reflects_a_genuine_quiet_window_once_channel_is_active(self, tmp_path):
        """Once the channel has EVER produced a document (elsewhere in
        history), a window with zero comment rows for THIS sector is a
        real, trusted zero -- not a re-triggered degraded flag."""
        db = str(tmp_path / "review_populated.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            # Historical proof the channel is active, for a DIFFERENT symbol.
            _doc(symbol="MSFT", source_name="reddit",
                 as_of=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)),
            # This cycle's actual news for the symbol under test -- no
            # comment-class row this specific window.
            _doc(symbol="AAPL", source_name="gdelt", final_weighted_score=0.4),
        ])

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"],
                ticker_sector_map={"AAPL": "Technology", "MSFT": "Technology"},
                as_of=datetime(2026, 7, 21, 20, 30, tzinfo=timezone.utc),
                historical_store=store,
            )

        assert result["Technology"]["degraded_reason"] is None
        assert result["Technology"]["review_volume"] == pytest.approx(0.0)
