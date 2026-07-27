"""Tests for signals/sentiment_index.py -- the composite sentiment index
S_t = w1*news_score + w2*review_score."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.historical_store import HistoricalStore
from signals.sentiment_index import compute_sentiment_index


class TestGatingAndEmptyInputs:
    def test_disabled_returns_empty_without_touching_store(self):
        store = MagicMock()
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", False):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", historical_store=store
            )
        assert result == {}
        store.get_sentiment_daily_by_source_class.assert_not_called()

    def test_empty_symbols_returns_empty(self):
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index([], "2026-07-01", "2026-07-21")
        assert result == {}

    def test_store_read_failure_degrades_to_empty(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.side_effect = RuntimeError("db down")
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", historical_store=store
            )
        assert result == {}


class TestWeightResolution:
    def test_defaults_to_sector_selection_weights(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {"2026-07-21": {"news_mean_score": 0.5, "comment_mean_score": 0.2}}
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_W1", 0.4), \
             patch("settings.settings.SECTOR_SELECTION_W2", 0.1):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", historical_store=store
            )
        s_t = result["AAPL"]["2026-07-21"]["s_t"]
        assert s_t == pytest.approx(0.4 * 0.5 + 0.1 * 0.2)

    def test_explicit_w1_w2_override_settings(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {"2026-07-21": {"news_mean_score": 1.0, "comment_mean_score": 1.0}}
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.7, w2=0.3, historical_store=store
            )
        assert result["AAPL"]["2026-07-21"]["s_t"] == pytest.approx(1.0)


class TestHonestDegradation:
    def test_review_unavailable_uses_news_only_not_zero_filled(self):
        """CONSTRAINT #4: w1*news + w2*0 would assert a neutral review
        sentiment never observed -- must be w1*news alone."""
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {"2026-07-21": {"news_mean_score": 0.6, "comment_mean_score": float("nan")}}
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        day = result["AAPL"]["2026-07-21"]
        assert day["s_t"] == pytest.approx(0.4 * 0.6)
        assert day["review_score"] is None
        assert day["news_score"] == pytest.approx(0.6)
        assert day["degraded_reason"] == "review_unavailable"

    def test_news_unavailable_uses_review_only(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {"2026-07-21": {"news_mean_score": float("nan"), "comment_mean_score": -0.3}}
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        day = result["AAPL"]["2026-07-21"]
        assert day["s_t"] == pytest.approx(0.1 * -0.3)
        assert day["news_score"] is None
        assert day["degraded_reason"] == "news_unavailable"

    def test_both_unavailable_s_t_is_none_not_zero(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {"2026-07-21": {
                "news_mean_score": float("nan"), "comment_mean_score": float("nan"),
            }}
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", historical_store=store
            )
        day = result["AAPL"]["2026-07-21"]
        assert day["s_t"] is None
        assert day["news_score"] is None
        assert day["review_score"] is None
        assert day["degraded_reason"] == "no_data"

    def test_both_available_no_degradation(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {"2026-07-21": {"news_mean_score": 0.5, "comment_mean_score": 0.5}}
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        day = result["AAPL"]["2026-07-21"]
        assert day["degraded_reason"] is None
        assert day["s_t"] == pytest.approx(0.25)


class TestMultiSymbolMultiDay:
    def test_independent_per_symbol_per_day(self):
        store = MagicMock()
        store.get_sentiment_daily_by_source_class.return_value = {
            "AAPL": {
                "2026-07-20": {"news_mean_score": 0.2, "comment_mean_score": 0.1},
                "2026-07-21": {"news_mean_score": 0.8, "comment_mean_score": -0.2},
            },
            "MSFT": {
                "2026-07-21": {"news_mean_score": -0.5, "comment_mean_score": float("nan")},
            },
        }
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL", "MSFT"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert set(result["AAPL"].keys()) == {"2026-07-20", "2026-07-21"}
        assert result["MSFT"]["2026-07-21"]["degraded_reason"] == "review_unavailable"


class TestEndToEndAgainstRealHistoricalStore:
    def _doc(self, **overrides):
        base = dict(
            as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),
            symbol="AAPL",
            source_name="gdelt",
            text_content="test",
            raw_sentiment_score=0.5,
        )
        base.update(overrides)
        return base

    def test_real_store_reuses_finbert_scores_no_rescoring(self, tmp_path):
        db = str(tmp_path / "sentiment_index.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            self._doc(source_name="gdelt", final_weighted_score=0.4),
            self._doc(source_name="reddit", final_weighted_score=-0.2),
        ])
        with patch("settings.settings.SENTIMENT_INDEX_ENABLED", True):
            result = compute_sentiment_index(
                ["AAPL"], "2026-07-01", "2026-07-21", w1=0.4, w2=0.1, historical_store=store
            )
        day = result["AAPL"]["2026-07-21"]
        assert day["news_score"] == pytest.approx(0.4)
        assert day["review_score"] == pytest.approx(-0.2)
        assert day["s_t"] == pytest.approx(0.4 * 0.4 + 0.1 * -0.2)
        assert day["degraded_reason"] is None
