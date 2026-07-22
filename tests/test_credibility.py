"""
tests/test_credibility.py
==========================
Unit tests for signals/credibility.py (Sentiment Pipeline Phase 4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from data.sentiment_sources import SentimentDocument
from signals.credibility import (
    CredibilityScore,
    score_document,
    score_documents,
)


def _doc(**overrides) -> SentimentDocument:
    base = dict(
        as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),
        symbol="AAPL",
        source_name="reddit",
        text_content="AAPL to the moon",
        raw_sentiment_score=0.5,
    )
    base.update(overrides)
    return SentimentDocument(**base)


class TestInstitutionalSources:
    @pytest.mark.parametrize("source", ["finnhub", "yahoo_rss", "gdelt", "edgar"])
    def test_institutional_source_fully_trusted(self, source):
        doc = _doc(source_name=source, author_handle=None)
        score = score_document(doc)
        assert score.s_authority == 1.0
        assert score.s_humanity == 1.0
        assert score.credibility_weight == 1.0
        assert score.is_bot is False


class TestScoreAuthority:
    def test_unknown_followers_neutral(self):
        doc = _doc(author_followers=None)
        score = score_document(doc)
        assert score.s_authority == 0.5

    def test_low_followers_low_authority(self):
        doc = _doc(author_followers=10)
        score = score_document(doc)
        assert score.s_authority == 0.1

    def test_high_followers_full_authority(self):
        doc = _doc(author_followers=100_000)
        score = score_document(doc)
        assert score.s_authority == 1.0

    def test_mid_followers_between_bounds(self):
        doc = _doc(author_followers=2525)  # midpoint of 50-5000 range
        score = score_document(doc)
        assert 0.1 < score.s_authority < 1.0


class TestScoreHumanity:
    def test_unknown_cadence_neutral_leaning(self):
        doc = _doc()
        score = score_document(doc, posts_per_minute=None)
        assert score.s_humanity == 0.7

    def test_low_cadence_full_humanity(self):
        doc = _doc()
        score = score_document(doc, posts_per_minute=0.0)
        assert score.s_humanity == 1.0

    def test_high_cadence_flagged_as_bot(self):
        doc = _doc()
        score = score_document(doc, posts_per_minute=50.0)
        assert score.s_humanity == 0.0
        assert score.is_bot is True

    def test_bounds_respected(self):
        for ppm in [0.0, 1.0, 5.0, 10.0, 100.0]:
            doc = _doc()
            score = score_document(doc, posts_per_minute=ppm)
            assert 0.0 <= score.s_humanity <= 1.0


class TestCredibilityWeightBounds:
    def test_weight_never_below_floor(self):
        doc = _doc(author_followers=1)
        score = score_document(doc, posts_per_minute=100.0)  # worst case
        assert score.credibility_weight >= 0.1

    def test_weight_never_above_ceiling(self):
        doc = _doc(author_followers=100_000)
        score = score_document(doc, posts_per_minute=0.0)
        assert score.credibility_weight <= 1.0


class TestScoreDocumentsBatch:
    def test_empty_batch_returns_empty(self):
        assert score_documents([]) == []

    def test_batch_size_matches_input(self):
        docs = [_doc(symbol=f"SYM{i}") for i in range(5)]
        scores = score_documents(docs)
        assert len(scores) == 5
        assert all(isinstance(s, CredibilityScore) for s in scores)

    def test_high_frequency_author_flagged_across_batch(self):
        now = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        # Same author posts 20 times within a 1-minute span -> high cadence.
        docs = [
            _doc(author_handle="spammer", as_of=now + timedelta(seconds=i))
            for i in range(20)
        ]
        scores = score_documents(docs)
        assert any(s.is_bot for s in scores)

    def test_single_post_author_not_flagged(self):
        docs = [_doc(author_handle="normal_user")]
        scores = score_documents(docs)
        assert scores[0].is_bot is False

    def test_no_author_handle_gets_neutral_humanity(self):
        # institutional sources never reach _score_humanity (short-circuited),
        # so use a social source with no author to hit the None path.
        docs = [_doc(source_name="reddit", author_handle=None)]
        scores = score_documents(docs)
        assert scores[0].s_humanity == 0.7

    def test_mixed_institutional_and_social_batch(self):
        docs = [
            _doc(source_name="finnhub", author_handle=None),
            _doc(source_name="reddit", author_handle="user1", author_followers=10),
        ]
        scores = score_documents(docs)
        assert scores[0].credibility_weight == 1.0  # institutional
        assert scores[1].credibility_weight < 1.0  # social, low followers
