"""Tests for the Google Trends API client and rate-limiting."""
import time
from datetime import datetime
from unittest import mock

import pandas as pd
import pytest

from data import google_trends_client
from settings import settings


@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset the module-level state before and after each test."""
    google_trends_client.reset_limiter_state()
    yield
    google_trends_client.reset_limiter_state()


def test_fetch_disabled():
    """Test that fetching returns early when disabled in settings."""
    settings.GOOGLE_TRENDS_ENABLED = False
    results = google_trends_client.fetch_overlapping_windows("AAPL", "2023-01-01", "2023-03-01")
    assert results == []


@mock.patch("data.google_trends_client.TrendReq")
def test_fetch_success(mock_trend_req):
    """Test successful data fetching in overlapping windows."""
    settings.GOOGLE_TRENDS_ENABLED = True
    settings.GOOGLE_TRENDS_WINDOW_DAYS = 90
    settings.GOOGLE_TRENDS_OVERLAP_DAYS = 30
    settings.GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS = 0.0
    
    mock_instance = mock_trend_req.return_value
    
    # Mock interest_over_time to return a dummy dataframe
    dates = pd.date_range("2023-01-01", periods=10)
    df = pd.DataFrame({"AAPL": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "isPartial": [False]*10}, index=dates)
    mock_instance.interest_over_time.return_value = df
    
    results = google_trends_client.fetch_overlapping_windows(
        "AAPL", "2023-01-01", "2023-06-01", window_days=90, overlap_days=30
    )
    
    # 2023-01-01 to 2023-06-01 is 151 days.
    # W1: 01-01 to 04-01 (90 days). Next start: 04-01 - 30 days = 03-02
    # W2: 03-02 to 05-31 (90 days). Next start: 05-31 - 30 days = 05-01
    # W3: 05-01 to 06-01 (31 days). 
    # Should be 3 windows
    assert len(results) == 3
    assert mock_instance.interest_over_time.call_count == 3
    assert len(results[0]) == 10
    assert results[0].name == "AAPL"


@mock.patch("data.google_trends_client.time.sleep")
@mock.patch("data.google_trends_client.TrendReq")
def test_fetch_retries_and_cooldown(mock_trend_req, mock_sleep):
    """Test that failed requests trigger retries and eventually the cooldown."""
    settings.GOOGLE_TRENDS_ENABLED = True
    settings.GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS = 0.0
    settings.GOOGLE_TRENDS_MAX_RETRIES = 1
    settings.GOOGLE_TRENDS_COOLDOWN_THRESHOLD = 2
    settings.GOOGLE_TRENDS_COOLDOWN_SECONDS = 300.0
    
    mock_instance = mock_trend_req.return_value
    mock_instance.interest_over_time.side_effect = Exception("HTTP 429 Too Many Requests")
    
    # W1 triggers attempt 0 (fail), attempt 1 (fail) -> 2 failures total.
    # Cooldown threshold is 2, so cooldown triggers!
    results = google_trends_client.fetch_overlapping_windows(
        "AAPL", "2023-01-01", "2023-06-01"
    )
    
    assert results == []
    assert mock_instance.interest_over_time.call_count == 2
    
    # Second fetch should immediately return [] due to cooldown
    mock_instance.interest_over_time.reset_mock()
    results2 = google_trends_client.fetch_overlapping_windows(
        "AAPL", "2023-01-01", "2023-06-01"
    )
    
    assert results2 == []
    assert mock_instance.interest_over_time.call_count == 0


@mock.patch("data.google_trends_client.TrendReq")
def test_fetch_empty_dataframe(mock_trend_req):
    """Test that an empty dataframe does not trigger an exception or cooldown."""
    settings.GOOGLE_TRENDS_ENABLED = True
    settings.GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS = 0.0
    
    mock_instance = mock_trend_req.return_value
    mock_instance.interest_over_time.return_value = pd.DataFrame()
    
    results = google_trends_client.fetch_overlapping_windows(
        "AAPL", "2023-01-01", "2023-02-01"
    )
    
    # Should not crash, returns empty list (or empty series list)
    # The current implementation breaks the retry loop and doesn't append success, so it returns []
    assert results == []
    assert mock_instance.interest_over_time.call_count == 1
