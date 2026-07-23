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
TestScoreHeadlinesBatched — batched score_headlines(): softmax shape, batching, fallback
TestScoreHeadlineBackwardCompatibility — _score_headline's pre-batching float contract
TestFinbertScoreCacheLookaheadSafety — content-hash keying carries no lookahead risk
TestScoreHeadlinesCache — content-hash cache dedup + HistoricalStore round-trip
TestSignalCompute       — compute() reads from pre-computed cache; absent → 0.0
TestPreCompute          — batch-fetch populates context fields; no-key → 0.0
TestRegistration        — 'news_catalyst' in global_registry
TestGracefulDegradation — API error → 0.0; all-error batch → no crash
TestEarningsProximityEdge — boundary conditions for the proximity multiplier
TestContextPopulation   — pre_compute writes news_sentiment_scores + earnings_dates
TestRegimeGate          — is_active_in_regime suppression + SignalAggregator wiring
"""

import os
import types
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest import mock
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from signals.aggregator import SignalAggregator
from signals.base import SignalContext
from signals.news_catalyst import (
    NewsCatalystSignal,
    _content_hash,
    _distribution_to_signed,
    _earnings_proximity_multiplier,
    _lexicon_sentiment,
    _lexicon_softmax,
    _score_headline,
    fetch_company_news,
    fetch_next_earnings,
    score_headlines,
)
from signals.registry import SignalRegistry


@pytest.fixture(autouse=True)
def _mock_multi_source_ingestion(monkeypatch):
    """Prevent every test in this file from making real network calls via
    the Sentiment Pipeline Phase 3/4 multi-source ingestion path --
    NewsCatalystSignal.pre_compute() now unconditionally calls
    data.sentiment_sources.get_sentiment_source() (see
    _run_multi_source_ingestion), independent of Finnhub configuration.
    Without this, Yahoo RSS/GDELT/Reddit/EDGAR would all be hit for real on
    every pre_compute() call in this file.
    """
    monkeypatch.setattr(
        "data.sentiment_sources.get_sentiment_source", lambda: MagicMock()
    )


# ===========================================================================
# Helper fixtures
# ===========================================================================

def _make_signal() -> NewsCatalystSignal:
    """Return a fresh signal instance without auto-registration side-effects."""
    s = object.__new__(NewsCatalystSignal)
    s._news_scores = {}
    s._earnings_dt = {}
    s._sentiment_credibility = {}
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
    """_score_headline() is now a thin, cache-bypassing wrapper around the
    batched score_headlines() -- the fake pipelines below return the full
    3-class softmax (a list-of-lists, one list of label/score dicts per
    input) that a real ``top_k=None`` FinBERT pipeline call would produce,
    matching score_headlines()'s calling convention (see TestScoreHeadline
    BackwardCompatibility for the float-contract-unchanged proof)."""

    def test_empty_headline_returns_zero(self):
        assert _score_headline("", None) == 0.0
        assert _score_headline("", MagicMock()) == 0.0

    def test_lexicon_fallback_when_no_pipeline(self):
        score = _score_headline("Company beats expectations", None)
        assert score > 0

    def test_finbert_positive(self):
        fake_pipeline = MagicMock(return_value=[[
            {"label": "positive", "score": 0.95},
            {"label": "neutral", "score": 0.03},
            {"label": "negative", "score": 0.02},
        ]])
        score = _score_headline("Revenue surges 30%", fake_pipeline)
        # positive - negative net probability mass
        assert abs(score - (0.95 - 0.02)) < 1e-6

    def test_finbert_negative(self):
        fake_pipeline = MagicMock(return_value=[[
            {"label": "negative", "score": 0.88},
            {"label": "neutral", "score": 0.09},
            {"label": "positive", "score": 0.03},
        ]])
        score = _score_headline("Company faces bankruptcy", fake_pipeline)
        assert abs(score - (0.03 - 0.88)) < 1e-6

    def test_finbert_neutral(self):
        fake_pipeline = MagicMock(return_value=[[
            {"label": "neutral", "score": 0.70},
            {"label": "positive", "score": 0.16},
            {"label": "negative", "score": 0.14},
        ]])
        score = _score_headline("Company releases report", fake_pipeline)
        assert abs(score - (0.16 - 0.14)) < 1e-6

    def test_finbert_error_falls_back_to_lexicon(self):
        broken_pipeline = MagicMock(side_effect=RuntimeError("model error"))
        score = _score_headline("record profits and strong growth", broken_pipeline)
        # Falls back to lexicon — should be positive
        assert score > 0

    def test_finbert_malformed_result_falls_back_to_lexicon(self):
        """A pipeline that doesn't return the expected shape (e.g. the old
        single-label dict, not a list of per-class dicts) must degrade to
        the lexicon rather than raise."""
        odd_pipeline = MagicMock(return_value=[{"label": "positive", "score": 0.95}])
        score = _score_headline("record profits and strong growth", odd_pipeline)
        assert score > 0  # lexicon fallback, still positive

    def test_score_clamped_to_bounds(self):
        # The score should always be in [-1, +1]
        for pipeline in [None]:
            for h in ["beat gain profit", "miss loss fraud"]:
                s = _score_headline(h, pipeline)
                assert -1.0 <= s <= 1.0


# ===========================================================================
# TestScoreHeadlinesBatched
# ===========================================================================

class TestScoreHeadlinesBatched:
    """score_headlines() -- the new batched scoring entry point."""

    def test_empty_list_returns_empty(self):
        assert score_headlines([]) == []

    def test_returns_full_softmax_dicts(self):
        result = score_headlines(
            ["Company beats expectations"], pipeline=None, use_cache=False
        )
        assert len(result) == 1
        dist = result[0]
        assert set(dist.keys()) == {"positive", "neutral", "negative"}
        assert all(isinstance(v, float) for v in dist.values())

    def test_order_and_count_preserved_lexicon_path(self):
        headlines = [
            "Company beats and surges",
            "Company misses and plunges",
            "Company issues quarterly report",
        ]
        result = score_headlines(headlines, pipeline=None, use_cache=False)
        assert len(result) == len(headlines)
        for headline, dist in zip(headlines, result):
            assert dist == pytest.approx(_lexicon_softmax(headline))

    def test_pipeline_none_uses_lexicon_for_every_headline(self):
        headlines = ["record profits", "steep losses", "no news"]
        result = score_headlines(headlines, pipeline=None, use_cache=False)
        for headline, dist in zip(headlines, result):
            assert dist == pytest.approx(_lexicon_softmax(headline))

    def test_batch_size_is_respected(self):
        headlines = [f"headline number {i} beats" for i in range(10)]
        observed_batch_sizes = []

        def fake_pipeline(batch, **kwargs):
            observed_batch_sizes.append(len(batch))
            return [
                [
                    {"label": "positive", "score": 0.9},
                    {"label": "neutral", "score": 0.05},
                    {"label": "negative", "score": 0.05},
                ]
                for _ in batch
            ]

        score_headlines(headlines, pipeline=fake_pipeline, batch_size=4, use_cache=False)
        assert observed_batch_sizes == [4, 4, 2]

    def test_default_batch_size_from_settings(self):
        headlines = [f"h{i}" for i in range(5)]
        observed_batch_sizes = []

        def fake_pipeline(batch, **kwargs):
            observed_batch_sizes.append(len(batch))
            return [
                [
                    {"label": "neutral", "score": 1.0},
                    {"label": "positive", "score": 0.0},
                    {"label": "negative", "score": 0.0},
                ]
                for _ in batch
            ]

        with patch("settings.settings.FINBERT_BATCH_SIZE", 2):
            score_headlines(headlines, pipeline=fake_pipeline, use_cache=False)
        assert observed_batch_sizes == [2, 2, 1]

    def test_order_preserved_finbert_path(self):
        headlines = ["Stock surges", "Company misses", "Neutral update"]

        def fake_pipeline(batch, **kwargs):
            out = []
            for text in batch:
                if "surges" in text:
                    out.append([
                        {"label": "positive", "score": 0.9},
                        {"label": "neutral", "score": 0.05},
                        {"label": "negative", "score": 0.05},
                    ])
                elif "misses" in text:
                    out.append([
                        {"label": "negative", "score": 0.8},
                        {"label": "neutral", "score": 0.15},
                        {"label": "positive", "score": 0.05},
                    ])
                else:
                    out.append([
                        {"label": "neutral", "score": 0.9},
                        {"label": "positive", "score": 0.05},
                        {"label": "negative", "score": 0.05},
                    ])
            return out

        result = score_headlines(headlines, pipeline=fake_pipeline, use_cache=False)
        assert result[0]["positive"] == pytest.approx(0.9)
        assert result[1]["negative"] == pytest.approx(0.8)
        assert result[2]["neutral"] == pytest.approx(0.9)

    def test_pipeline_exception_falls_back_to_lexicon_for_whole_batch(self):
        def broken_pipeline(batch, **kwargs):
            raise RuntimeError("model crashed")

        headlines = ["beat strong growth", "miss and fraud"]
        result = score_headlines(headlines, pipeline=broken_pipeline, use_cache=False)
        for headline, dist in zip(headlines, result):
            assert dist == pytest.approx(_lexicon_softmax(headline))

    def test_malformed_pipeline_output_length_falls_back_to_lexicon(self):
        """A pipeline returning the wrong number of results (shape mismatch)
        must degrade to the lexicon rather than mis-align results."""
        def short_pipeline(batch, **kwargs):
            return [[{"label": "positive", "score": 1.0}]]  # only 1 result for N inputs

        headlines = ["beat strong growth", "miss and fraud", "steady quarter"]
        result = score_headlines(headlines, pipeline=short_pipeline, use_cache=False)
        for headline, dist in zip(headlines, result):
            assert dist == pytest.approx(_lexicon_softmax(headline))


# ===========================================================================
# TestScoreHeadlineBackwardCompatibility
# ===========================================================================

class TestScoreHeadlineBackwardCompatibility:
    """Proves _score_headline() keeps its pre-batching contract: same
    signature, same float ∈ [-1, 1] return type — even though it is now a
    thin wrapper around the batched score_headlines(). Existing callers
    (data/sentiment_sources.py's several ``_score()`` helpers, which always
    pass pipeline=None) are unaffected."""

    def test_lexicon_path_matches_raw_lexicon_score_exactly(self):
        headline = "Company beats earnings expectations and raises guidance"
        expected = _lexicon_sentiment(headline)
        actual = _score_headline(headline, None)
        assert actual == pytest.approx(expected)

    def test_returns_a_plain_float(self):
        result = _score_headline("Stock crashes amid fraud probe", None)
        assert isinstance(result, float)
        assert -1.0 <= result <= 1.0

    def test_round_trips_through_score_headlines_for_a_single_item(self):
        """_score_headline(h, p) must equal
        _distribution_to_signed(score_headlines([h], pipeline=p)[0]) —
        i.e. it is genuinely just a thin wrapper, not a parallel
        implementation that could silently drift from the batched path."""
        headline = "Company beats and posts record profits"
        direct = _score_headline(headline, None)
        via_batch = _distribution_to_signed(
            score_headlines([headline], pipeline=None, use_cache=False)[0]
        )
        assert direct == pytest.approx(via_batch)

    def test_gdelt_style_caller_contract_unchanged(self):
        """Mirrors data/sentiment_sources.py's GDELTSource._score() (and the
        other _score() helpers), which all call
        `_score_headline(text, None)` — always the lexicon path."""
        text = "Company beats and posts record profits"
        score = _score_headline(text, None)
        assert isinstance(score, float)
        assert -1.0 <= score <= 1.0


# ===========================================================================
# TestFinbertScoreCacheLookaheadSafety
# ===========================================================================

class TestFinbertScoreCacheLookaheadSafety:
    """The finbert_score_cache is keyed on a SHA-256 hash of headline TEXT,
    not a date/cycle — this is deliberate and carries no lookahead risk
    (see the DDL comment in data/historical_store.py and the
    score_headlines()/_content_hash() docstrings). These tests establish
    the reasoning explicitly, in this codebase's house style for
    caching + point-in-time-discipline proofs (mirroring
    tests/test_sentiment_pit_lookahead.py)."""

    def test_content_hash_is_pure_function_of_text(self):
        """Same text -> same key, independent of when it is computed."""
        h1 = _content_hash("Apple beats earnings")
        h2 = _content_hash("Apple beats earnings")
        assert h1 == h2

    def test_different_text_gets_different_hash(self):
        assert _content_hash("Apple beats earnings") != _content_hash("Apple misses earnings")

    def test_lookup_cannot_surface_a_headline_this_cycle_never_fetched(self, tmp_path):
        """A cache HIT can only occur for text a caller actually hashed and
        looked up itself -- there is no mechanism for a lookup keyed by one
        headline's hash to return a DIFFERENT (e.g. not-yet-seen /
        future-cycle) headline's score. Even pre-seeding the cache with a
        score for text a 'later cycle' will eventually see cannot leak into
        an earlier cycle's read of unrelated text, because the lookup key is
        a hash of the text ITSELF, not a date or sequence number."""
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "lookahead.db"))
        future_headline = "Company announces blowout Q4 results"
        future_hash = _content_hash(future_headline)
        store.save_finbert_scores(
            {future_hash: {"positive": 0.99, "neutral": 0.0, "negative": 0.01}}
        )

        this_cycle_headline = "Company reports a routine operational update"
        result = store.get_finbert_score(_content_hash(this_cycle_headline))
        assert result is None  # distinct content -> distinct key -> no accidental hit

    def test_identical_text_reread_later_is_the_same_valid_score(self, tmp_path):
        """The flip side: once THIS cycle has legitimately scored and cached
        a headline, an IDENTICAL headline appearing in a later cycle's
        lookback window is correctly served the same score -- this is the
        intended cache hit, not a leak, since the text (and therefore the
        deterministic score) has not changed."""
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "identical.db"))
        headline = "Company beats and raises full-year guidance"
        h = _content_hash(headline)
        store.save_finbert_scores(
            {h: {"positive": 0.8, "neutral": 0.15, "negative": 0.05}}
        )
        # A later cycle asking about the SAME text gets the SAME score.
        result = store.get_finbert_score(_content_hash(headline))
        assert result == pytest.approx({"positive": 0.8, "neutral": 0.15, "negative": 0.05})


# ===========================================================================
# TestScoreHeadlinesCache
# ===========================================================================

class TestScoreHeadlinesCache:
    """score_headlines()'s content-hash cache: dedup across repeat calls and
    HistoricalStore round-trip persistence."""

    def _patched_store(self, tmp_path, monkeypatch, name="cache.db"):
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / name))
        monkeypatch.setattr(
            "data.historical_store.HistoricalStore", lambda *a, **kw: store
        )
        return store

    def test_repeated_headline_scored_once_via_finbert(self, tmp_path, monkeypatch):
        self._patched_store(tmp_path, monkeypatch)
        call_count = {"n": 0}

        def fake_pipeline(batch, **kwargs):
            call_count["n"] += 1
            return [
                [
                    {"label": "positive", "score": 0.9},
                    {"label": "neutral", "score": 0.05},
                    {"label": "negative", "score": 0.05},
                ]
                for _ in batch
            ]

        headline = "Company beats and raises full-year guidance"
        first = score_headlines([headline], pipeline=fake_pipeline)
        second = score_headlines([headline], pipeline=fake_pipeline)

        assert call_count["n"] == 1  # second call was a cache hit
        assert first[0] == pytest.approx(second[0])

    def test_repeated_headline_scored_once_via_lexicon(self, tmp_path, monkeypatch):
        self._patched_store(tmp_path, monkeypatch)
        headline = "Company beats and raises full-year guidance"

        with patch(
            "signals.news_catalyst._lexicon_sentiment", wraps=_lexicon_sentiment
        ) as spy:
            first = score_headlines([headline], pipeline=None)
            second = score_headlines([headline], pipeline=None)

        assert spy.call_count == 1  # second call was a cache hit
        assert first[0] == pytest.approx(second[0])

    def test_two_distinct_headlines_both_scored(self, tmp_path, monkeypatch):
        self._patched_store(tmp_path, monkeypatch)
        call_count = {"n": 0}

        def fake_pipeline(batch, **kwargs):
            call_count["n"] += len(batch)
            return [
                [
                    {"label": "positive", "score": 0.7},
                    {"label": "neutral", "score": 0.2},
                    {"label": "negative", "score": 0.1},
                ]
                for _ in batch
            ]

        score_headlines(["headline one beats"], pipeline=fake_pipeline)
        score_headlines(["headline two beats"], pipeline=fake_pipeline)
        assert call_count["n"] == 2  # two distinct headlines, both cache misses

    def test_cache_disabled_setting_never_constructs_store(self):
        headline = "Company beats and raises full-year guidance"
        with patch("settings.settings.FINBERT_SCORE_CACHE_ENABLED", False):
            with patch("data.historical_store.HistoricalStore") as mock_cls:
                score_headlines([headline], pipeline=None)
        mock_cls.assert_not_called()

    def test_historical_store_disabled_never_constructs_store(self):
        headline = "Company beats and raises full-year guidance"
        with patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
            with patch("data.historical_store.HistoricalStore") as mock_cls:
                score_headlines([headline], pipeline=None)
        mock_cls.assert_not_called()

    def test_use_cache_false_never_constructs_store(self):
        headline = "Company beats and raises full-year guidance"
        with patch("data.historical_store.HistoricalStore") as mock_cls:
            score_headlines([headline], pipeline=None, use_cache=False)
        mock_cls.assert_not_called()

    def test_cache_store_unavailable_degrades_to_fresh_scoring(self):
        """CONSTRAINT #6: a broken cache store must never raise out of
        score_headlines(); it must simply score fresh instead."""
        headline = "Company beats and raises full-year guidance"
        with patch(
            "data.historical_store.HistoricalStore",
            side_effect=RuntimeError("db unavailable"),
        ):
            result = score_headlines([headline], pipeline=None)  # must not raise
        assert result[0] == pytest.approx(_lexicon_softmax(headline))


class TestHistoricalStoreFinbertScoreCache:
    """data/historical_store.py's finbert_score_cache table -- DDL, round
    trip, and dead-letter behavior (mirrors
    tests/test_historical_store_news_history.py's style for the sibling
    news_history table)."""

    def test_table_created_on_init(self, tmp_path):
        import sqlite3
        from data.historical_store import HistoricalStore

        db = str(tmp_path / "finbert.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='finbert_score_cache'"
            ).fetchone()
        assert row is not None

    def test_round_trip(self, tmp_path):
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "finbert2.db"))
        h = _content_hash("Some headline text")
        store.save_finbert_scores(
            {h: {"positive": 0.7, "neutral": 0.2, "negative": 0.1, "headline_snippet": "Some headline text"}}
        )
        result = store.get_finbert_score(h)
        assert result == pytest.approx({"positive": 0.7, "neutral": 0.2, "negative": 0.1})

    def test_cache_miss_returns_none(self, tmp_path):
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "finbert3.db"))
        assert store.get_finbert_score(_content_hash("never scored")) is None

    def test_upsert_overwrites_same_hash(self, tmp_path):
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "finbert4.db"))
        h = _content_hash("Company beats expectations")
        store.save_finbert_scores({h: {"positive": 0.1, "neutral": 0.8, "negative": 0.1}})
        store.save_finbert_scores({h: {"positive": 0.9, "neutral": 0.05, "negative": 0.05}})
        result = store.get_finbert_score(h)
        assert result == pytest.approx({"positive": 0.9, "neutral": 0.05, "negative": 0.05})

    def test_empty_scores_is_a_noop(self, tmp_path):
        import sqlite3
        from data.historical_store import HistoricalStore

        db = str(tmp_path / "finbert5.db")
        store = HistoricalStore(db_path=db)
        store.save_finbert_scores({})  # must not raise
        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM finbert_score_cache").fetchone()[0]
        assert count == 0

    def test_write_failure_is_swallowed(self, tmp_path, monkeypatch):
        """CONSTRAINT #6: a write failure must never raise out of this method."""
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "finbert6.db"))

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(store, "_now_utc_iso", _boom)
        store.save_finbert_scores({"deadbeef": {"positive": 0.5, "neutral": 0.3, "negative": 0.2}})  # must not raise

    def test_read_failure_returns_none(self, tmp_path, monkeypatch):
        """CONSTRAINT #6: a read failure must never raise; degrades to None."""
        from data.historical_store import HistoricalStore

        store = HistoricalStore(db_path=str(tmp_path / "finbert7.db"))
        # Break the session factory (session_scope(self.Session) calls
        # self.Session(), which raises TypeError against None) to simulate
        # a genuine read failure without touching sqlite internals directly.
        monkeypatch.setattr(store, "Session", None)
        assert store.get_finbert_score("deadbeef") is None


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
        # A bare, unconfigured MagicMock().get_finbert_score(...) call would
        # return a truthy, non-None MagicMock -- score_headlines() would
        # read that as a real cache HIT for every headline (instead of the
        # intended miss) and skip lexicon scoring entirely. Explicitly
        # returning None here keeps this test exercising the real lexicon
        # scoring path, matching its pre-batching intent.
        mock_store_instance.get_finbert_score.return_value = None
        mock_store_cls = MagicMock(return_value=mock_store_instance)

        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test_key"}):
            with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
                with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                    with patch("signals.news_catalyst.time.sleep"):
                        with patch("data.historical_store.HistoricalStore", mock_store_cls):
                            sig.pre_compute(universe, ctx)

        # pre_compute() now also constructs HistoricalStore for the Phase 4
        # credibility-aggregate read AND score_headlines()'s content-hash
        # cache check/write, in addition to this archive write -- assert
        # the write itself, not the raw constructor call count.
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

class TestMultiSourceIngestion:
    """_run_multi_source_ingestion() -- the only call site that invokes
    CompositeSentimentSource in the live pipeline. Without it,
    sentiment_ingestion_audit never accumulates rows regardless of how much
    time passes -- these tests pin down that it actually runs when enabled,
    and stays a true no-op (no network attempted) by default."""

    def test_disabled_by_default_is_a_noop(self):
        """SENTIMENT_INGESTION_ENABLED defaults False -- no source is ever
        constructed or called. This is the fast-path every other test file
        in this repo relies on (e.g. main.py/main_orchestrator.py tests that
        build a real universe and call pre_compute transitively)."""
        sig = _make_signal()
        with patch("data.sentiment_sources.get_sentiment_source") as mock_getter:
            sig._run_multi_source_ingestion(["AAPL"])
        mock_getter.assert_not_called()

    def test_calls_fetch_and_archive_for_every_symbol(self):
        sig = _make_signal()
        mock_source = MagicMock()
        with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
            with patch("data.sentiment_sources.get_sentiment_source", return_value=mock_source):
                sig._run_multi_source_ingestion(["AAPL", "MSFT", "GOOG"])
        assert mock_source.fetch_and_archive.call_count == 3
        called_symbols = {c.args[0] for c in mock_source.fetch_and_archive.call_args_list}
        assert called_symbols == {"AAPL", "MSFT", "GOOG"}

    def test_calls_reset_cycle_once(self):
        sig = _make_signal()
        mock_source = MagicMock()
        with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
            with patch("data.sentiment_sources.get_sentiment_source", return_value=mock_source):
                sig._run_multi_source_ingestion(["AAPL"])
        mock_source.reset_cycle.assert_called_once()

    def test_respects_sentiment_audit_enabled_gate(self):
        sig = _make_signal()
        mock_source = MagicMock()
        with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
            with patch("settings.settings.SENTIMENT_AUDIT_ENABLED", False):
                with patch("data.sentiment_sources.get_sentiment_source", return_value=mock_source):
                    sig._run_multi_source_ingestion(["AAPL"])
        mock_source.fetch_and_archive.assert_not_called()

    def test_per_symbol_failure_does_not_block_others(self):
        sig = _make_signal()
        mock_source = MagicMock()
        mock_source.fetch_and_archive.side_effect = [RuntimeError("boom"), None, None]
        with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
            with patch("data.sentiment_sources.get_sentiment_source", return_value=mock_source):
                sig._run_multi_source_ingestion(["AAPL", "MSFT", "GOOG"])  # must not raise
        assert mock_source.fetch_and_archive.call_count == 3

    def test_setup_failure_never_raises(self):
        sig = _make_signal()
        with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
            with patch(
                "data.sentiment_sources.get_sentiment_source",
                side_effect=RuntimeError("singleton construction failed"),
            ):
                sig._run_multi_source_ingestion(["AAPL"])  # must not raise

    def test_empty_symbol_list_is_a_noop(self):
        sig = _make_signal()
        mock_source = MagicMock()
        with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
            with patch("data.sentiment_sources.get_sentiment_source", return_value=mock_source):
                sig._run_multi_source_ingestion([])
        mock_source.fetch_and_archive.assert_not_called()

    def test_runs_regardless_of_finnhub_configuration(self):
        """pre_compute() must run multi-source ingestion even when
        FINNHUB_API_KEY is unset (Reddit/GDELT/EDGAR/Yahoo RSS don't need it),
        as long as SENTIMENT_INGESTION_ENABLED is explicitly turned on."""
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL"])
        mock_source = MagicMock()
        with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}, clear=False):
            with patch("settings.settings.SENTIMENT_INGESTION_ENABLED", True):
                with patch("data.sentiment_sources.get_sentiment_source", return_value=mock_source):
                    sig.pre_compute(universe, ctx)
        mock_source.fetch_and_archive.assert_called_once_with("AAPL")


class TestSentimentCredibilityBlend:
    """Sentiment Pipeline Phase 4 -- credibility-aggregate read + compute() blend."""

    def test_read_aggregate_populates_cache(self):
        sig = _make_signal()
        mock_store_instance = MagicMock()
        mock_store_instance.get_sentiment_aggregate_by_symbol.return_value = {
            "AAPL": {"credibility_weighted_sentiment": 0.5, "bot_activity_ratio": 0.1,
                     "aggregated_source_credibility": 0.8},
        }
        mock_store_cls = MagicMock(return_value=mock_store_instance)
        with patch("data.historical_store.HistoricalStore", mock_store_cls):
            sig._read_sentiment_credibility_aggregate()
        assert sig._sentiment_credibility == {
            "AAPL": {"credibility_weighted_sentiment": 0.5, "bot_activity_ratio": 0.1,
                     "aggregated_source_credibility": 0.8},
        }

    def test_read_aggregate_failure_degrades_to_empty(self):
        sig = _make_signal()
        with patch("data.historical_store.HistoricalStore", side_effect=RuntimeError("db down")):
            sig._read_sentiment_credibility_aggregate()  # must not raise
        assert sig._sentiment_credibility == {}

    def test_pre_compute_populates_context_sentiment_credibility_scores(self):
        sig = _make_signal()
        ctx = _make_context()
        universe = _make_universe(["AAPL"])
        mock_store_instance = MagicMock()
        mock_store_instance.get_sentiment_aggregate_by_symbol.return_value = {
            "AAPL": {"credibility_weighted_sentiment": 0.3, "bot_activity_ratio": 0.0,
                     "aggregated_source_credibility": 1.0},
        }
        mock_store_cls = MagicMock(return_value=mock_store_instance)
        with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}, clear=False):
            with patch("data.historical_store.HistoricalStore", mock_store_cls):
                sig.pre_compute(universe, ctx)
        assert ctx.sentiment_credibility_scores == {
            "AAPL": {"credibility_weighted_sentiment": 0.3, "bot_activity_ratio": 0.0,
                     "aggregated_source_credibility": 1.0},
        }

    def test_compute_blends_headline_and_social(self):
        sig = _make_signal()
        sig._news_scores = {"AAPL": 0.8}
        sig._earnings_dt = {}
        sig._sentiment_credibility = {
            "AAPL": {"credibility_weighted_sentiment": 0.0, "bot_activity_ratio": 0.0,
                     "aggregated_source_credibility": 1.0},
        }
        row = pd.Series({"Symbol": "AAPL", "Ticker": "AAPL"})
        with patch("settings.settings.SENTIMENT_SOCIAL_BLEND_WEIGHT", 0.4):
            out = sig.compute(row, _make_context())
        # 0.6 * 0.8 (headline) + 0.4 * 0.0 (social) = 0.48
        assert abs(out.score - 0.48) < 1e-6
        assert "social blend" in out.explanation

    def test_compute_degrades_to_headline_only_without_social_data(self):
        sig = _make_signal()
        sig._news_scores = {"AAPL": 0.6}
        sig._earnings_dt = {}
        sig._sentiment_credibility = {}  # no social documents this cycle
        row = pd.Series({"Symbol": "AAPL", "Ticker": "AAPL"})
        out = sig.compute(row, _make_context())
        assert abs(out.score - 0.6) < 1e-6
        assert "social blend" not in out.explanation

    def test_compute_blend_weight_zero_is_headline_only(self):
        sig = _make_signal()
        sig._news_scores = {"AAPL": 0.6}
        sig._earnings_dt = {}
        sig._sentiment_credibility = {
            "AAPL": {"credibility_weighted_sentiment": 0.9, "bot_activity_ratio": 0.0,
                     "aggregated_source_credibility": 1.0},
        }
        row = pd.Series({"Symbol": "AAPL", "Ticker": "AAPL"})
        with patch("settings.settings.SENTIMENT_SOCIAL_BLEND_WEIGHT", 0.0):
            out = sig.compute(row, _make_context())
        assert abs(out.score - 0.6) < 1e-6

    def test_compute_blend_weight_one_is_social_only(self):
        sig = _make_signal()
        sig._news_scores = {"AAPL": 0.6}
        sig._earnings_dt = {}
        sig._sentiment_credibility = {
            "AAPL": {"credibility_weighted_sentiment": 0.9, "bot_activity_ratio": 0.0,
                     "aggregated_source_credibility": 1.0},
        }
        row = pd.Series({"Symbol": "AAPL", "Ticker": "AAPL"})
        with patch("settings.settings.SENTIMENT_SOCIAL_BLEND_WEIGHT", 1.0):
            out = sig.compute(row, _make_context())
        assert abs(out.score - 0.9) < 1e-6


class TestRegimeGate:
    """is_active_in_regime suppression (mirrors tests/test_rsi2_regime_gate.py)."""

    def test_recession_regime_forces_score_zero(self):
        sig = _make_signal()
        macro = MacroEconomicDTO(
            yield_curve_10y_2y=-0.5, high_yield_oas=8.0, inflation_rate=2.0,
            nominal_10y=4.0, vix_value=15.0,
        )
        assert macro.market_regime == "RECESSION"
        assert sig.is_active_in_regime(macro) is False

    def test_credit_event_regime_forces_score_zero(self):
        sig = _make_signal()
        macro = MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=7.0, inflation_rate=2.0,
            nominal_10y=4.0, vix_value=15.0,
        )
        assert macro.market_regime == "CREDIT EVENT"
        assert sig.is_active_in_regime(macro) is False

    def test_high_vix_forces_score_zero_even_in_neutral_regime(self):
        sig = _make_signal()
        macro = MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
            nominal_10y=4.0, vix_value=35.0,
        )
        assert macro.market_regime in ("NEUTRAL", "RISK ON")
        assert sig.is_active_in_regime(macro) is False

    def test_risk_on_regime_remains_active(self):
        sig = _make_signal()
        macro = MacroEconomicDTO(
            yield_curve_10y_2y=2.0, high_yield_oas=1.5, inflation_rate=2.0,
            nominal_10y=4.0, vix_value=12.0,
        )
        assert sig.is_active_in_regime(macro) is True

    def test_aggregator_suppresses_contribution_during_recession(self):
        """End-to-end: SignalAggregator must zero out this module's contribution
        when macro is RECESSION, even though the raw compute() score is strongly
        positive."""
        registry = SignalRegistry()
        sig = _make_signal()
        sig._news_scores = {"TEST": 0.9}
        sig._earnings_dt = {}
        registry.register(sig)

        bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 100.0, 100.0, 100.0, 1000)
        fundamentals = FundamentalDataDTO(
            ticker="TEST", company_name="Test Corp", sector="Technology",
            pe_ratio=15.0, pb_ratio=1.5, book_value=50.0, eps_trailing=5.0,
            dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30,
        )
        row = pd.Series({"Symbol": "TEST", "Ticker": "TEST"})

        # Sanity check: in isolation (no regime gate), this row scores high.
        benign_macro = MacroEconomicDTO(0.5, 2.0, 2.0, 4.0, vix_value=15.0)
        benign_context = SignalContext(bar=bar, fundamentals=fundamentals, macro=benign_macro)
        raw_output = sig.compute(row, benign_context)
        assert raw_output.score > 0.5

        # Now run through the aggregator under a RECESSION macro.
        recession_macro = MacroEconomicDTO(-0.5, 8.0, 2.0, 4.0, vix_value=15.0)
        recession_context = SignalContext(bar=bar, fundamentals=fundamentals, macro=recession_macro)
        aggregator = SignalAggregator(registry, weights={"news_catalyst": 10.0})

        final_score, score_log, warnings, details, outputs, _meta = aggregator.aggregate(
            row, recession_context
        )

        # Base neutral score is 50.0; the gated module must contribute nothing
        # to the aggregate score or explainer log, even though compute() itself
        # still ran (outputs retains the raw, ungated score for introspection).
        assert final_score == 50.0
        assert not any("News sentiment" in line for line in score_log)
        assert "news_catalyst" in outputs  # raw compute() output is preserved
        assert outputs["news_catalyst"].score > 0.5  # but never reaches the score/log


class TestSettings:
    def test_news_lookback_days_positive(self):
        from settings import settings
        assert settings.NEWS_LOOKBACK_DAYS > 0

    def test_finbert_enabled_is_bool(self):
        from settings import settings
        assert isinstance(settings.FINBERT_ENABLED, bool)

    def test_finbert_batch_size_positive(self):
        from settings import settings
        assert isinstance(settings.FINBERT_BATCH_SIZE, int)
        assert settings.FINBERT_BATCH_SIZE > 0

    def test_finbert_score_cache_enabled_is_bool(self):
        from settings import settings
        assert isinstance(settings.FINBERT_SCORE_CACHE_ENABLED, bool)

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
