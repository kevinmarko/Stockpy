"""
tests/test_fmp_news.py
=======================
Unit tests for the FMP company-news integration:

- ``data/fmp_client.py::stock_news`` -- the raw ``/news/stock`` HTTP wrapper
  (param mapping, raw-JSON pass-through). The shared throttle/retry/cooldown/
  dead-endpoint machinery it routes through (``_fmp_get``) is already
  exhaustively covered by ``tests/test_fmp_client.py``; this file only proves
  ``stock_news`` routes through it correctly, not the machinery itself.
- ``data/fmp_client.py::parse_news_published_date`` -- the naive US-Eastern
  ``publishedDate`` string -> UTC-aware ``datetime`` conversion, including the
  EDT/EST daylight-saving transition that is the entire reason this uses
  ``ZoneInfo("America/New_York")`` instead of a fixed UTC offset.
- ``data/sentiment_sources.py::FMPNewsSource`` -- the opt-in ``SentimentSource``
  wrapping ``stock_news``: the two-gate enable check, pagination (short-page
  stop, ``FMP_NEWS_MAX_PAGES`` ceiling, ``deadline_exceeded()`` early stop),
  ``since``/empty-title filtering, document shape, and CONSTRAINT #6 (never
  raises out of ``fetch()``).
- Registry wiring: ``fmp_news`` in ``_SOURCE_REGISTRY``/``_SOURCE_PRIORITY``
  (ahead of ``finnhub``) and in ``signals.credibility._INSTITUTIONAL_SOURCES``.

Everything here is offline. ``requests.get`` / ``data.fmp_client.stock_news`` /
``signals.news_catalyst.score_headlines`` are monkeypatched or mocked; no real
network request and no real FinBERT model load occurs (``FINBERT_ENABLED`` is
kept ``False`` throughout, so scoring falls back to the keyword lexicon).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.fmp_client import (
    FMPUnavailable,
    parse_news_published_date,
    reset_fmp_rate_limiter,
    stock_news,
)
from data.sentiment_sources import (
    FMPNewsSource,
    SentimentDocument,
    _SOURCE_PRIORITY,
    _SOURCE_REGISTRY,
)
from signals.credibility import _INSTITUTIONAL_SOURCES
from settings import settings


def _resp(status: int = 200, *, payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = payload if payload is not None else []
    return resp


def _article(
    title: str = "Apple headline",
    published: str = "2026-08-02 14:51:00",
    **overrides,
) -> dict:
    base = {
        "symbol": "AAPL",
        "publishedDate": published,
        "publisher": "GlobeNewswire",
        "site": "globenewswire.com",
        "title": title,
        "text": "body text",
        "url": "https://example.test/article",
    }
    base.update(overrides)
    return base


def _mock_score_headlines(headlines, pipeline=None, **kwargs):
    """Deterministic, mildly-positive distribution per headline -- mirrors
    tests/test_sentiment_sources.py's own ``_mock_score_headlines`` helper.
    Accepts (and ignores) ``score_headlines()``'s real ``pipeline``/
    ``batch_size``/``use_cache`` kwargs so a signature drift surfaces as a
    real test failure rather than being silently masked."""
    return [{"positive": 0.6, "neutral": 0.3, "negative": 0.1} for _ in headlines]


@pytest.fixture
def api_key(monkeypatch):
    """A key on the SINGLETON -- never via ``patch.dict(os.environ)``, which
    would test a code path ``data/fmp_client.py`` deliberately does not have
    (see its module docstring / ``tests/test_fmp_client.py``'s regression
    guard for the six-month Finnhub incident this pattern avoids repeating)."""
    reset_fmp_rate_limiter()
    monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
    yield "test-key-abc123"
    reset_fmp_rate_limiter()


# ---------------------------------------------------------------------------
# data/fmp_client.py::stock_news
# ---------------------------------------------------------------------------

class TestStockNews:
    """Only the wrapper's own param-mapping and raw-JSON-pass-through
    contract is exercised here; the throttle/retry/cooldown/dead-endpoint
    state machine it runs through (``_fmp_get``) is not re-tested per-wrapper
    -- that's ``tests/test_fmp_client.py``'s job."""

    def test_only_symbols_and_apikey_sent_when_optional_params_omitted(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            stock_news("AAPL")
        assert get.call_args.kwargs["params"] == {"symbols": "AAPL", "apikey": api_key}

    def test_symbols_passed_through_unmodified(self, api_key):
        """``stock_news`` does not validate, split, or upper-case ``symbols``
        -- FMP's own single-ticker-or-comma-separated-list convention is the
        caller's responsibility, per the function's own docstring."""
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            stock_news("aapl,msft")
        assert get.call_args.kwargs["params"]["symbols"] == "aapl,msft"

    def test_from_to_page_limit_mapped_to_fmp_param_names(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            stock_news(
                "AAPL", from_date="2026-01-01", to_date="2026-01-31", page=2, limit=50,
            )
        params = get.call_args.kwargs["params"]
        assert params["from"] == "2026-01-01"
        assert params["to"] == "2026-01-31"
        assert params["page"] == 2
        assert params["limit"] == 50
        # Mapped to FMP's own query-param names, not passed through raw.
        assert "from_date" not in params
        assert "to_date" not in params

    def test_hits_the_news_stock_path(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            stock_news("AAPL")
        assert get.call_args.args[0].endswith("/news/stock")

    def test_returns_raw_parsed_json_list_unchanged(self, api_key):
        """No key mapping, no unit conversion -- the same raw-JSON contract
        every other wrapper in data/fmp_client.py follows."""
        raw = [
            {
                "symbol": "AAPL",
                "title": "Apple headline",
                "publishedDate": "2026-08-02 14:51:00",
                "publisher": "GlobeNewswire",
            }
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=raw)):
            assert stock_news("AAPL") == raw

    def test_429_propagates_as_fmpunavailable(self, api_key, monkeypatch):
        monkeypatch.setattr(settings, "FMP_MAX_RETRIES", 0)
        with patch("data.fmp_client.requests.get", return_value=_resp(429)):
            with pytest.raises(FMPUnavailable):
                stock_news("AAPL")

    def test_5xx_propagates_as_fmpunavailable(self, api_key, monkeypatch):
        monkeypatch.setattr(settings, "FMP_MAX_RETRIES", 0)
        with patch("data.fmp_client.requests.get", return_value=_resp(503)):
            with pytest.raises(FMPUnavailable):
                stock_news("AAPL")

    def test_no_api_key_raises_without_touching_the_network(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            with pytest.raises(FMPUnavailable):
                stock_news("AAPL")
        assert get.called is False


# ---------------------------------------------------------------------------
# data/fmp_client.py::parse_news_published_date
# ---------------------------------------------------------------------------

class TestParseNewsPublishedDate:
    """Converts FMP's naive US-Eastern ``publishedDate`` string into a
    UTC-aware ``datetime``. Uses ``ZoneInfo("America/New_York")`` rather than
    a fixed UTC-4/UTC-5 offset specifically so the EDT/EST daylight-saving
    transition is handled correctly year-round -- the two season-specific
    tests below are the proof."""

    def test_edt_season_converts_4_hours_ahead(self):
        """The REAL verified case from the implementation's own docstring:
        FMP reported ``publishedDate: "2026-08-02 14:51:00"`` for a
        GlobeNewswire article whose OWN page stated "August 02, 2026 14:51
        ET" -- an exact match. August is EDT (UTC-4)."""
        result = parse_news_published_date("2026-08-02 14:51:00")
        assert result == datetime(2026, 8, 2, 18, 51, 0, tzinfo=timezone.utc)

    def test_est_season_converts_5_hours_ahead(self):
        """Winter is EST (UTC-5), not the summer's UTC-4 -- proves the DST
        transition is genuinely handled, not a hardcoded offset that would
        silently be wrong half the year."""
        result = parse_news_published_date("2026-01-15 09:00:00")
        assert result == datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    def test_empty_string_returns_none(self):
        assert parse_news_published_date("") is None

    def test_none_like_input_returns_none(self):
        assert parse_news_published_date(None) is None  # type: ignore[arg-type]

    def test_malformed_string_returns_none(self):
        assert parse_news_published_date("not-a-date") is None

    def test_returned_datetime_is_utc_aware(self):
        result = parse_news_published_date("2026-08-02 14:51:00")
        assert result is not None
        assert result.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# data/sentiment_sources.py::FMPNewsSource
# ---------------------------------------------------------------------------

class TestFMPNewsSource:
    """``data.fmp_client.stock_news`` is mocked directly here (coarser than
    mocking ``requests.get``) since the HTTP-level throttle/retry/breaker
    machinery is already covered by ``tests/test_fmp_client.py`` -- this
    class is about ``FMPNewsSource``'s own gate/pagination/filter/shape
    contract."""

    @pytest.fixture
    def enabled(self, monkeypatch):
        """Both halves of the two-gate convention set, ``FINBERT_ENABLED``
        off (lexicon fallback -- no model download in a test), and
        ``HISTORICAL_STORE_ENABLED`` off so ``score_headlines()``'s content-
        hash cache lookup never touches a real DB in these tests."""
        monkeypatch.setattr(settings, "FMP_NEWS_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
        monkeypatch.setattr(settings, "FINBERT_ENABLED", False)
        monkeypatch.setattr(settings, "HISTORICAL_STORE_ENABLED", False)
        return settings

    # --- Two-gate disable paths -----------------------------------------

    def test_disabled_by_default_returns_empty_no_network(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_ENABLED", False)
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
        src = FMPNewsSource()
        with patch("data.fmp_client.stock_news") as mock_stock_news:
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []
        mock_stock_news.assert_not_called()

    def test_enabled_but_no_api_key_returns_empty_no_network(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        src = FMPNewsSource()
        with patch("data.fmp_client.stock_news") as mock_stock_news:
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []
        mock_stock_news.assert_not_called()

    # --- Pagination -------------------------------------------------------

    def test_single_short_page_stops_pagination(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 100)
        monkeypatch.setattr(settings, "FMP_NEWS_MAX_PAGES", 10)
        src = FMPNewsSource()
        articles = [_article(title=f"headline {i}") for i in range(3)]  # < page_limit
        with patch("data.fmp_client.stock_news", return_value=articles) as mock_stock_news:
            with patch(
                "signals.news_catalyst.score_headlines", side_effect=_mock_score_headlines,
            ):
                docs = src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert mock_stock_news.call_count == 1
        assert len(docs) == 3

    def test_pagination_continues_until_max_pages_then_stops(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 2)
        monkeypatch.setattr(settings, "FMP_NEWS_MAX_PAGES", 3)
        src = FMPNewsSource()
        full_page = [_article(title=f"headline {i}") for i in range(2)]  # == page_limit
        with patch("data.fmp_client.stock_news", return_value=full_page) as mock_stock_news:
            with patch(
                "signals.news_catalyst.score_headlines", side_effect=_mock_score_headlines,
            ):
                src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc))
        # Every page came back full (== page_limit, which alone would keep
        # pagination going) -- only FMP_NEWS_MAX_PAGES stops it, never more.
        assert mock_stock_news.call_count == 3

    def test_deadline_exceeded_stops_pagination_after_first_page(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 2)
        monkeypatch.setattr(settings, "FMP_NEWS_MAX_PAGES", 10)
        src = FMPNewsSource()
        checks = {"n": 0}

        def _fake_deadline_exceeded() -> bool:
            checks["n"] += 1
            return checks["n"] > 1  # not exceeded on the first check, exceeded after

        monkeypatch.setattr(src, "deadline_exceeded", _fake_deadline_exceeded)
        full_page = [_article(title=f"headline {i}") for i in range(2)]  # a FULL page
        with patch("data.fmp_client.stock_news", return_value=full_page) as mock_stock_news:
            with patch(
                "signals.news_catalyst.score_headlines", side_effect=_mock_score_headlines,
            ):
                docs = src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc))
        # A full page alone would keep paginating (see the MAX_PAGES test
        # above) -- the deadline is what stops it here, after just one page.
        assert mock_stock_news.call_count == 1
        assert len(docs) == 2

    # --- Filtering ----------------------------------------------------

    def test_article_before_since_is_excluded(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 100)
        src = FMPNewsSource()
        articles = [
            _article(title="Old news", published="2025-01-01 09:00:00"),
            _article(title="Fresh news", published="2026-08-02 14:51:00"),
        ]
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("data.fmp_client.stock_news", return_value=articles):
            with patch(
                "signals.news_catalyst.score_headlines", side_effect=_mock_score_headlines,
            ):
                docs = src.fetch("AAPL", since)
        assert len(docs) == 1
        assert docs[0].text_content == "Fresh news"

    def test_missing_title_article_is_skipped(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 100)
        src = FMPNewsSource()
        articles = [_article(title=""), _article(title="Real headline")]
        with patch("data.fmp_client.stock_news", return_value=articles):
            with patch(
                "signals.news_catalyst.score_headlines", side_effect=_mock_score_headlines,
            ):
                docs = src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(docs) == 1
        assert docs[0].text_content == "Real headline"

    # --- Document shape -------------------------------------------------

    def test_documents_carry_source_name_uppercased_symbol_no_credibility_metadata(
        self, enabled, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 100)
        src = FMPNewsSource()
        articles = [_article(title="Apple headline")]
        with patch("data.fmp_client.stock_news", return_value=articles):
            with patch(
                "signals.news_catalyst.score_headlines", side_effect=_mock_score_headlines,
            ):
                docs = src.fetch("aapl", datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(docs) == 1
        doc = docs[0]
        assert isinstance(doc, SentimentDocument)
        assert doc.source_name == "fmp_news"
        assert doc.symbol == "AAPL"  # uppercased even though "aapl" was passed in
        assert doc.author_followers is None
        assert doc.account_age_days is None

    # --- Failure handling (CONSTRAINT #6: fetch() never raises) -----------

    def test_exception_in_stock_news_is_caught_and_returns_empty(self, enabled):
        src = FMPNewsSource()
        with patch("data.fmp_client.stock_news", side_effect=RuntimeError("boom")):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_fmpunavailable_from_stock_news_degrades_to_empty(self, enabled):
        src = FMPNewsSource()
        with patch("data.fmp_client.stock_news", side_effect=FMPUnavailable("cooldown open")):
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert docs == []

    def test_scoring_failure_is_caught_and_returns_empty(self, enabled, monkeypatch):
        """A score_headlines() failure must not raise out of fetch() --
        mirrors GoogleNewsRSSSource's identical CONSTRAINT #6 guarantee."""
        monkeypatch.setattr(settings, "FMP_NEWS_PAGE_LIMIT", 100)
        src = FMPNewsSource()
        articles = [_article(title="Apple headline")]
        with patch("data.fmp_client.stock_news", return_value=articles):
            with patch(
                "signals.news_catalyst.score_headlines",
                side_effect=RuntimeError("scoring backend broke"),
            ):
                docs = src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert docs == []

    # --- Scoring integration (lexicon fallback, no FinBERT download) ------

    def test_scoring_lexicon_fallback_positive_and_negative_headlines(self, enabled):
        """Light integration check (not a full lexicon unit test -- see
        tests/test_news_catalyst.py::TestLexiconSentiment for that): with
        FINBERT_ENABLED=False, score_headlines() falls back to the keyword
        lexicon per headline, so a real (unmocked) scoring pass over a
        clearly-positive and a clearly-negative headline still lands on the
        correct side of zero."""
        src = FMPNewsSource()
        articles = [
            _article(
                title="Company beats earnings expectations and raises guidance",
                published="2026-08-02 09:00:00",
            ),
            _article(
                title="Stock crashes as losses mount and fraud investigation widens",
                published="2026-08-02 10:00:00",
            ),
        ]
        with patch("data.fmp_client.stock_news", return_value=articles):
            docs = src.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(docs) == 2
        scores = {d.text_content: d.raw_sentiment_score for d in docs}
        assert scores["Company beats earnings expectations and raises guidance"] > 0
        assert scores["Stock crashes as losses mount and fraud investigation widens"] < 0


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

class TestFMPNewsRegistration:
    def test_registered_in_source_registry(self):
        assert _SOURCE_REGISTRY["fmp_news"] is FMPNewsSource

    def test_priority_ordered_before_finnhub(self):
        assert "fmp_news" in _SOURCE_PRIORITY
        assert "finnhub" in _SOURCE_PRIORITY
        assert _SOURCE_PRIORITY.index("fmp_news") < _SOURCE_PRIORITY.index("finnhub")

    def test_treated_as_an_institutional_source_for_credibility(self):
        assert "fmp_news" in _INSTITUTIONAL_SOURCES
