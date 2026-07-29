"""
tests/test_gdelt_rate_limiter.py
=================================
Unit tests for the shared GDELT rate limiter in ``data/sentiment_sources.py``.

The bug this covers, concretely: ``GDELTSource.fetch`` chunks a historical
``since`` into 7-day windows, so a LIVE per-cycle fetch is one request and
looked fine, while a real 6-month backfill
(``scripts/backfill_sentiment_history.py --months 6``) fired ~26 unspaced
requests per symbol. Across an operator universe that drew HTTP 429 for
substantially every one of them (measured 2026-07-29, 33 symbols): the
backfill burned wall-clock, archived nothing, and logged one warning per
window.

Both halves of that outage are covered here, because the fix initially only
handled one. Minutes after the throttle was added GDELT stopped 429-ing this
host and started READ-TIMING-OUT instead, and one symbol still cost 262 s for
zero documents -- 26 windows x a 10 s timeout. Hence the breaker counts
consecutive FAILURES of any kind, and
``TestCooldownCircuitBreaker`` asserts both the 429 path and the
transport-error path.

No real network requests and no real sleeps: ``requests.get`` is
monkeypatched and the module's ``time`` is replaced with a fake clock, so
these assert the limiter's ACTUAL spacing/backoff arithmetic rather than
waiting it out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.sentiment_sources import (
    GDELTUnavailable,
    GDELTSource,
    GDELTVolumeSource,
    _gdelt_get,
    reset_gdelt_rate_limiter,
)
from settings import settings


class FakeClock:
    """Deterministic stand-in for the module's ``time``.

    ``sleep`` advances the clock instead of blocking, so a test can assert
    exactly how long the limiter *would* have waited without waiting.
    """

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _resp(status: int = 200, *, payload=None, retry_after=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    resp.json.return_value = payload if payload is not None else {"articles": []}
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("data.sentiment_sources.time", fake)
    reset_gdelt_rate_limiter()
    yield fake
    reset_gdelt_rate_limiter()


@pytest.fixture
def limiter_settings(monkeypatch):
    """Explicit, non-default limiter knobs. The root conftest zeroes the
    interval for the suite at large; the limiter's own tests must opt back
    in or they would assert nothing."""
    monkeypatch.setattr(settings, "GDELT_MIN_REQUEST_INTERVAL_SECONDS", 5.0)
    monkeypatch.setattr(settings, "GDELT_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "GDELT_RETRY_BACKOFF_SECONDS", 4.0)
    monkeypatch.setattr(settings, "GDELT_COOLDOWN_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GDELT_COOLDOWN_SECONDS", 300.0)
    return settings


class TestThrottleSpacing:
    def test_consecutive_requests_are_spaced_by_the_minimum_interval(
        self, clock, limiter_settings
    ):
        with patch("data.sentiment_sources.requests.get", return_value=_resp()) as get:
            _gdelt_get({"query": "AAPL"})
            _gdelt_get({"query": "MSFT"})
            _gdelt_get({"query": "NVDA"})

        assert get.call_count == 3
        # First request is issued immediately (the clock starts far past the
        # zero-initialised last-request time); each subsequent one waits the
        # full interval because the fake transport returns instantly.
        assert clock.sleeps == [5.0, 5.0]

    def test_elapsed_time_is_credited_against_the_interval(self, clock, limiter_settings):
        """A slow request must not then also sleep the full interval -- the
        spacing is between issuance times, not an unconditional delay."""
        def slow_get(*_args, **_kwargs):
            clock.now += 3.0  # the request itself took 3 s
            return _resp()

        with patch("data.sentiment_sources.requests.get", side_effect=slow_get):
            _gdelt_get({"query": "AAPL"})
            _gdelt_get({"query": "MSFT"})

        assert clock.sleeps == [2.0]  # 5 s interval - 3 s already elapsed

    def test_zero_interval_and_zero_retries_reproduce_pre_limiter_behaviour(
        self, clock, monkeypatch
    ):
        monkeypatch.setattr(settings, "GDELT_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(settings, "GDELT_MAX_RETRIES", 0)
        with patch("data.sentiment_sources.requests.get", return_value=_resp()) as get:
            _gdelt_get({"query": "AAPL"})
            _gdelt_get({"query": "MSFT"})
        assert get.call_count == 2
        assert clock.sleeps == []  # exactly one request each, no waiting


class TestRetryAndBackoff:
    def test_429_is_retried_with_exponential_backoff_then_raises(
        self, clock, limiter_settings
    ):
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)) as get:
            with pytest.raises(GDELTUnavailable):
                _gdelt_get({"query": "AAPL"})

        # 1 initial attempt + GDELT_MAX_RETRIES(2) retries.
        assert get.call_count == 3
        # 4 s backoff (4 * 2**0), then only 1 s more to reach the 5 s issuance
        # spacing -- the backoff already counts toward it, and double-waiting
        # would be the bug. Then 8 s (4 * 2**1); the third attempt is the last,
        # so it gives up rather than backing off again.
        assert clock.sleeps == [4.0, 1.0, 8.0]

    def test_a_retry_that_succeeds_returns_the_response(self, clock, limiter_settings):
        ok = _resp(200)
        with patch(
            "data.sentiment_sources.requests.get", side_effect=[_resp(429), ok]
        ) as get:
            assert _gdelt_get({"query": "AAPL"}) is ok
        assert get.call_count == 2

    def test_retry_after_header_takes_precedence_over_computed_backoff(
        self, clock, limiter_settings
    ):
        with patch(
            "data.sentiment_sources.requests.get",
            side_effect=[_resp(429, retry_after="30"), _resp(200)],
        ):
            _gdelt_get({"query": "AAPL"})
        assert 30.0 in clock.sleeps  # the server's number, not our 4 s
        assert 4.0 not in clock.sleeps

    def test_unparseable_retry_after_falls_back_to_computed_backoff(
        self, clock, limiter_settings
    ):
        with patch(
            "data.sentiment_sources.requests.get",
            side_effect=[_resp(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"), _resp(200)],
        ):
            _gdelt_get({"query": "AAPL"})
        assert 4.0 in clock.sleeps

    def test_5xx_is_retried_like_a_429(self, clock, limiter_settings):
        with patch("data.sentiment_sources.requests.get", return_value=_resp(503)) as get:
            with pytest.raises(GDELTUnavailable):
                _gdelt_get({"query": "AAPL"})
        assert get.call_count == 3


class TestCooldownCircuitBreaker:
    def test_cooldown_opens_after_the_threshold_and_skips_without_requesting(
        self, clock, limiter_settings
    ):
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)) as get:
            with pytest.raises(GDELTUnavailable):
                _gdelt_get({"query": "AAPL"})
            calls_during_first = get.call_count
            # Threshold is 3 and the first call already produced 3 rate-limited
            # responses (its retries included), so the cooldown is now open:
            # this must not touch the network at all.
            with pytest.raises(GDELTUnavailable):
                _gdelt_get({"query": "MSFT"})
            assert get.call_count == calls_during_first

    def test_cooldown_expires_and_requests_resume(self, clock, limiter_settings):
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)):
            with pytest.raises(GDELTUnavailable):
                _gdelt_get({"query": "AAPL"})

        clock.now += 301.0  # past GDELT_COOLDOWN_SECONDS
        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)) as get:
            _gdelt_get({"query": "MSFT"})
        assert get.call_count == 1

    def test_one_success_clears_the_consecutive_count(self, clock, limiter_settings):
        """Two isolated 429s separated by a success must not accumulate toward
        the threshold -- the breaker is for a sustained block, not noise."""
        with patch(
            "data.sentiment_sources.requests.get",
            side_effect=[_resp(429), _resp(200), _resp(429), _resp(200)],
        ) as get:
            _gdelt_get({"query": "AAPL"})
            _gdelt_get({"query": "MSFT"})
        assert get.call_count == 4  # nothing was skipped

    def test_zero_threshold_disables_the_cooldown(self, clock, limiter_settings, monkeypatch):
        monkeypatch.setattr(settings, "GDELT_COOLDOWN_THRESHOLD", 0)
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)) as get:
            for _ in range(3):
                with pytest.raises(GDELTUnavailable):
                    _gdelt_get({"query": "AAPL"})
        # 3 calls x (1 attempt + 2 retries) -- every one actually issued.
        assert get.call_count == 9

    def test_one_transport_error_does_not_open_the_cooldown(self, clock, limiter_settings):
        """A single flaky socket must not blind the platform to GDELT for five
        minutes -- the breaker needs CONSECUTIVE failures, not one."""
        with patch(
            "data.sentiment_sources.requests.get", side_effect=RuntimeError("connection reset")
        ):
            with pytest.raises(RuntimeError):
                _gdelt_get({"query": "AAPL"})

        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)) as get:
            _gdelt_get({"query": "MSFT"})
        assert get.call_count == 1  # not skipped -- one failure is not a trend

    def test_consecutive_transport_errors_do_open_the_cooldown(self, clock, limiter_settings):
        """Measured 2026-07-29: GDELT stopped 429-ing this host and started
        read-timing-out instead, and a cooldown that counted only 429s let a
        26-window backfill grind through every window at 10 s apiece for the
        same net-zero result. From the caller's side "refusing us" and "not
        answering us" have identical cost and identical remedy."""
        with patch(
            "data.sentiment_sources.requests.get", side_effect=RuntimeError("read timed out")
        ) as get:
            for _ in range(3):  # == GDELT_COOLDOWN_THRESHOLD
                with pytest.raises(RuntimeError):
                    _gdelt_get({"query": "AAPL"})
            assert get.call_count == 3

        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)) as get:
            with pytest.raises(GDELTUnavailable):
                _gdelt_get({"query": "MSFT"})
        assert get.call_count == 0  # skipped, not issued

    def test_a_success_between_transport_errors_resets_the_count(
        self, clock, limiter_settings
    ):
        def flaky(*_a, **_k):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        outcomes = [
            RuntimeError("blip"), RuntimeError("blip"), _resp(200),
            RuntimeError("blip"), RuntimeError("blip"), _resp(200),
        ]
        with patch("data.sentiment_sources.requests.get", side_effect=flaky) as get:
            for _ in range(2):
                for _ in range(2):
                    with pytest.raises(RuntimeError):
                        _gdelt_get({"query": "AAPL"})
                _gdelt_get({"query": "AAPL"})
        assert get.call_count == 6  # never skipped -- the run never reached 3

    def test_an_unresponsive_host_stops_the_backfill_fast_instead_of_grinding(
        self, clock, limiter_settings
    ):
        """End-to-end shape of the measured failure: with GDELT timing out,
        a 6-month fetch must cost a handful of requests, not one per window."""
        src = GDELTSource()
        with patch(
            "data.sentiment_sources.requests.get", side_effect=RuntimeError("read timed out")
        ) as get:
            docs = src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=180))
        assert docs == []
        # Threshold(3) failing windows, then the 4th window's _gdelt_get sees
        # the open cooldown and aborts the range -- not ~26 windows.
        assert get.call_count == 3

    def test_non_integer_status_is_not_inferred_as_rate_limiting(self, clock, limiter_settings):
        resp = MagicMock()
        del resp.status_code  # a transport exposing no status at all
        resp.json.return_value = {"articles": []}
        resp.raise_for_status = MagicMock()
        with patch("data.sentiment_sources.requests.get", return_value=resp):
            assert _gdelt_get({"query": "AAPL"}) is resp


class TestGDELTSourceIntegration:
    def test_backfill_abandons_remaining_windows_once_the_cooldown_opens(
        self, clock, limiter_settings
    ):
        """The headline fix: a throttled 6-month backfill must stop, not
        grind through ~26 certain-to-fail windows per symbol."""
        src = GDELTSource()
        since = datetime.now(timezone.utc) - timedelta(days=180)  # ~26 windows
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)) as get:
            docs = src.fetch("AAPL", since)

        assert docs == []
        # Only the first window's attempts (1 + 2 retries) were issued; the
        # remaining ~25 windows were abandoned rather than attempted.
        assert get.call_count == 3

    def test_a_single_malformed_window_still_does_not_abandon_the_range(
        self, clock, limiter_settings
    ):
        """Regression guard on the distinction the fix introduces: only rate
        limiting aborts the range. A one-off parse/transport failure keeps the
        pre-existing skip-this-window-and-continue behaviour."""
        src = GDELTSource()
        good = _resp(200, payload={
            "articles": [
                {"title": "Real historical headline", "seendate": "20260601T140000Z", "tone": 1.0},
            ]
        })
        since = datetime.now(timezone.utc) - timedelta(days=21)  # 3 windows
        with patch(
            "data.sentiment_sources.requests.get",
            side_effect=[RuntimeError("window 1 network error"), good, good],
        ):
            docs = src.fetch("AAPL", since)
        assert len(docs) == 2

    def test_wall_clock_deadline_stops_the_window_loop(self, clock, limiter_settings):
        """The composite checks its budget BETWEEN sources, which cannot
        interrupt a 26-window loop already in progress -- with throttling
        those windows are minutes of sleeps, so the loop must check too."""
        src = GDELTSource()
        src.deadline = clock.monotonic() + 12.0  # room for ~2 spaced requests
        since = datetime.now(timezone.utc) - timedelta(days=180)
        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)) as get:
            src.fetch("AAPL", since)
        assert 0 < get.call_count < 26

    def test_no_deadline_means_unbounded_as_before(self, clock, monkeypatch):
        monkeypatch.setattr(settings, "GDELT_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
        src = GDELTSource()
        assert src.deadline is None
        assert src.deadline_exceeded() is False
        # Just under 5 chunks. Deliberately not exactly 35 days: `fetch` reads
        # the real clock a hair after this line, which would tip a flush 35 days
        # into a 6th one-second window and make the assertion flaky.
        since = datetime.now(timezone.utc) - timedelta(days=34, hours=23)
        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)) as get:
            src.fetch("AAPL", since)
        assert get.call_count == 5

    def test_composite_assigns_its_cycle_deadline_to_each_source(self, monkeypatch):
        from data.sentiment_sources import CompositeSentimentSource

        composite = CompositeSentimentSource(sources={"gdelt": GDELTSource()})
        composite.reset_cycle()
        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)):
            composite.fetch_all("AAPL", datetime.now(timezone.utc) - timedelta(days=1))
        assert composite._sources["gdelt"].deadline == composite._cycle_deadline
        assert composite._sources["gdelt"].deadline is not None


class TestSharedBudgetAcrossBothConsumers:
    def test_volume_source_is_skipped_while_the_artlist_cooldown_is_open(
        self, clock, limiter_settings
    ):
        """One host, one budget. If GDELTSource has already been throttled
        into cooldown, GDELTVolumeSource must not keep hammering the same
        endpoint from the Sector Heat path."""
        src = GDELTSource()
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)):
            src.fetch("AAPL", datetime.now(timezone.utc) - timedelta(days=180))

        with patch("data.sentiment_sources.requests.get", return_value=_resp(200)) as get:
            counts = GDELTVolumeSource().fetch_daily_counts(
                "technology", datetime.now(timezone.utc) - timedelta(days=30),
            )
        assert counts == {}         # honest empty series, never fabricated
        assert get.call_count == 0  # skipped, not issued

    def test_volume_source_rate_limit_degrades_to_empty_not_an_exception(
        self, clock, limiter_settings
    ):
        with patch("data.sentiment_sources.requests.get", return_value=_resp(429)):
            counts = GDELTVolumeSource().fetch_daily_counts(
                "technology", datetime.now(timezone.utc) - timedelta(days=30),
            )
        assert counts == {}
