"""
tests/test_news_catalyst.py
============================
Unit tests for ``signals.news_catalyst`` (Tier 2.4).

All Finnhub and transformers network calls are monkeypatched; no real
API requests are made.

Coverage
--------
TestLexiconSentiment    — positive, negative, neutral, mixed headlines
TestEarningsProximity   — suppress within 48h, dampen within 7 days, pass beyond
TestScoreHeadline       — FinBERT path, fallback to lexicon, empty headline
TestSignalCompute       — compute() reads from pre-computed cache; absent → 0.0
TestPreCompute          — batch-fetch populates context fields; no-key → 0.0
TestRegistration        — 'news_catalyst' in global_registry
TestGracefulDegradation — API error → 0.0; all-error batch → no crash
TestEarningsProximityEdge — boundary conditions for the proximity multiplier
TestContextPopulation   — pre_compute writes news_sentiment_scores + earnings_dates
"""

import os
import types
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest import mock
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from signals.news_catalyst import (
    NewsCatalystSignal,
    _earnings_proximity_multiplier,
    _lexicon_sentiment,
    _score_headline,
    fetch_company_news,
    fetch_next_earnings,
)


# ===========================================================================
# Helper fixtures
# ===========================================================================

def _make_signal() -> NewsCatalystSignal:
    """Return a fresh signal instance without auto-registration side-effects."""
    s = object.__new__(NewsCatalystSignal)
    s._news_scores = {}
    s._earnings_dt = {}
    return s


def _make_context(**kwargs):
    """Minimal SignalContext-like object for testing pre_compute."""
    ctx = types.SimpleNamespace(
        news_sentiment_scores={},
        earnings_dates={},
        **kwargs,
    )
    return ctx


def _make_universe(symbols):
    return pd.DataFrame({"Symbol": symbols})


# ===========================================================================
# TestLexiconSentiment
# ===========================================================================

class TestLexiconSentiment:
    def test_positive_headline(self):
        score = _lexicon_sentiment("Company beats earnings expectations and raises guidance")
        assert score > 0

    def test_negative_headline(self):
        # Unambiguous negatives: "crashes", "losses", "fraud", "investigation"
        score = _lexicon_sentiment("Stock crashes as losses mount and fraud investigation widens")
        assert score < 0

    def test_neutral_headline(self):
        score = _lexicon_sentiment("Company reports quarterly financial results")
        assert score == 0.0

    def test_empty_headline(self):
        assert _lexicon_sentiment("") == 0.0

    def test_mixed_headline(self):
        # Equal positive and negative hits → 0
        score = _lexicon_sentiment("record losses and strong gains today")
        # "record" positive, "losses" negative, "strong" positive, "gains" positive
        # That's 3 positive, 1 negative → positive score
        assert isinstance(score, float)
        assert -1.0 <= score <= 1.0

    def test_score_bounds(self):
        for headline in [
            "beat beat beat record rally soar surge jump",
            "miss loss fail bankrupt fraud crash decline",
            "",
        ]:
            score = _lexicon_sentiment(headline)
            assert -1.0 <= score <= 1.0


# ===========================================================================
# TestEarningsProximity
# ===========================================================================

class TestEarningsProximity:
    NOW = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)

    def test_no_earnings_returns_one(self):
        m = _earnings_proximity_multiplier(None, self.NOW, 48.0, 7.0)
        assert m == 1.0

    def test_suppress_within_48h(self):
        soon = self.NOW + timedelta(hours=24)
        m = _earnings_proximity_multiplier(soon, self.NOW, 48.0, 7.0)
        assert m == 0.0

    def test_suppress_exactly_at_boundary(self):
        at_boundary = self.NOW + timedelta(hours=48)
        m = _earnings_proximity_multiplier(at_boundary, self.NOW, 48.0, 7.0)
        assert m == 0.0

    def test_dampen_within_7_days(self):
        mid = self.NOW + timedelta(days=4)
        m = _earnings_proximity_multiplier(mid, self.NOW, 48.0, 7.0)
        assert m == 0.5

    def test_full_signal_beyond_7_days(self):
        far = self.NOW + timedelta(days=10)
        m = _earnings_proximity_multiplier(far, self.NOW, 48.0, 7.0)
        assert m == 1.0

    def test_post_earnings_within_24h_dampened(self):
        recent = self.NOW - timedelta(hours=12)
        m = _earnings_proximity_multiplier(recent, self.NOW, 48.0, 7.0)
        assert m == 0.5

    def test_post_earnings_beyond_24h_full(self):
        old = self.NOW - timedelta(hours=36)
        m = _earnings_proximity_multiplier(old, self.NOW, 48.0, 7.0)
        assert m == 1.0

    def test_custom_thresholds(self):
        # Custom suppress=24h, dampen=3 days
        now = self.NOW
        m_suppress = _earnings_proximity_multiplier(now + timedelta(hours=12), now, 24.0, 3.0)
        m_dampen = _earnings_proximity_multiplier(now + timedelta(hours=48), now, 24.0, 3.0)
        m_full = _earnings_proximity_multiplier(now + timedelta(days=4), now, 24.0, 3.0)
        assert m_suppress == 0.0
        assert m_dampen == 0.5
        assert m_full == 1.0


# ===========================================================================
# TestScoreHeadline
# ===========================================================================

class TestScoreHeadline:
    def test_empty_headline_returns_zero(self):
        assert _score_headline("", None) == 0.0
        assert _score_headline("", MagicMock()) == 0.0

    def test_lexicon_fallback_when_no_pipeline(self):
        score = _score_headline("Company beats expectations", None)
        assert score > 0

    def test_finbert_positive(self):
        fake_pipeline = MagicMock(return_value=[{"label": "positive", "score": 0.95}])
        score = _score_headline("Revenue surges 30%", fake_pipeline)
        assert abs(score - 0.95) < 1e-6

    def test_finbert_negative(self):
        fake_pipeline = MagicMock(return_value=[{"label": "negative", "score": 0.88}])
        score = _score_headline("Company faces bankruptcy", fake_pipeline)
        assert abs(score - (-0.88)) < 1e-6

    def test_finbert_neutral(self):
        fake_pipeline = MagicMock(return_value=[{"label": "neutral", "score": 0.70}])
        score = _score_headline("Company releases report", fake_pipeline)
        assert score == 0.0

    def test_finbert_error_falls_back_to_lexicon(self):
        broken_pipeline = MagicMock(side_effect=RuntimeError("model error"))
        score = _score_headline("record profits and strong growth", broken_pipeline)
        # Falls back to lexicon — should be positive
        assert score > 0

    def test_score_clamped_to_bounds(self):
        # The score should always be in [-1, +1]
        for pipeline in [None]:
            for h in ["beat gain profit", "miss loss fraud"]:
                s = _score_headline(h, pipeline)
                assert -1.0 <= s <= 1.0


# ===========================================================================
# TestSignalCompute
# ===========================================================================

class TestSignalCompute:
    def _make_row(self, symbol: str) -> pd.Series:
        return pd.Series({"Symbol": symbol, "Ticker": symbol})

    def test_compute_reads_cached_score(self):
        sig = _make_signal()
        sig._news_scores = {"AAPL": 0.75}
        sig._earnings_dt = {"AAPL": None}
        out = sig.compute(self._make_row("AAPL"), _make_context())
        assert abs(out.score - 0.75) < 1e-6
        assert out.confidence == 0.75

    def test_compute_absent_symbol_returns_zero(self):
        sig = _make_signal()
        out = sig.compute(self._make_row("TSLA"), _make_context())
        assert out.score == 0.0
        assert out.confidence == 0.5

    def test_compute_negative_score(self):
        sig = _make_signal()
        sig._news_scores = {"MSFT": -0.5}
        sig._earnings_dt = {}
        out = sig.compute(self._make_row("MSFT"), _make_context())
        assert out.score < 0

    def test_compute_explanation_contains_direction(self):
        sig = _make_signal()
        sig._news_scores = {"AAPL": 0.6}
        sig._earnings_dt = {}
        out = sig.compute(self._make_row("aapl"), _make_context())  # lowercase
        assert "positive" in out.explanation.lower()

    def test_compute_explanation_includes_earnings_date(self):
        sig = _make_signal()
        now = datetime.now(timezone.utc)
        sig._news_scores = {"NVDA": 0.3}
        sig._earnings_dt = {"NVDA": now + timedelta(days=5)}
        out = sig.compute(self._make_row("NVDA"), _make_context())
        assert "earnings" in out.explanation.lower()

    def test_compute_output_score_in_bounds(self):
        sig = _make_signal()
        for score in [0.9, -0.9, 0.0, 1.0, -1.0]:
            sig._news_scores = {"X": score}
            sig._earnings_dt = {}
            out = sig.compute(self._make_row("X"), _make_context())
            assert -1.0 <= out.score <= 1.0


# ===========================================================================
# TestPreCompute
# ===========================================================================

class TestPreCompute:
    def test_no_api_key_gives_zero_scores(self):
        """pre_compute returns 0.0 for all symbols when FINNHUB_API_KEY is absent."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL", "MSFT"])
        with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}, clear=False):
            sig.pre_compute(universe, ctx)
        # When no key, caches should be empty (module logs info and returns)
        assert sig._news_scores == {}

    def test_finnhub_api_error_per_symbol_resilient(self):
        """per-symbol Finnhub errors do not abort the batch."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL", "MSFT", "GOOG"])
        mock_client = MagicMock()
        mock_client.company_news.side_effect = RuntimeError("rate limit")
        mock_client.earnings_calendar.return_value = {"earningsCalendar": []}
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                    sig.pre_compute(universe, ctx)
        # All symbols should have 0.0 scores (error path)
        for sym in ["AAPL", "MSFT", "GOOG"]:
            assert sig._news_scores.get(sym, 0.0) == 0.0

    def test_pre_compute_populates_context_fields(self):
        """pre_compute writes news_sentiment_scores and earnings_dates into context."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL"])
        mock_client = MagicMock()
        mock_client.company_news.return_value = [
            {"headline": "Apple beats earnings expectations"}
        ]
        mock_client.earnings_calendar.return_value = {"earningsCalendar": []}
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                    with patch("signals.news_catalyst.time.sleep"):  # skip courtesy delay
                        sig.pre_compute(universe, ctx)
        assert isinstance(ctx.news_sentiment_scores, dict)
        assert isinstance(ctx.earnings_dates, dict)
        assert "AAPL" in ctx.news_sentiment_scores

    def test_pre_compute_empty_news_gives_zero(self):
        """Empty news list → sentiment score 0.0 (no crash)."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL"])
        mock_client = MagicMock()
        mock_client.company_news.return_value = []
        mock_client.earnings_calendar.return_value = {"earningsCalendar": []}
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                    with patch("signals.news_catalyst.time.sleep"):
                        sig.pre_compute(universe, ctx)
        assert sig._news_scores.get("AAPL", -999) == 0.0

    def test_pre_compute_earnings_suppresses_score(self):
        """News score is zeroed when earnings are within 48h."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL"])
        now = datetime.now(timezone.utc)
        soon = (now + timedelta(hours=24)).strftime("%Y-%m-%d")

        mock_client = MagicMock()
        mock_client.company_news.return_value = [
            {"headline": "Apple beats and surges"}
        ]
        mock_client.earnings_calendar.return_value = {
            "earningsCalendar": [{"date": soon}]
        }
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                    with patch("signals.news_catalyst.time.sleep"):
                        sig.pre_compute(universe, ctx)
        # Earnings within 48h → score should be 0.0 (suppressed)
        assert sig._news_scores.get("AAPL", -999) == 0.0

    def test_pre_compute_empty_universe(self):
        """Empty universe DataFrame → no crash, empty caches."""
        sig = _make_signal()
        ctx = _make_context()
        universe = pd.DataFrame({"Symbol": []})
        mock_client = MagicMock()
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                sig.pre_compute(universe, ctx)
        assert sig._news_scores == {}


# ===========================================================================
# TestNewsHistoryArchive -- forward-archive write hook (no backtest reads this yet)
# ===========================================================================

class TestNewsHistoryArchive:
    def test_pre_compute_archives_scores_when_enabled(self):
        """pre_compute() writes the cycle's scores via HistoricalStore.save_news_sentiment
        when settings.NEWS_HISTORY_CAPTURE_ENABLED is True (the default)."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL"])
        mock_client = MagicMock()
        mock_client.company_news.return_value = [{"headline": "Apple beats"}]
        mock_client.earnings_calendar.return_value = {"earningsCalendar": []}

        mock_store_instance = MagicMock()
        mock_store_cls = MagicMock(return_value=mock_store_instance)

        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                    with patch("signals.news_catalyst.time.sleep"):
                        with patch("data.historical_store.HistoricalStore", mock_store_cls):
                            sig.pre_compute(universe, ctx)

        mock_store_cls.assert_called_once()
        mock_store_instance.save_news_sentiment.assert_called_once()
        call_args = mock_store_instance.save_news_sentiment.call_args
        assert call_args[0][0] == sig._news_scores

    def test_archive_disabled_skips_write(self):
        """settings.NEWS_HISTORY_CAPTURE_ENABLED=False must skip the write entirely."""
        mock_store_cls = MagicMock()
        with patch("settings.settings.NEWS_HISTORY_CAPTURE_ENABLED", False):
            with patch("data.historical_store.HistoricalStore", mock_store_cls):
                NewsCatalystSignal._archive_news_history({"AAPL": 0.5})
        mock_store_cls.assert_not_called()

    def test_archive_failure_never_propagates(self):
        """CONSTRAINT #6: a HistoricalStore failure inside the archive hook must
        never raise out of pre_compute (or the standalone helper)."""
        mock_store_cls = MagicMock(side_effect=RuntimeError("db unavailable"))
        with patch("data.historical_store.HistoricalStore", mock_store_cls):
            NewsCatalystSignal._archive_news_history({"AAPL": 0.5})  # must not raise

    def test_empty_scores_does_not_construct_store(self):
        mock_store_cls = MagicMock()
        with patch("data.historical_store.HistoricalStore", mock_store_cls):
            NewsCatalystSignal._archive_news_history({})
        mock_store_cls.assert_not_called()


# ===========================================================================
# TestRegistration
# ===========================================================================

class TestRegistration:
    def test_news_catalyst_in_registry(self):
        from signals.registry import global_registry
        import signals.news_catalyst  # noqa: F401 — ensure import side-effect ran
        assert "news_catalyst" in global_registry.get_all()

    def test_registered_instance_is_NewsCatalystSignal(self):
        from signals.registry import global_registry
        mod = global_registry.get_all().get("news_catalyst")
        assert isinstance(mod, NewsCatalystSignal)

    def test_signal_weight_configured(self):
        from settings import settings
        assert "news_catalyst" in settings.SIGNAL_WEIGHTS
        assert settings.SIGNAL_WEIGHTS["news_catalyst"] >= 0.0


# ===========================================================================
# TestFetchHelpers (offline — monkeypatched)
# ===========================================================================

class TestFetchHelpers:
    def test_fetch_company_news_error_returns_empty(self):
        mock_client = MagicMock()
        mock_client.company_news.side_effect = RuntimeError("timeout")
        result = fetch_company_news(mock_client, "AAPL", 7)
        assert result == []

    def test_fetch_next_earnings_error_returns_none(self):
        mock_client = MagicMock()
        mock_client.earnings_calendar.side_effect = ValueError("bad request")
        result = fetch_next_earnings(mock_client, "AAPL")
        assert result is None

    def test_fetch_company_news_returns_list(self):
        mock_client = MagicMock()
        mock_client.company_news.return_value = [
            {"headline": "test news", "datetime": 1234567890}
        ]
        result = fetch_company_news(mock_client, "AAPL", 7)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_fetch_next_earnings_parses_future_date(self):
        mock_client = MagicMock()
        future = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")
        mock_client.earnings_calendar.return_value = {
            "earningsCalendar": [{"date": future}]
        }
        result = fetch_next_earnings(mock_client, "AAPL")
        assert result is not None
        assert result > datetime.now(timezone.utc)

    def test_fetch_next_earnings_empty_calendar_returns_none(self):
        mock_client = MagicMock()
        mock_client.earnings_calendar.return_value = {"earningsCalendar": []}
        result = fetch_next_earnings(mock_client, "AAPL")
        assert result is None


# ===========================================================================
# TestSettings
# ===========================================================================

class TestSettings:
    def test_news_lookback_days_positive(self):
        from settings import settings
        assert settings.NEWS_LOOKBACK_DAYS > 0

    def test_finbert_enabled_is_bool(self):
        from settings import settings
        assert isinstance(settings.FINBERT_ENABLED, bool)

    def test_suppress_hours_positive(self):
        from settings import settings
        assert settings.NEWS_EARNINGS_SUPPRESS_HOURS > 0

    def test_dampen_days_positive(self):
        from settings import settings
        assert settings.NEWS_EARNINGS_DAMPEN_DAYS > 0

    def test_suppress_less_than_dampen(self):
        from settings import settings
        # Suppress window (hours) should be less than dampen window (hours)
        assert settings.NEWS_EARNINGS_SUPPRESS_HOURS < settings.NEWS_EARNINGS_DAMPEN_DAYS * 24
