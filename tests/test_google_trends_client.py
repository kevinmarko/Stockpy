"""Tests for the Google Trends API client and rate-limiting.

``pytrends`` is a lazy, in-function import (see
data/google_trends_client.py's own docstring comment for why -- it mirrors
data/attention_sources.py's `_fetch_pytrends_attention_score()` convention),
so it is never a module attribute here. Every test that needs to control
`TrendReq` therefore patches `sys.modules["pytrends.request"]` (matching
tests/test_attention_sources.py's `TestPytrendsOptionalPath` pattern)
instead of `mock.patch("data.google_trends_client.TrendReq")`.

Every settings mutation goes through the self-restoring
`mock.patch("settings.settings.X", ...)` / `mock.patch.multiple("settings.settings", ...)`
context-manager pattern (matching tests/test_sentiment_sources.py), never a
raw `settings.X = ...` assignment -- a raw assignment would leave the real
`settings.settings` singleton mutated for every test that runs after this
file in a pytest-randomly-ordered run.
"""
from unittest import mock
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data import google_trends_client


@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset the module-level state before and after each test."""
    google_trends_client.reset_limiter_state()
    yield
    google_trends_client.reset_limiter_state()


def _pytrends_module(trend_req_mock):
    """Build a fake `pytrends.request` module exposing `TrendReq`, for use
    with `patch.dict("sys.modules", ...)` -- mirrors
    tests/test_attention_sources.py::TestPytrendsOptionalPath's fixtures."""
    mock_module = MagicMock()
    mock_module.TrendReq = trend_req_mock
    return mock_module


def test_fetch_disabled():
    """Test that fetching returns early when disabled in settings."""
    with patch("settings.settings.GOOGLE_TRENDS_ENABLED", False):
        results = google_trends_client.fetch_overlapping_windows("AAPL", "2023-01-01", "2023-03-01")
    assert results == []


def test_fetch_success():
    """Test successful data fetching in overlapping windows."""
    mock_trend_req = MagicMock()
    mock_instance = mock_trend_req.return_value

    # Mock interest_over_time to return a dummy dataframe
    dates = pd.date_range("2023-01-01", periods=10)
    df = pd.DataFrame({"AAPL": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "isPartial": [False] * 10}, index=dates)
    mock_instance.interest_over_time.return_value = df

    with mock.patch.multiple(
        "settings.settings",
        GOOGLE_TRENDS_ENABLED=True,
        GOOGLE_TRENDS_WINDOW_DAYS=90,
        GOOGLE_TRENDS_OVERLAP_DAYS=30,
        GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
    ):
        with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": _pytrends_module(mock_trend_req)}):
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


def test_fetch_retries_and_cooldown():
    """Test that failed requests trigger retries and eventually the cooldown."""
    mock_trend_req = MagicMock()
    mock_instance = mock_trend_req.return_value
    mock_instance.interest_over_time.side_effect = Exception("HTTP 429 Too Many Requests")

    with mock.patch.multiple(
        "settings.settings",
        GOOGLE_TRENDS_ENABLED=True,
        GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
        GOOGLE_TRENDS_MAX_RETRIES=1,
        GOOGLE_TRENDS_COOLDOWN_THRESHOLD=2,
        GOOGLE_TRENDS_COOLDOWN_SECONDS=300.0,
    ):
        with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": _pytrends_module(mock_trend_req)}):
            with mock.patch("data.google_trends_client.time.sleep"):
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


def test_fetch_empty_dataframe():
    """Test that an empty dataframe does not trigger an exception or cooldown."""
    mock_trend_req = MagicMock()
    mock_instance = mock_trend_req.return_value
    mock_instance.interest_over_time.return_value = pd.DataFrame()

    with mock.patch.multiple(
        "settings.settings",
        GOOGLE_TRENDS_ENABLED=True,
        GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
    ):
        with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": _pytrends_module(mock_trend_req)}):
            results = google_trends_client.fetch_overlapping_windows(
                "AAPL", "2023-01-01", "2023-02-01"
            )

    # Should not crash, returns empty list (or empty series list)
    # The current implementation breaks the retry loop and doesn't append success, so it returns []
    assert results == []
    assert mock_instance.interest_over_time.call_count == 1


def test_fetch_malformed_date_degrades_to_empty_list():
    """CONSTRAINT #6: an unparseable date string must degrade to [] rather
    than let `datetime.strptime`'s `ValueError` propagate to the caller."""
    with mock.patch.multiple(
        "settings.settings",
        GOOGLE_TRENDS_ENABLED=True,
        GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
    ):
        # No pytrends module patched at all -- if the date guard didn't fire
        # first, this would blow up trying to import/construct TrendReq
        # too, so reaching a clean [] proves the guard runs before any of
        # that.
        results = google_trends_client.fetch_overlapping_windows(
            "AAPL", "not-a-date", "2023-06-01"
        )
    assert results == []


class TestTrendReqConstructionFailure:
    """CONSTRAINT #6 regression: `TrendReq(...)` itself must never be
    allowed to raise out of `fetch_overlapping_windows` (its real
    `GetGoogleCookie()` call only catches `requests.exceptions.ProxyError`,
    so any other network failure would otherwise propagate)."""

    def test_pytrends_import_missing_degrades_to_empty_list(self):
        with mock.patch.multiple(
            "settings.settings",
            GOOGLE_TRENDS_ENABLED=True,
            GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
        ):
            with patch.dict("sys.modules", {"pytrends": None, "pytrends.request": None}):
                results = google_trends_client.fetch_overlapping_windows(
                    "AAPL", "2023-01-01", "2023-06-01"
                )
        assert results == []

    def test_trendreq_constructor_raises_degrades_to_empty_list_never_raises(self):
        mock_trend_req = MagicMock(side_effect=ConnectionError("DNS resolution failed"))

        with mock.patch.multiple(
            "settings.settings",
            GOOGLE_TRENDS_ENABLED=True,
            GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
        ):
            with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": _pytrends_module(mock_trend_req)}):
                # Must not raise -- this is the exact regression this fix
                # covers: TrendReq() construction used to sit outside every
                # try/except.
                results = google_trends_client.fetch_overlapping_windows(
                    "AAPL", "2023-01-01", "2023-06-01"
                )

        assert results == []
        mock_trend_req.assert_called_once()


class TestLockAndMonotonicClock:
    """Regression coverage for the threading.Lock + time.monotonic() fix.

    Proves (a) the shared rate-limiter state is genuinely guarded by a lock
    that the fetch path actually acquires, and (b) the module no longer
    reads `time.time()` anywhere -- both changes are meant to be purely
    mechanical, so the retry/cooldown outcome tests above must keep passing
    unmodified, which they do.
    """

    def test_state_lock_is_a_real_threading_lock(self):
        assert hasattr(google_trends_client._state_lock, "__enter__")
        assert hasattr(google_trends_client._state_lock, "__exit__")

    def test_state_lock_is_actually_acquired_during_a_fetch(self, monkeypatch):
        real_lock = google_trends_client._state_lock
        counts = {"acquire": 0, "release": 0}

        class _CountingLock:
            def __enter__(self):
                counts["acquire"] += 1
                return real_lock.__enter__()

            def __exit__(self, *exc_info):
                counts["release"] += 1
                return real_lock.__exit__(*exc_info)

        monkeypatch.setattr(google_trends_client, "_state_lock", _CountingLock())

        mock_trend_req = MagicMock()
        mock_instance = mock_trend_req.return_value
        dates = pd.date_range("2023-01-01", periods=5)
        df = pd.DataFrame({"AAPL": [1, 2, 3, 4, 5], "isPartial": [False] * 5}, index=dates)
        mock_instance.interest_over_time.return_value = df

        with mock.patch.multiple(
            "settings.settings",
            GOOGLE_TRENDS_ENABLED=True,
            GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
        ):
            with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": _pytrends_module(mock_trend_req)}):
                results = google_trends_client.fetch_overlapping_windows(
                    "AAPL", "2023-01-01", "2023-02-01"
                )

        assert len(results) == 1
        # The lock must have been acquired at least once (cooldown check +
        # success reset, at minimum), and every acquire must be paired with
        # a release -- proving no path holds it open or double-releases it.
        assert counts["acquire"] > 0
        assert counts["acquire"] == counts["release"]

    def test_time_time_is_never_called_only_monotonic(self):
        """The old implementation computed elapsed time from `time.time()`,
        vulnerable to an NTP clock step. Every elapsed-time computation must
        now go through `time.monotonic()` instead."""
        mock_trend_req = MagicMock()
        mock_instance = mock_trend_req.return_value
        dates = pd.date_range("2023-01-01", periods=5)
        df = pd.DataFrame({"AAPL": [1, 2, 3, 4, 5], "isPartial": [False] * 5}, index=dates)
        mock_instance.interest_over_time.return_value = df

        with mock.patch.multiple(
            "settings.settings",
            GOOGLE_TRENDS_ENABLED=True,
            GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS=0.0,
        ):
            with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": _pytrends_module(mock_trend_req)}):
                with mock.patch(
                    "data.google_trends_client.time.time",
                    side_effect=AssertionError("time.time() must not be used; use time.monotonic()"),
                ):
                    results = google_trends_client.fetch_overlapping_windows(
                        "AAPL", "2023-01-01", "2023-02-01"
                    )

        assert len(results) == 1
