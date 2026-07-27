"""Tests for data/sentiment_source_class.py -- the pure news-vs-comment
source taxonomy shared by Sector Selection's Sector Heat Factor and the
composite sentiment index S_t."""
from __future__ import annotations

from unittest.mock import patch

from data.sentiment_source_class import classify_source


class TestClassifySource:
    def test_default_comment_sources(self):
        assert classify_source("reddit") == "comment"

    def test_default_news_sources(self):
        assert classify_source("gdelt") == "news"
        assert classify_source("yahoo_rss") == "news"
        assert classify_source("edgar") == "news"

    def test_finnhub_is_news_even_though_excluded_from_default_fanout(self):
        """'finnhub' is a legitimate news source in the vocabulary even
        though it's excluded from SENTIMENT_SOURCES' default (NewsCatalyst
        already fetches it directly) -- an operator who adds it back must
        not have it silently misclassified."""
        assert classify_source("finnhub") == "news"

    def test_google_news_is_news(self):
        assert classify_source("google_news") == "news"

    def test_case_insensitive(self):
        assert classify_source("REDDIT") == "comment"
        assert classify_source("GDELT") == "news"

    def test_whitespace_stripped(self):
        assert classify_source("  reddit  ") == "comment"

    def test_empty_string_is_unknown(self):
        assert classify_source("") == "unknown"

    def test_none_like_falsy_is_unknown(self):
        assert classify_source(None) == "unknown"  # type: ignore[arg-type]

    def test_unrecognized_source_is_unknown_not_news(self):
        """An unclassified source must never default to news -- that would
        silently inflate the news volume term for a source nobody vetted."""
        assert classify_source("some_future_platform") == "unknown"

    def test_stocktwits_pre_classified_as_comment_even_though_source_absent(self):
        """SENTIMENT_COMMENT_SOURCES lists 'stocktwits' ahead of
        data/sentiment_sources.py actually implementing it (Sentiment
        Source Class Phase 4) -- classify_source must already route it to
        'comment' the moment ingestion exists, with zero code change here."""
        assert classify_source("stocktwits") == "comment"

    def test_custom_comment_sources_setting_respected(self):
        with patch("settings.settings.SENTIMENT_COMMENT_SOURCES", "reddit,discord"):
            assert classify_source("discord") == "comment"

    def test_reddit_removed_from_comment_sources_falls_through_to_news(self):
        """'reddit' is also a member of the default SENTIMENT_SOURCES
        fan-out (the full ingested-source list), so once it's carved out of
        SENTIMENT_COMMENT_SOURCES it correctly falls through to 'news' --
        SENTIMENT_COMMENT_SOURCES only ever narrows the comment subset of
        an already-recognized source, it doesn't gate recognition itself."""
        with patch("settings.settings.SENTIMENT_COMMENT_SOURCES", "discord"):
            assert classify_source("reddit") == "news"

    def test_comment_source_with_no_other_classification_falls_through_to_unknown(self):
        """A name that's ONLY ever recognized via SENTIMENT_COMMENT_SOURCES
        (not a member of SENTIMENT_SOURCES or the known-news vocabulary),
        once removed from that setting, must become genuinely 'unknown' --
        never silently 'news' for a source nobody classified as such."""
        with patch("settings.settings.SENTIMENT_COMMENT_SOURCES", "reddit"):
            assert classify_source("stocktwits") == "unknown"

    def test_empty_comment_sources_setting_reddit_still_news(self):
        with patch("settings.settings.SENTIMENT_COMMENT_SOURCES", ""):
            assert classify_source("reddit") == "news"
            assert classify_source("stocktwits") == "unknown"
