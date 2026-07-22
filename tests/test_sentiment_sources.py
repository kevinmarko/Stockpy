"""
tests/test_sentiment_sources.py
================================
Unit tests for data/sentiment_sources.py (Sentiment Pipeline Phase 3).

All HTTP/API calls are monkeypatched; no real network requests are made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.sentiment_sources import (
    CompositeSentimentSource,
    EdgarSource,
    FinnhubSentimentSource,
    GDELTSource,
    RedditSource,
    SentimentDocument,
    YahooRSSSource,
    _dedup_key,
    desentencize,
    get_sentiment_source,
    reset_sentiment_source,
)


def _doc(**overrides) -> SentimentDocument:
    base = dict(
        as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),
        symbol="AAPL",
        source_name="finnhub",
        text_content="Apple beats earnings expectations",
        raw_sentiment_score=0.6,
    )
    base.update(overrides)
    return SentimentDocument(**base)


class TestSentimentDocument:
    def test_to_audit_row_shape(self):
        doc = _doc(author_handle="someone")
        row = doc.to_audit_row()
        assert row["symbol"] == "AAPL"
        assert row["source_name"] == "finnhub"
        assert row["author_handle"] == "someone"
        assert row["raw_sentiment_score"] == pytest.approx(0.6)


class TestDedupKey:
    def test_same_inputs_same_hash(self):
        doc = _doc()
        assert _dedup_key(doc, "2026-07-21") == _dedup_key(doc, "2026-07-21")

    def test_different_trading_day_different_hash(self):
        doc = _doc()
        assert _dedup_key(doc, "2026-07-21") != _dedup_key(doc, "2026-07-22")

    def test_different_text_different_hash(self):
        doc1 = _doc(text_content="A")
        doc2 = _doc(text_content="B")
        assert _dedup_key(doc1, "2026-07-21") != _dedup_key(doc2, "2026-07-21")


class TestDesentencize:
    def test_decimal_amount_preserved(self):
        assert "$4.50" in desentencize("Shares rose to $4.50 today.")

    def test_abbreviation_preserved(self):
        result = desentencize("U.S. markets rallied.")
        assert result.startswith("U.S.")

    def test_cashtag_unaffected(self):
        result = desentencize("Buy $AAPL now. Strong signal.")
        assert "$AAPL" in result

    def test_sentence_boundary_periods_become_semicolons(self):
        result = desentencize("First sentence. Second sentence.")
        assert result == "First sentence; Second sentence;"

    def test_multiple_decimals_all_preserved(self):
        result = desentencize("Price moved from $4.50 to $5.25. Big move.")
        assert "$4.50" in result and "$5.25" in result


class TestFinnhubSentimentSource:
    def test_no_client_returns_empty(self):
        src = FinnhubSentimentSource()
        with patch("signals.news_catalyst.build_finnhub_client", return_value=None):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_fetch_returns_documents(self):
        src = FinnhubSentimentSource()
        mock_client = MagicMock()
        now = datetime.now(timezone.utc)
        mock_client.company_news.return_value = [
            {"headline": "Apple beats earnings", "datetime": int(now.timestamp())}
        ]
        with patch("signals.news_catalyst.build_finnhub_client", return_value=mock_client):
            with patch("signals.news_catalyst._get_finbert_pipeline", return_value=None):
                docs = src.fetch("AAPL", now - timedelta(days=1))
        assert len(docs) == 1
        assert docs[0].symbol == "AAPL"
        assert docs[0].source_name == "finnhub"

    def test_error_returns_empty(self):
        src = FinnhubSentimentSource()
        with patch("signals.news_catalyst.build_finnhub_client", side_effect=RuntimeError("boom")):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []


class TestYahooRSSSource:
    _RSS_XML = b"""<?xml version="1.0"?>
    <rss><channel>
        <item>
            <title>Apple stock surges on strong guidance</title>
            <pubDate>Tue, 21 Jul 2026 14:00:00 GMT</pubDate>
        </item>
    </channel></rss>
    """

    def test_fetch_parses_feed(self):
        src = YahooRSSSource()
        mock_resp = MagicMock()
        mock_resp.content = self._RSS_XML
        mock_resp.raise_for_status = MagicMock()
        with patch("data.sentiment_sources.requests.get", return_value=mock_resp):
            docs = src.fetch("AAPL", datetime(2026, 7, 20, tzinfo=timezone.utc))
        assert len(docs) == 1
        assert docs[0].source_name == "yahoo_rss"
        assert "surges" in docs[0].text_content

    def test_stale_item_filtered_by_since(self):
        src = YahooRSSSource()
        mock_resp = MagicMock()
        mock_resp.content = self._RSS_XML
        mock_resp.raise_for_status = MagicMock()
        with patch("data.sentiment_sources.requests.get", return_value=mock_resp):
            docs = src.fetch("AAPL", datetime(2026, 7, 22, tzinfo=timezone.utc))
        assert docs == []

    def test_network_error_returns_empty(self):
        src = YahooRSSSource()
        with patch("data.sentiment_sources.requests.get", side_effect=RuntimeError("timeout")):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []


class TestGDELTSource:
    def test_fetch_parses_articles(self):
        src = GDELTSource()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "articles": [
                {"title": "Apple rallies on new product", "seendate": "20260721T140000Z", "tone": 5.0},
            ]
        }
        with patch("data.sentiment_sources.requests.get", return_value=mock_resp):
            docs = src.fetch("AAPL", datetime(2026, 7, 20, tzinfo=timezone.utc))
        assert len(docs) == 1
        assert docs[0].raw_sentiment_score == pytest.approx(0.5)

    def test_tone_clamped_to_bounds(self):
        src = GDELTSource()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "articles": [
                {"title": "Extreme headline", "seendate": "20260721T140000Z", "tone": 500.0},
            ]
        }
        with patch("data.sentiment_sources.requests.get", return_value=mock_resp):
            docs = src.fetch("AAPL", datetime(2026, 7, 20, tzinfo=timezone.utc))
        assert docs[0].raw_sentiment_score == 1.0

    def test_error_returns_empty(self):
        src = GDELTSource()
        with patch("data.sentiment_sources.requests.get", side_effect=RuntimeError("boom")):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []


class TestRedditSource:
    def test_no_credentials_returns_empty(self):
        src = RedditSource()
        with patch("settings.settings.REDDIT_CLIENT_ID", ""):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_fetch_with_credentials(self):
        src = RedditSource()
        now = datetime.now(timezone.utc)
        mock_token_resp = MagicMock()
        mock_token_resp.raise_for_status = MagicMock()
        mock_token_resp.json.return_value = {"access_token": "tok123"}

        mock_search_resp = MagicMock()
        mock_search_resp.raise_for_status = MagicMock()
        mock_search_resp.json.return_value = {
            "data": {"children": [
                {"data": {
                    "title": "AAPL to the moon",
                    "created_utc": now.timestamp(),
                    "author": "some_redditor",
                }}
            ]}
        }

        with patch("settings.settings.REDDIT_CLIENT_ID", "cid"):
            with patch("settings.settings.REDDIT_CLIENT_SECRET", "csecret"):
                with patch("data.sentiment_sources.requests.post", return_value=mock_token_resp):
                    with patch("data.sentiment_sources.requests.get", return_value=mock_search_resp):
                        docs = src.fetch("AAPL", now - timedelta(days=1))
        assert len(docs) == 1
        assert docs[0].author_handle == "some_redditor"
        assert docs[0].author_followers is None  # never fabricated

    def test_token_failure_returns_empty(self):
        src = RedditSource()
        with patch("settings.settings.REDDIT_CLIENT_ID", "cid"):
            with patch("settings.settings.REDDIT_CLIENT_SECRET", "csecret"):
                with patch("data.sentiment_sources.requests.post", side_effect=RuntimeError("boom")):
                    docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []


class TestEdgarSource:
    def test_no_user_agent_returns_empty(self):
        src = EdgarSource()
        with patch("settings.settings.EDGAR_USER_AGENT", ""):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_fetch_filters_to_8k_only(self):
        src = EdgarSource()
        mock_tickers_resp = MagicMock()
        mock_tickers_resp.raise_for_status = MagicMock()
        mock_tickers_resp.json.return_value = {
            "0": {"ticker": "AAPL", "cik_str": 320193},
        }
        mock_submissions_resp = MagicMock()
        mock_submissions_resp.raise_for_status = MagicMock()
        mock_submissions_resp.json.return_value = {
            "filings": {"recent": {
                "form": ["8-K", "10-Q"],
                "filingDate": ["2026-07-21", "2026-07-15"],
                "primaryDocDescription": ["Material event", "Quarterly report"],
            }}
        }
        with patch("settings.settings.EDGAR_USER_AGENT", "Test test@example.com"):
            with patch(
                "data.sentiment_sources.requests.get",
                side_effect=[mock_tickers_resp, mock_submissions_resp],
            ):
                docs = src.fetch("AAPL", datetime(2026, 7, 1, tzinfo=timezone.utc))
        assert len(docs) == 1
        assert docs[0].text_content == "Material event"

    def test_unknown_ticker_returns_empty(self):
        src = EdgarSource()
        mock_tickers_resp = MagicMock()
        mock_tickers_resp.raise_for_status = MagicMock()
        mock_tickers_resp.json.return_value = {"0": {"ticker": "MSFT", "cik_str": 789019}}
        with patch("settings.settings.EDGAR_USER_AGENT", "Test test@example.com"):
            with patch("data.sentiment_sources.requests.get", return_value=mock_tickers_resp):
                docs = src.fetch("ZZZZ", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_error_returns_empty(self):
        src = EdgarSource()
        with patch("settings.settings.EDGAR_USER_AGENT", "Test test@example.com"):
            with patch("data.sentiment_sources.requests.get", side_effect=RuntimeError("boom")):
                docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []


class TestCompositeSentimentSource:
    def test_build_enabled_sources_respects_setting(self):
        with patch("settings.settings.SENTIMENT_SOURCES", "finnhub,gdelt"):
            composite = CompositeSentimentSource()
        assert set(composite._sources.keys()) == {"finnhub", "gdelt"}

    def test_unknown_source_name_skipped(self):
        with patch("settings.settings.SENTIMENT_SOURCES", "finnhub,not_a_real_source"):
            composite = CompositeSentimentSource()
        assert set(composite._sources.keys()) == {"finnhub"}

    def test_fetch_all_merges_and_dedups(self):
        source_a = MagicMock()
        source_a.fetch.return_value = [_doc(source_name="a", text_content="same text")]
        source_b = MagicMock()
        source_b.fetch.return_value = [_doc(source_name="a", text_content="same text")]  # exact dup

        composite = CompositeSentimentSource(sources={"a": source_a, "b": source_b})
        docs = composite.fetch_all("AAPL", since=datetime(2026, 7, 1, tzinfo=timezone.utc))
        assert len(docs) == 1  # deduped

    def test_fetch_all_one_source_failing_does_not_block_others(self):
        good = MagicMock()
        good.fetch.return_value = [_doc()]
        bad = MagicMock()
        bad.fetch.side_effect = RuntimeError("boom")

        composite = CompositeSentimentSource(sources={"good": good, "bad": bad})
        docs = composite.fetch_all("AAPL", since=datetime(2026, 7, 1, tzinfo=timezone.utc))
        assert len(docs) == 1

    def test_backpressure_sheds_lower_priority_sources(self):
        finnhub_mock = MagicMock()
        finnhub_mock.fetch.return_value = [_doc(source_name="finnhub")]
        reddit_mock = MagicMock()
        reddit_mock.fetch.return_value = [_doc(source_name="reddit")]

        composite = CompositeSentimentSource(sources={"finnhub": finnhub_mock, "reddit": reddit_mock})
        with patch("settings.settings.SENTIMENT_MAX_DOCUMENTS_PER_CYCLE", 1):
            docs = composite.fetch_all("AAPL", since=datetime(2026, 7, 1, tzinfo=timezone.utc))
        # finnhub (higher priority) fills the budget; reddit is shed.
        assert len(docs) == 1
        assert docs[0].source_name == "finnhub"
        reddit_mock.fetch.assert_not_called()

    def test_reset_cycle_clears_budget_counter(self):
        finnhub_mock = MagicMock()
        finnhub_mock.fetch.return_value = [_doc(source_name="finnhub")]

        composite = CompositeSentimentSource(sources={"finnhub": finnhub_mock})
        with patch("settings.settings.SENTIMENT_MAX_DOCUMENTS_PER_CYCLE", 1):
            composite.fetch_all("AAPL", since=datetime(2026, 7, 1, tzinfo=timezone.utc))
            assert composite._documents_this_cycle == 1
            composite.reset_cycle()
            assert composite._documents_this_cycle == 0

    def test_fetch_and_archive_writes_when_enabled(self):
        finnhub_mock = MagicMock()
        finnhub_mock.fetch.return_value = [_doc()]
        composite = CompositeSentimentSource(sources={"finnhub": finnhub_mock})

        mock_store_instance = MagicMock()
        mock_store_cls = MagicMock(return_value=mock_store_instance)
        with patch("settings.settings.SENTIMENT_AUDIT_ENABLED", True):
            with patch("data.historical_store.HistoricalStore", mock_store_cls):
                composite.fetch_and_archive("AAPL", since=datetime(2026, 7, 1, tzinfo=timezone.utc))
        mock_store_instance.save_sentiment_documents.assert_called_once()

    def test_fetch_and_archive_skips_when_disabled(self):
        finnhub_mock = MagicMock()
        finnhub_mock.fetch.return_value = [_doc()]
        composite = CompositeSentimentSource(sources={"finnhub": finnhub_mock})

        mock_store_cls = MagicMock()
        with patch("settings.settings.SENTIMENT_AUDIT_ENABLED", False):
            with patch("data.historical_store.HistoricalStore", mock_store_cls):
                composite.fetch_and_archive("AAPL", since=datetime(2026, 7, 1, tzinfo=timezone.utc))
        mock_store_cls.assert_not_called()

    def test_archive_failure_never_propagates(self):
        """CONSTRAINT #6: an archive failure must never raise."""
        mock_store_cls = MagicMock(side_effect=RuntimeError("db unavailable"))
        with patch("settings.settings.SENTIMENT_AUDIT_ENABLED", True):
            with patch("data.historical_store.HistoricalStore", mock_store_cls):
                CompositeSentimentSource._archive([_doc()])  # must not raise


class TestSingleton:
    def test_get_sentiment_source_returns_same_instance(self):
        reset_sentiment_source()
        try:
            a = get_sentiment_source()
            b = get_sentiment_source()
            assert a is b
        finally:
            reset_sentiment_source()

    def test_reset_sentiment_source_forces_new_instance(self):
        reset_sentiment_source()
        try:
            a = get_sentiment_source()
            reset_sentiment_source()
            b = get_sentiment_source()
            assert a is not b
        finally:
            reset_sentiment_source()
