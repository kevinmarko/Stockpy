import pytest
import time
from datetime import datetime, timezone
from unittest.mock import patch
from desktop.daemon_runtime import OrchestratorDaemon
from settings import settings

@pytest.fixture
def mock_trends_settings(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 24)
    monkeypatch.setattr(settings, "DEFAULT_TICKERS", ["NVDA", "AAPL"])

def test_maybe_refresh_google_trends(mock_trends_settings):
    daemon = OrchestratorDaemon()
    
    with patch("data.trends_stitcher.GoogleTrendsStitcher") as MockStitcher, \
         patch("data.google_trends_client.fetch_overlapping_windows") as mock_fetch, \
         patch("data.trends_store.TrendsStore") as MockStore:
         
         # Mock fetch to return some dummy data
         mock_fetch.return_value = []
         
         daemon.maybe_refresh_google_trends()
         
         assert hasattr(daemon, "_last_google_trends_refresh")
         assert daemon._last_google_trends_refresh > 0
         
         mock_fetch.assert_called()

def test_maybe_refresh_google_trends_throttled(mock_trends_settings):
    daemon = OrchestratorDaemon()
    daemon._last_google_trends_refresh = time.monotonic()
    
    with patch("data.google_trends_client.fetch_overlapping_windows") as mock_fetch:
         daemon.maybe_refresh_google_trends()
         mock_fetch.assert_not_called()

def test_maybe_refresh_google_trends_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    daemon = OrchestratorDaemon()

    with patch("data.google_trends_client.fetch_overlapping_windows") as mock_fetch:
         daemon.maybe_refresh_google_trends()
         mock_fetch.assert_not_called()
         assert not hasattr(daemon, "_last_google_trends_refresh")


def test_maybe_refresh_google_trends_isolates_symbol_failures(monkeypatch):
    """A failure on one symbol (e.g. a bad fetch/stitch/store) must not abort
    the remaining symbols in the same pass, and the throttle timestamp must
    still be updated afterward -- otherwise the failing symbol would defeat
    the throttle and every subsequent timer wake would retry immediately
    with no backoff. Mirrors this repo's per-ticker try/except convention.
    """
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 24)
    monkeypatch.setattr(settings, "DEFAULT_TICKERS", ["BAD", "GOOD"])

    daemon = OrchestratorDaemon()

    now = datetime.now(timezone.utc)
    dummy_series = [{now: 1.0}]

    with patch("data.trends_stitcher.GoogleTrendsStitcher") as MockStitcher, \
         patch("data.google_trends_client.fetch_overlapping_windows") as mock_fetch, \
         patch("data.trends_store.TrendsStore") as MockStoreCls:

        mock_store = MockStoreCls.return_value
        # BAD's fetch raises; GOOD's fetch succeeds -- proves the per-symbol
        # try/except keeps iterating past the failing symbol.
        mock_fetch.side_effect = [RuntimeError("boom for BAD"), dummy_series]

        stitched_mock = MockStitcher.stitch_multiple_intervals.return_value
        stitched_mock.empty = False
        stitched_mock.items.return_value = [(now, 1.0)]

        daemon.maybe_refresh_google_trends()

        # Both symbols were attempted despite BAD raising.
        assert mock_fetch.call_count == 2

        # Only GOOD ever reached the store (BAD raised before any store call).
        mock_store.save_stitched_series.assert_called_once()
        assert mock_store.save_stitched_series.call_args[0][0] == "GOOD"

        # Throttle timestamp updates even though BAD failed mid-loop.
        assert hasattr(daemon, "_last_google_trends_refresh")
        assert daemon._last_google_trends_refresh > 0
