import pytest
import time
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
