"""Tests for scripts/backfill_news_history.py.

Covers: the pure historical-headline/earnings parsing helpers
(_fetch_headlines/_fetch_earnings_dates/_next_earnings_on), the per-symbol
trailing-window reconstruction (_backfill_symbol — honest NaN vs real-score
days, [-1, 1] clipping), and main()'s dead-letter resilience / no-provider
guard. The repo-root import shim and the empty-universe guard are covered by
tests/test_backfill_scripts_invocation.py, shared with
backfill_news_history_from_audit.py's byte-identical versions of both tests.
"""

import math
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

from scripts import backfill_news_history as backfill


_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = datetime(2026, 4, 1, tzinfo=timezone.utc)


class TestFetchHeadlines:
    """FMP-only (Finnhub removed 2026-09): paginated ``stock_news`` gated on
    FMP_NEWS_ENABLED + FMP_API_KEY."""

    def test_parses_valid_items(self):
        articles = [
            {"title": "Widgets beat estimates", "publishedDate": "2026-03-01 09:30:00"},
        ]
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", "k"), \
             mock.patch("data.fmp_client.stock_news", return_value=articles):
            out = backfill._fetch_headlines("AAPL", _START, _END)
        assert len(out) == 1
        as_of, headline = out[0]
        assert headline == "Widgets beat estimates"
        assert as_of.year == 2026 and as_of.month == 3

    def test_skips_items_missing_title_or_unparseable_date(self):
        articles = [
            {"title": "", "publishedDate": "2026-03-01 09:30:00"},
            {"title": "No timestamp"},
            {"title": "Bad stamp", "publishedDate": "not-a-date"},
        ]
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", "k"), \
             mock.patch("data.fmp_client.stock_news", return_value=articles):
            out = backfill._fetch_headlines("AAPL", _START, _END)
        assert out == []

    def test_disabled_or_unkeyed_makes_no_network_call(self):
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", False), \
             mock.patch("data.fmp_client.stock_news") as stock_news:
            assert backfill._fetch_headlines("AAPL", _START, _END) == []
        stock_news.assert_not_called()
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", None), \
             mock.patch("data.fmp_client.stock_news") as stock_news:
            assert backfill._fetch_headlines("AAPL", _START, _END) == []
        stock_news.assert_not_called()

    def test_fmp_unavailable_never_raises(self):
        from data.fmp_client import FMPUnavailable

        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", "k"), \
             mock.patch("data.fmp_client.stock_news", side_effect=FMPUnavailable("down")):
            out = backfill._fetch_headlines("AAPL", _START, _END)
        assert out == []


class TestFetchEarningsDates:
    def test_parses_filters_and_sorts(self):
        rows = [
            {"event_date": "2026-06-01"},
            {"event_date": "2026-02-01"},
            {"event_date": "2025-01-01"},  # outside [start, end]
        ]
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", "k"), \
             mock.patch("data.fmp_feeds_company.fetch_earnings_rows", return_value=rows):
            out = backfill._fetch_earnings_dates(
                "AAPL", _START, datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
        assert out == [
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 1, tzinfo=timezone.utc),
        ]

    def test_skips_malformed_dates(self):
        rows = [{"event_date": ""}, {"event_date": "not-a-date"}]
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", "k"), \
             mock.patch("data.fmp_feeds_company.fetch_earnings_rows", return_value=rows):
            out = backfill._fetch_earnings_dates(
                "AAPL", _START, datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
        assert out == []

    def test_fetch_exception_never_raises(self):
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", True), \
             mock.patch("scripts.backfill_news_history.settings.FMP_API_KEY", "k"), \
             mock.patch("data.fmp_feeds_company.fetch_earnings_rows",
                        side_effect=RuntimeError("network down")):
            out = backfill._fetch_earnings_dates(
                "AAPL", _START, datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
        assert out == []

    def test_disabled_makes_no_call(self):
        with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", False), \
             mock.patch("data.fmp_feeds_company.fetch_earnings_rows") as rows:
            assert backfill._fetch_earnings_dates("AAPL", _START, _END) == []
        rows.assert_not_called()


class TestNextEarningsOn:
    def test_returns_earliest_future_date(self):
        day = datetime(2026, 3, 1, tzinfo=timezone.utc)
        dates = [
            datetime(2026, 3, 10, tzinfo=timezone.utc),
            datetime(2026, 6, 1, tzinfo=timezone.utc),
        ]
        assert backfill._next_earnings_on(day, dates) == dates[0]

    def test_keeps_date_within_24h_grace_window(self):
        day = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
        recent = datetime(2026, 3, 1, 0, tzinfo=timezone.utc)  # 12h in the past
        assert backfill._next_earnings_on(day, [recent]) == recent

    def test_drops_date_older_than_24h(self):
        day = datetime(2026, 3, 5, tzinfo=timezone.utc)
        old = datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert backfill._next_earnings_on(day, [old]) is None

    def test_empty_list_returns_none(self):
        assert backfill._next_earnings_on(datetime(2026, 3, 1, tzinfo=timezone.utc), []) is None


class TestBackfillSymbol:
    def _run(self, headlines, earnings=None, **kwargs):
        with mock.patch.object(backfill, "_fetch_headlines", return_value=headlines), \
             mock.patch.object(backfill, "_fetch_earnings_dates", return_value=earnings or []):
            defaults = dict(
                symbol="AAPL", pipeline=None,
                start_date=datetime(2026, 3, 2, tzinfo=timezone.utc),  # Monday
                end_date=datetime(2026, 3, 6, tzinfo=timezone.utc),    # Friday
                lookback_days=7, suppress_hours=48.0, dampen_days=7.0,
            )
            defaults.update(kwargs)
            return backfill._backfill_symbol(**defaults)

    def test_day_with_no_headlines_in_window_is_nan(self):
        day_scores, n_headlines = self._run(headlines=[])
        assert n_headlines == 0
        assert all(math.isnan(v) for v in day_scores.values())
        # One row per business day, Mon-Fri inclusive.
        assert len(day_scores) == 5

    def test_day_with_positive_headline_gets_real_positive_score(self):
        headlines = [
            (datetime(2026, 3, 3, tzinfo=timezone.utc), "Widgets beat estimates, record profit"),
        ]
        day_scores, n_headlines = self._run(headlines=headlines)
        assert n_headlines == 1
        # 2026-03-02 (before the headline) has nothing in its trailing window yet.
        assert math.isnan(day_scores["2026-03-02"])
        # 2026-03-03 onward includes the real headline in the trailing window.
        assert day_scores["2026-03-03"] > 0
        assert day_scores["2026-03-06"] > 0

    def test_score_is_clipped_to_unit_interval(self):
        headlines = [
            (datetime(2026, 3, 3, tzinfo=timezone.utc), "beat beat beat record surge rally"),
        ]
        day_scores, _ = self._run(headlines=headlines)
        for v in day_scores.values():
            if not math.isnan(v):
                assert -1.0 <= v <= 1.0

    def test_earnings_proximity_suppresses_score_near_earnings(self):
        headlines = [
            (datetime(2026, 3, 3, tzinfo=timezone.utc), "Widgets beat estimates, record profit"),
        ]
        # Earnings scheduled the same day as the trading day being scored --
        # well within the default 48h suppress window.
        earnings = [datetime(2026, 3, 3, 1, tzinfo=timezone.utc)]
        day_scores, _ = self._run(headlines=headlines, earnings=earnings)
        assert day_scores["2026-03-03"] == 0.0


class TestMainGuards:
    # test_empty_universe_logs_error_and_returns lives in
    # tests/test_backfill_scripts_invocation.py (shared, byte-identical
    # with backfill_news_history_from_audit.py's version of this test).

    def test_no_provider_logs_error_and_returns(self, caplog):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch("scripts.backfill_news_history.settings.FMP_NEWS_ENABLED", False):
                with mock.patch.object(sys, "argv", ["backfill_news_history.py"]):
                    with caplog.at_level("ERROR"):
                        backfill.main()  # must not raise
        assert any("FMP_API_KEY" in r.message for r in caplog.records)

    def test_per_symbol_failure_is_dead_lettered(self, caplog):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL", "MSFT"]):
            with mock.patch.object(backfill, "_fmp_configured", return_value=True):
                with mock.patch.object(backfill, "_get_finbert_pipeline", return_value=None):
                    with mock.patch.object(
                        backfill, "_backfill_symbol",
                        side_effect=[RuntimeError("boom"), ({"2026-03-02": 0.1}, 1)],
                    ):
                        with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                            with mock.patch.object(sys, "argv", ["backfill_news_history.py"]):
                                with mock.patch("time.sleep"):
                                    backfill.main()  # must not raise despite AAPL failing
        assert mock_store_cls.return_value.save_news_sentiment.called

    def test_happy_path_writes_one_call_per_day_with_backfill_source(self):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "_fmp_configured", return_value=True):
                with mock.patch.object(backfill, "_get_finbert_pipeline", return_value=None):
                    with mock.patch.object(
                        backfill, "_backfill_symbol",
                        return_value=({"2026-03-02": 0.4, "2026-03-03": float("nan")}, 3),
                    ):
                        with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                            mock_store = mock_store_cls.return_value
                            with mock.patch.object(sys, "argv", ["backfill_news_history.py"]):
                                with mock.patch("time.sleep"):
                                    backfill.main()
        assert mock_store.save_news_sentiment.call_count == 2
        for call in mock_store.save_news_sentiment.call_args_list:
            assert call.kwargs.get("source") == "finbert_backfill"
            scores_arg = call.args[0]
            assert scores_arg.keys() == {"AAPL"}

# TestInvocationForms (direct-path `--help` invocation) lives in
# tests/test_backfill_scripts_invocation.py, shared with
# backfill_news_history_from_audit.py's and backfill_sentiment_history.py's
# byte-identical versions of this test.
