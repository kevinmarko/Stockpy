"""Tests for scripts/backfill_sentiment_history.py.

Covers: per-symbol dead-letter resilience (_process_one never raises),
resolve_universe wiring, the yahoo_rss/reddit caveat warnings, the
settings overrides applied for the run, and the repo-root import shim
(direct-path subprocess invocation, mirroring
tests/test_backfill_edgar_fundamentals.py's TestInvocationForms).
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from scripts import backfill_sentiment_history as backfill

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestProcessOne:
    def test_success_returns_count(self):
        mock_source = mock.MagicMock()
        mock_source.fetch_and_archive.return_value = [mock.MagicMock(), mock.MagicMock()]
        result = backfill._process_one("AAPL", mock_source, mock.ANY)
        assert result == ("AAPL", 2, None)
        mock_source.reset_cycle.assert_called_once()

    def test_failure_never_raises(self):
        mock_source = mock.MagicMock()
        mock_source.fetch_and_archive.side_effect = RuntimeError("network down")
        result = backfill._process_one("AAPL", mock_source, mock.ANY)  # must not raise
        assert result[0] == "AAPL"
        assert result[1] == 0
        assert "network down" in result[2]

    def test_reset_cycle_called_before_fetch(self):
        """Each symbol gets its own fresh budget/circuit-breaker/deadline."""
        mock_source = mock.MagicMock()
        call_order = []
        mock_source.reset_cycle.side_effect = lambda: call_order.append("reset")
        mock_source.fetch_and_archive.side_effect = lambda *a, **kw: call_order.append("fetch") or []
        backfill._process_one("AAPL", mock_source, mock.ANY)
        assert call_order == ["reset", "fetch"]


class TestMainEmptyUniverse:
    def test_empty_universe_logs_error_and_returns(self):
        with mock.patch.object(backfill, "resolve_universe", return_value=[]):
            with mock.patch.object(sys, "argv", ["backfill_sentiment_history.py"]):
                backfill.main()  # must not raise


class TestMainOverridesSettings:
    def test_sources_and_budget_overridden_for_this_run(self, monkeypatch):
        # main() mutates the real settings singleton by design (see its own
        # docstring on why that's safe for a standalone CLI process) --
        # monkeypatch guarantees these two attributes are restored after the
        # test regardless of what main() does, so this test can't leak state
        # into any other test sharing the same pytest process.
        monkeypatch.setattr(backfill.settings, "SENTIMENT_SOURCES", backfill.settings.SENTIMENT_SOURCES)
        monkeypatch.setattr(
            backfill.settings, "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",
            backfill.settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE,
        )
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "CompositeSentimentSource") as mock_composite_cls:
                mock_source = mock.MagicMock()
                mock_source.fetch_and_archive.return_value = []
                mock_composite_cls.return_value = mock_source
                with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                    mock_store_cls.return_value.get_sentiment_archive_depth_by_source.return_value = {}
                    with mock.patch.object(
                        sys, "argv",
                        ["backfill_sentiment_history.py", "--sources", "gdelt,edgar",
                         "--max-seconds-per-symbol", "120"],
                    ):
                        backfill.main()
        assert backfill.settings.SENTIMENT_SOURCES == "gdelt,edgar"
        assert backfill.settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE == 120.0

    def test_yahoo_rss_warns_but_does_not_crash(self, caplog, monkeypatch):
        monkeypatch.setattr(backfill.settings, "SENTIMENT_SOURCES", backfill.settings.SENTIMENT_SOURCES)
        monkeypatch.setattr(
            backfill.settings, "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",
            backfill.settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE,
        )
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "CompositeSentimentSource") as mock_composite_cls:
                mock_source = mock.MagicMock()
                mock_source.fetch_and_archive.return_value = []
                mock_composite_cls.return_value = mock_source
                with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                    mock_store_cls.return_value.get_sentiment_archive_depth_by_source.return_value = {}
                    with mock.patch.object(
                        sys, "argv",
                        ["backfill_sentiment_history.py", "--sources", "yahoo_rss"],
                    ):
                        backfill.main()  # must not raise
        assert any("yahoo_rss has no historical archive" in r.message for r in caplog.records)

    def test_reddit_caveat_warning_logged(self, caplog, monkeypatch):
        monkeypatch.setattr(backfill.settings, "SENTIMENT_SOURCES", backfill.settings.SENTIMENT_SOURCES)
        monkeypatch.setattr(
            backfill.settings, "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",
            backfill.settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE,
        )
        with mock.patch.object(backfill, "resolve_universe", return_value=["AAPL"]):
            with mock.patch.object(backfill, "CompositeSentimentSource") as mock_composite_cls:
                mock_source = mock.MagicMock()
                mock_source.fetch_and_archive.return_value = []
                mock_composite_cls.return_value = mock_source
                with mock.patch.object(backfill, "HistoricalStore") as mock_store_cls:
                    mock_store_cls.return_value.get_sentiment_archive_depth_by_source.return_value = {}
                    with mock.patch.object(
                        sys, "argv",
                        ["backfill_sentiment_history.py", "--sources", "reddit"],
                    ):
                        backfill.main()
        assert any("Reddit backfill caveat" in r.message for r in caplog.records)


class TestPrintDepthReport:
    def test_empty_archive_logs_informational_message(self, caplog):
        mock_store = mock.MagicMock()
        mock_store.get_sentiment_archive_depth_by_source.return_value = {}
        with caplog.at_level("INFO"):
            backfill._print_depth_report(mock_store)
        assert any("is empty" in r.message for r in caplog.records)

    def test_nonempty_archive_logs_per_source_depth(self, caplog):
        mock_store = mock.MagicMock()
        mock_store.get_sentiment_archive_depth_by_source.return_value = {
            "gdelt": {"document_count": 42, "earliest_as_of": "2026-02-01T00:00:00+00:00", "depth_days": 150},
        }
        with caplog.at_level("INFO"):
            backfill._print_depth_report(mock_store)
        assert any("gdelt" in r.message for r in caplog.records)


class TestInvocationForms:
    """Direct-path invocation (`python scripts/backfill_sentiment_history.py`)
    must not die with ModuleNotFoundError -- mirrors
    test_backfill_edgar_fundamentals.py's identical regression test for the
    repo-root sys.path shim."""

    def test_direct_path_invocation_imports_cleanly(self):
        result = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / "backfill_sentiment_history.py"), "--help"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "ModuleNotFoundError" not in result.stderr
        assert "--months" in result.stdout
