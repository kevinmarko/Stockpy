"""
tests/test_emoji_lexicon.py
============================
Tests for data/emoji_lexicon.py -- the hand-curated Emoji Sentiment Lexicon
backing ml.models.emoji_sentiment.EmojiSentimentModel's real emoji-parsing
channel.
"""
import numpy as np
import pytest

from data.emoji_lexicon import EMOJI_SENTIMENT, extract_emojis, score_text_emojis


def test_lexicon_scores_bounded():
    assert len(EMOJI_SENTIMENT) > 50
    for emoji, score in EMOJI_SENTIMENT.items():
        assert -1.0 <= score <= 1.0, f"{emoji} score {score} out of bounds"


def test_extract_emojis_finds_known_emoji():
    found = extract_emojis("To the moon! 🚀🚀 great earnings 📈")
    assert found.count("🚀") == 2
    assert "📈" in found


def test_extract_emojis_empty_text():
    assert extract_emojis("") == []
    assert extract_emojis(None) == []  # type: ignore[arg-type]


def test_score_text_emojis_positive():
    score = score_text_emojis("Absolutely crushing it 🚀📈🎉")
    assert score is not None
    assert score > 0


def test_score_text_emojis_negative():
    score = score_text_emojis("This is a disaster 😭📉👎")
    assert score is not None
    assert score < 0


def test_score_text_emojis_none_when_no_known_emoji():
    assert score_text_emojis("no emoji here at all") is None


def test_score_text_emojis_is_mean_of_found():
    text = "😀😭"  # +0.8 and -0.8 -> mean 0.0
    score = score_text_emojis(text)
    assert score == pytest.approx(0.0, abs=1e-9)


def test_score_text_emojis_deterministic():
    text = "Loving this rally 🚀🔥"
    assert score_text_emojis(text) == score_text_emojis(text)
