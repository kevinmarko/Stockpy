"""Tests for data/sentiment_sources.py::StockTwitsSource -- the free,
uncredentialed second comment-class source lighting up the Review term's
coverage alongside Reddit (see docs/RUNBOOK.md).

Per the honest-risk-register note this feature ships with: StockTwits'
public endpoint may rate-limit or require auth in a live deployment.
Nothing here asserts a live response -- only a captured-fixture parse and
the documented failure-degradation paths (403/429/timeout/malformed)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from data.sentiment_sources import StockTwitsSource, _SOURCE_REGISTRY


class TestStockTwitsSourceGating:
    def test_disabled_by_default_returns_empty(self):
        src = StockTwitsSource()
        with patch("settings.settings.STOCKTWITS_ENABLED", False):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_disabled_makes_no_network_call(self):
        src = StockTwitsSource()
        with patch("settings.settings.STOCKTWITS_ENABLED", False), \
             patch("data.sentiment_sources.requests.get") as mock_get:
            src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        mock_get.assert_not_called()

    def test_registered_in_source_registry(self):
        assert _SOURCE_REGISTRY.get("stocktwits") is StockTwitsSource

    def test_not_in_default_sentiment_sources(self):
        from settings import settings as _settings
        default_sources = [n.strip() for n in _settings.SENTIMENT_SOURCES.split(",")]
        assert "stocktwits" not in default_sources


class TestStockTwitsSourceFetch:
    def _fixture_response(self, messages):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"messages": messages}
        return resp

    def test_parses_captured_fixture(self):
        """A realistic captured StockTwits stream response -- body, ISO
        timestamp, username, follower count."""
        src = StockTwitsSource()
        now = datetime.now(timezone.utc)
        resp = self._fixture_response([{
            "id": 123456789,
            "body": "$AAPL breaking out above resistance",
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user": {"username": "some_trader", "followers": 4200},
        }])
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", now - timedelta(days=1))
        assert len(docs) == 1
        assert docs[0].text_content == "$AAPL breaking out above resistance"
        assert docs[0].source_name == "stocktwits"
        assert docs[0].author_handle == "some_trader"
        assert docs[0].author_followers == 4200
        assert docs[0].account_age_days is None  # never fabricated -- CONSTRAINT #4

    def test_message_without_user_block_degrades_credibility_fields_to_none(self):
        src = StockTwitsSource()
        now = datetime.now(timezone.utc)
        resp = self._fixture_response([{
            "body": "no user block here",
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }])
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", now - timedelta(days=1))
        assert docs[0].author_handle is None
        assert docs[0].author_followers is None

    def test_empty_body_skipped(self):
        src = StockTwitsSource()
        now = datetime.now(timezone.utc)
        resp = self._fixture_response([{"body": "", "created_at": now.isoformat()}])
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", now - timedelta(days=1))
        assert docs == []

    def test_message_older_than_since_excluded(self):
        src = StockTwitsSource()
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=10)
        resp = self._fixture_response([{
            "body": "stale message",
            "created_at": old.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }])
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", now - timedelta(days=1))
        assert docs == []

    def test_unparsable_created_at_skips_message_not_crash(self):
        src = StockTwitsSource()
        now = datetime.now(timezone.utc)
        resp = self._fixture_response([{"body": "bad timestamp", "created_at": "not-a-date"}])
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", now - timedelta(days=1))
        assert docs == []

    def test_no_messages_key_returns_empty(self):
        src = StockTwitsSource()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []


class TestStockTwitsSourceFailureResilience:
    """CONSTRAINT #6: every documented failure mode degrades to [], never
    raises -- the endpoint-stability caveat this source ships with."""

    def test_403_forbidden_degrades_to_empty(self):
        src = StockTwitsSource()
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("403 Forbidden")
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_429_rate_limited_degrades_to_empty(self):
        src = StockTwitsSource()
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("429 Too Many Requests")
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_timeout_degrades_to_empty(self):
        src = StockTwitsSource()
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", side_effect=requests.exceptions.Timeout()):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_malformed_json_degrades_to_empty(self):
        src = StockTwitsSource()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("not json")
        with patch("settings.settings.STOCKTWITS_ENABLED", True), \
             patch("data.sentiment_sources.requests.get", return_value=resp):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []
