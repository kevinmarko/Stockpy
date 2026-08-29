"""Tests for scripts/backfill_news_history.py.

Covers: the pure historical-headline/earnings parsing helpers
(_fetch_headlines/_fetch_earnings_dates/_next_earnings_on), the per-symbol
trailing-window reconstruction (_backfill_symbol — honest NaN vs real-score
days, [-1, 1] clipping), main()'s dead-letter resilience and empty-universe/
no-client guards, and the repo-root import shim (mirrors
tests/test_backfill_sentiment_history.py's identical TestInvocationForms).
"""

import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import backfill_news_history as backfill

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestFetchHeadlines:
    def test_parses_valid_items(self):
        client = mock.MagicMock()
        ts = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())
        client.company_news.return_value = [
            {"headline": "Widgets beat estimates", "datetime": ts},
        ]
        out = backfill._fetch_headlines(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert len(out) == 1
        as_of, headline = out[0]
        assert headline == "Widgets beat estimates"
        assert as_of == datetime(2026, 3, 1, tzinfo=timezone.utc)

    def test_skips_items_missing_headline_or_datetime(self):
        client = mock.MagicMock()
        client.company_news.return_value = [
            {"headline": "", "datetime": 123456},
            {"headline": "No timestamp"},
            {"headline": "Fine", "datetime": None},
        ]
        out = backfill._fetch_headlines(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert out == []

    def test_non_list_result_returns_empty(self):
        client = mock.MagicMock()
        client.company_news.return_value = {"error": "rate limited"}
        out = backfill._fetch_headlines(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert out == []

    def test_client_exception_never_raises(self):
        client = mock.MagicMock()
        client.company_news.side_effect = RuntimeError("network down")
        out = backfill._fetch_headlines(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert out == []


class TestFetchEarningsDates:
    def test_parses_and_sorts(self):
        client = mock.MagicMock()
        client.earnings_calendar.return_value = {
            "earningsCalendar": [
                {"date": "2026-06-01"},
                {"date": "2026-02-01"},
            ]
        }
        out = backfill._fetch_earnings_dates(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert out == [
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 1, tzinfo=timezone.utc),
        ]

    def test_skips_malformed_dates(self):
        client = mock.MagicMock()
        client.earnings_calendar.return_value = {
            "earningsCalendar": [{"date": ""}, {"date": "not-a-date"}]
        }
        out = backfill._fetch_earnings_dates(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert out == []

    def test_client_exception_never_raises(self):
        client = mock.MagicMock()
        client.earnings_calendar.side_effect = RuntimeError("network down")
        out = backfill._fetch_earnings_dates(
            client, "AAPL",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert out == []


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
        client = mock.MagicMock()
        with mock.patch.object(backfill, "_fetch_headlines", return_value=headlines), \
             mock.patch.object(backfill, "_fetch_earnings_dates", return_value=earnings or []):
            defaults = dict(
                symbol="AAPL", client=client, pipeline=None,
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
    def test_empty_universe_logs_error_and_returns(self, caplog):
        with mock.patch.object(backfill, "resolve_universe", return_value=[]):
            with mock.patch.object(sys, "argv", ["backfill_news_history.py"]):
                with caplog.at_level("ERROR"):
                    backfill.main()  # must not raise
        assert any("empty universe" in r.message for r in caplog.records)

    def test_no_finnhub_client_logs_error_and_returns(self, caplog):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "build_finnhub_client", return_value=None):
                with mock.patch.object(sys, "argv", ["backfill_news_history.py"]):
                    with caplog.at_level("ERROR"):
                        backfill.main()  # must not raise
        assert any("FINNHUB_API_KEY" in r.message for r in caplog.records)

    def test_per_symbol_failure_is_dead_lettered(self, caplog):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL", "MSFT"]):
            with mock.patch.object(backfill, "build_finnhub_client", return_value=mock.MagicMock()):
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
            with mock.patch.object(backfill, "build_finnhub_client", return_value=mock.MagicMock()):
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


class TestInvocationForms:
    """Direct-path invocation (`python scripts/backfill_news_history.py`)
    must not die with ModuleNotFoundError -- mirrors
    test_backfill_sentiment_history.py's identical regression test for the
    repo-root sys.path shim."""

    def test_direct_path_invocation_imports_cleanly(self):
        result = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / "backfill_news_history.py"), "--help"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "ModuleNotFoundError" not in result.stderr
        assert "--months" in result.stdout
