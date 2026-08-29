"""Tests for scripts/backfill_news_history_from_audit.py.

Covers: per-day aggregation (_backfill_day — real data, empty day,
tickers-filtering, dead-letter resilience on both the read and write
sides), and main()'s empty-audit guard. The empty-universe guard and the
repo-root import shim are covered by tests/test_backfill_scripts_invocation.py,
shared with backfill_news_history.py's byte-identical versions of both tests.
"""

import sys
from unittest import mock

from scripts import backfill_news_history_from_audit as backfill


class TestBackfillDay:
    def test_writes_real_aggregate_scores(self):
        store = mock.MagicMock()
        store.get_sentiment_aggregate_by_symbol.return_value = {
            "AAPL": {"credibility_weighted_sentiment": 0.4},
            "MSFT": {"credibility_weighted_sentiment": -0.2},
        }
        n = backfill._backfill_day(store, "2026-03-02", {"AAPL", "MSFT"})
        assert n == 2
        store.save_news_sentiment.assert_called_once()
        scores_arg, as_of_arg = store.save_news_sentiment.call_args.args
        assert scores_arg == {"AAPL": 0.4, "MSFT": -0.2}
        assert store.save_news_sentiment.call_args.kwargs.get("source") == "credibility_backfill"

    def test_filters_to_requested_tickers(self):
        store = mock.MagicMock()
        store.get_sentiment_aggregate_by_symbol.return_value = {
            "AAPL": {"credibility_weighted_sentiment": 0.4},
            "TSLA": {"credibility_weighted_sentiment": 0.9},
        }
        n = backfill._backfill_day(store, "2026-03-02", {"AAPL"})
        assert n == 1
        scores_arg = store.save_news_sentiment.call_args.args[0]
        assert scores_arg == {"AAPL": 0.4}

    def test_empty_day_writes_nothing(self):
        store = mock.MagicMock()
        store.get_sentiment_aggregate_by_symbol.return_value = {}
        n = backfill._backfill_day(store, "2026-03-02", {"AAPL"})
        assert n == 0
        store.save_news_sentiment.assert_not_called()

    def test_no_matching_tickers_writes_nothing(self):
        store = mock.MagicMock()
        store.get_sentiment_aggregate_by_symbol.return_value = {
            "TSLA": {"credibility_weighted_sentiment": 0.9},
        }
        n = backfill._backfill_day(store, "2026-03-02", {"AAPL"})
        assert n == 0
        store.save_news_sentiment.assert_not_called()

    def test_read_failure_never_raises(self):
        store = mock.MagicMock()
        store.get_sentiment_aggregate_by_symbol.side_effect = RuntimeError("db down")
        n = backfill._backfill_day(store, "2026-03-02", {"AAPL"})
        assert n == 0

    def test_write_failure_never_raises(self):
        store = mock.MagicMock()
        store.get_sentiment_aggregate_by_symbol.return_value = {
            "AAPL": {"credibility_weighted_sentiment": 0.4},
        }
        store.save_news_sentiment.side_effect = RuntimeError("db down")
        n = backfill._backfill_day(store, "2026-03-02", {"AAPL"})
        assert n == 0


class TestMainGuards:
    # test_empty_universe_logs_error_and_returns lives in
    # tests/test_backfill_scripts_invocation.py (shared, byte-identical
    # with backfill_news_history.py's version of this test).

    def test_empty_audit_table_logs_actionable_error(self, caplog):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                mock_store_cls.return_value.get_sentiment_archive_depth_by_source.return_value = {}
                with mock.patch.object(sys, "argv", ["backfill_news_history_from_audit.py"]):
                    with caplog.at_level("ERROR"):
                        backfill.main()  # must not raise
        assert any("backfill_sentiment_history.py" in r.message for r in caplog.records)

    def test_happy_path_backfills_every_trading_day(self):
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                mock_store = mock_store_cls.return_value
                mock_store.get_sentiment_archive_depth_by_source.return_value = {
                    "gdelt": {"document_count": 10, "earliest_as_of": "2026-01-01", "depth_days": 60},
                }
                mock_store.get_sentiment_aggregate_by_symbol.return_value = {
                    "AAPL": {"credibility_weighted_sentiment": 0.2},
                }
                with mock.patch.object(
                    sys, "argv",
                    ["backfill_news_history_from_audit.py", "--months", "0.2"],
                ):
                    backfill.main()
        # ~0.2 months ~= 6 days -> at least a couple of business days scanned.
        assert mock_store.get_sentiment_aggregate_by_symbol.call_count > 0
        assert mock_store.save_news_sentiment.call_count == mock_store.get_sentiment_aggregate_by_symbol.call_count

# TestInvocationForms (direct-path `--help` invocation) lives in
# tests/test_backfill_scripts_invocation.py, shared with
# backfill_news_history.py's and backfill_sentiment_history.py's
# byte-identical versions of this test.
