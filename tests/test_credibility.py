"""
tests/test_credibility.py
==========================
Unit tests for signals/credibility.py (Sentiment Pipeline Phase 4, plus
Phase 2 PR2 -- AI-Assisted Credibility Filtering).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# score_document()'s llm_verification parameter (Sentiment Pipeline Phase 2
# PR2 -- AI-Assisted Credibility Filtering)
# ---------------------------------------------------------------------------
class TestScoreDocumentLLMVerificationParam:
    def test_llm_verification_none_preserves_placeholder(self):
        doc = _doc()
        score = score_document(doc, llm_verification=None)
        assert score.s_verification == 1.0
        assert score.verification_method == "placeholder"

    def test_llm_verification_supplied_overrides_placeholder(self):
        doc = _doc()
        score = score_document(doc, llm_verification=0.85)
        assert score.s_verification == 0.85
        assert score.verification_method == "llm"

    def test_institutional_source_ignores_llm_verification(self):
        doc = _doc(source_name="finnhub", author_handle=None)
        score = score_document(doc, llm_verification=0.1)
        assert score.s_verification == 1.0
        assert score.verification_method == "placeholder"


# ---------------------------------------------------------------------------
# score_documents() -- flag OFF is a byte-identical no-op (CRITICAL
# regression guard: default pydantic setting, not a monkeypatched False)
# ---------------------------------------------------------------------------
class TestLLMVerificationDefaultOff:
    def test_default_flag_is_false(self):
        from settings import settings

        assert settings.SENTIMENT_LLM_VERIFICATION_ENABLED is False

    def test_s_verification_always_one_by_default(self):
        docs = [
            _doc(source_name="finnhub", author_handle=None, text_content="a"),
            _doc(source_name="reddit", author_handle=None, author_followers=10, text_content="b"),
            _doc(source_name="reddit", author_handle=None, author_followers=100_000, text_content="c"),
        ]
        scores = score_documents(docs)
        assert all(s.s_verification == 1.0 for s in scores)
        assert all(s.verification_method == "placeholder" for s in scores)

    def test_flag_off_never_resolves_a_provider(self):
        """No LLM machinery is even touched when the switch is off."""
        docs = [_doc(source_name="reddit", author_handle=None)]
        with patch("signals.credibility._get_verification_provider") as mock_get:
            score_documents(docs)
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# score_documents() -- flag ON, mocked LLMProvider.call_structured
# ---------------------------------------------------------------------------
class TestLLMVerificationEnabled:
    """All tests here mock the provider AND the cache store so no real
    network call or DB touch ever occurs."""

    @staticmethod
    def _borderline_batch():
        now = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        docs = [
            # Institutional -- always skipped, never a verification candidate.
            _doc(source_name="finnhub", author_handle=None, text_content="institutional"),
            # Clearly high-trust (high followers, no cadence signal) -- heuristic
            # (1.0 + 0.7) / 2 = 0.85, above the 0.7 borderline ceiling.
            _doc(source_name="reddit", author_handle=None, author_followers=100_000, text_content="trusted"),
        ]
        # Clearly bot-flagged (low followers + very high posting cadence) --
        # heuristic (0.1 + 0.0) / 2 = 0.05, below the 0.3 borderline floor.
        docs += [
            _doc(
                source_name="reddit", author_handle="spammer", author_followers=1,
                as_of=now + timedelta(seconds=i), text_content=f"spam{i}",
            )
            for i in range(20)
        ]
        # Borderline (unknown followers, unknown cadence) -- heuristic
        # (0.5 + 0.7) / 2 = 0.6, squarely inside [0.3, 0.7].
        docs += [
            _doc(source_name="reddit", author_handle=None, text_content="borderline1"),
            _doc(source_name="reddit", author_handle=None, text_content="borderline2"),
        ]
        return docs

    def test_only_borderline_docs_trigger_llm_call(self):
        docs = self._borderline_batch()
        mock_provider = MagicMock()
        mock_provider.call_structured.return_value = None
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=None):
            score_documents(docs)
        assert mock_provider.call_structured.call_count == 2

    def test_none_result_falls_back_to_placeholder(self):
        docs = [_doc(source_name="reddit", author_handle=None, text_content="borderline")]
        mock_provider = MagicMock()
        mock_provider.call_structured.return_value = None  # soft-fail
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=None):
            scores = score_documents(docs)
        assert scores[0].s_verification == 1.0
        assert scores[0].verification_method == "placeholder"

    def test_verifiable_result_maps_to_confidence(self):
        from llm.schemas import SentimentDocumentVerification

        docs = [_doc(source_name="reddit", author_handle=None, text_content="borderline")]
        mock_provider = MagicMock()
        mock_provider.call_structured.return_value = SentimentDocumentVerification(
            verifiable=True, confidence=0.9, rationale="Reads like real commentary."
        )
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=None):
            scores = score_documents(docs)
        assert scores[0].s_verification == pytest.approx(0.9)
        assert scores[0].verification_method == "llm"

    def test_not_verifiable_result_maps_to_inverse_confidence(self):
        from llm.schemas import SentimentDocumentVerification

        docs = [_doc(source_name="reddit", author_handle=None, text_content="borderline")]
        mock_provider = MagicMock()
        mock_provider.call_structured.return_value = SentimentDocumentVerification(
            verifiable=False, confidence=0.9, rationale="Reads like bot filler."
        )
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=None):
            scores = score_documents(docs)
        assert scores[0].s_verification == pytest.approx(0.1)  # 1 - 0.9

    def test_max_calls_budget_exhausted_mid_batch(self):
        from llm.schemas import SentimentDocumentVerification

        docs = [
            _doc(source_name="reddit", author_handle=None, text_content="borderline1"),
            _doc(source_name="reddit", author_handle=None, text_content="borderline2"),
        ]
        mock_provider = MagicMock()
        mock_provider.call_structured.return_value = SentimentDocumentVerification(
            verifiable=True, confidence=0.9, rationale="ok"
        )
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("settings.settings.SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE", 1), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=None):
            scores = score_documents(docs)
        assert mock_provider.call_structured.call_count == 1
        assert scores[1].s_verification == 1.0  # budget exhausted -> placeholder, no crash

    def test_remaining_seconds_budget_exhausted_skips_call(self):
        docs = [_doc(source_name="reddit", author_handle=None, text_content="borderline")]
        mock_provider = MagicMock()
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=None):
            scores = score_documents(docs, remaining_seconds=0.0)
        mock_provider.call_structured.assert_not_called()
        assert scores[0].s_verification == 1.0

    def test_cache_hit_skips_llm_call(self):
        docs = [_doc(source_name="reddit", author_handle=None, text_content="borderline")]
        mock_provider = MagicMock()
        mock_store = MagicMock()
        mock_store.get_cached_verification.return_value = (True, 0.8)
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True), \
             patch("signals.credibility._get_verification_provider", return_value=mock_provider), \
             patch("signals.credibility._get_verification_cache_store", return_value=mock_store):
            scores = score_documents(docs)
        mock_provider.call_structured.assert_not_called()
        assert scores[0].s_verification == pytest.approx(0.8)
        assert scores[0].verification_method == "llm"

    def test_disabled_when_provider_choice_is_none(self):
        """settings.SENTIMENT_LLM_VERIFICATION_PROVIDER == 'none' (the real
        default) means _get_verification_provider() resolves to None even
        with the master switch on -- score_documents must still no-op."""
        docs = [_doc(source_name="reddit", author_handle=None, text_content="borderline")]
        with patch("settings.settings.SENTIMENT_LLM_VERIFICATION_ENABLED", True):
            # _get_verification_provider is NOT patched here -- it resolves
            # for real via llm.router, which returns None because
            # SENTIMENT_LLM_VERIFICATION_PROVIDER defaults to "none".
            scores = score_documents(docs)
        assert scores[0].s_verification == 1.0


# ---------------------------------------------------------------------------
# Point-in-time safety: the LLM prompt must only ever see the document's own
# already-archived fields, never anything computed from data after the
# document's own as_of timestamp.
# ---------------------------------------------------------------------------
class TestVerificationPromptPITSafety:
    def test_prompt_only_uses_documents_own_fields(self):
        from signals.credibility import _verification_user_prompt

        doc = _doc(source_name="reddit", symbol="AAPL", text_content="AAPL is mooning today")
        prompt = _verification_user_prompt(doc)
        assert doc.text_content in prompt
        assert doc.symbol in prompt
        assert doc.source_name in prompt
        # No other document attribute (e.g. the raw sentiment score, which is
        # itself downstream of the credibility pipeline) leaks into the prompt.
        assert str(doc.raw_sentiment_score) not in prompt
