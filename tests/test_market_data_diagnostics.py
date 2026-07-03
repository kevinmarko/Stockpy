"""tests/test_market_data_diagnostics.py — coverage for the Market Data tab helpers.

Exercises ``gui/market_data_diagnostics.py`` end-to-end without Streamlit so
the production module can be re-used from any caller. Tests are organised
around the four public surfaces of that module:

1.  ``classify_market_error`` — error-category matrix across yfinance,
    Alpaca, and Finnhub-style exception strings.
2.  ``validate_quote`` — happy path + every invariant violation.
3.  ``FetchHealthTracker`` — empty → healthy, mixed → degraded, all-fail →
    down; window roll-off.
4.  ``BatchQuoteFetcher`` — yields one BatchResult per symbol, throttles
    via the injected sleep, classifies errors automatically, updates the
    health tracker.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List

import pytest

from data.market_data import MarketDataError, Quote
from gui.market_data_diagnostics import (
    BatchQuoteFetcher,
    BatchResult,
    ErrorCategory,
    FetchHealthTracker,
    HealthStatus,
    QuoteValidation,
    category_label,
    classify_market_error,
    summarise_categories,
    validate_quote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _good_quote(sym: str = "AAPL", price: float = 150.0) -> Quote:
    return Quote(
        symbol=sym, price=price, bid=price - 0.05, ask=price + 0.05,
        timestamp=datetime.now(timezone.utc), is_stale=False, source="test",
    )


# ===========================================================================
# 1. classify_market_error
# ===========================================================================

class _StatusExc(Exception):
    """Stand-in for FinnhubAPIException / requests.HTTPError with status_code."""

    def __init__(self, msg: str, status_code: int) -> None:
        super().__init__(msg)
        self.status_code = status_code


class TestClassifyMarketError:
    @pytest.mark.parametrize(
        "exc_msg, expected",
        [
            ("429 Too Many Requests for url: https://yfinance.com",
             ErrorCategory.RATE_LIMIT),
            ("yfinance bars fetch failed for AAPL: rate limit exceeded",
             ErrorCategory.RATE_LIMIT),
            ("Quota exhausted: API rate limited",
             ErrorCategory.RATE_LIMIT),
            ("yfinance returned empty bars for ZZZZ",
             ErrorCategory.NOT_FOUND),
            ("No data found, symbol may be delisted",
             ErrorCategory.NOT_FOUND),
            ("404 Not Found",
             ErrorCategory.NOT_FOUND),
            ("HTTPSConnectionPool: read timeout=5.0",
             ErrorCategory.NETWORK_TIMEOUT),
            ("Max retries exceeded with url",
             ErrorCategory.NETWORK_TIMEOUT),
            ("Connection refused by alpaca.markets",
             ErrorCategory.NETWORK_TIMEOUT),
            ("json.decoder.JSONDecodeError: Unexpected token",
             ErrorCategory.MALFORMED),
            ("malformed response from provider",
             ErrorCategory.MALFORMED),
            ("Something weird happened",
             ErrorCategory.UNKNOWN),
        ],
    )
    def test_category_for_raw_string(self, exc_msg: str, expected: ErrorCategory) -> None:
        cat = classify_market_error(MarketDataError(exc_msg))
        assert cat is expected, (
            f"Expected {expected} for {exc_msg!r}, got {cat}"
        )

    def test_status_code_attribute_recognised(self) -> None:
        """Finnhub 429 exceptions only expose `status_code`, no text."""
        exc = _StatusExc("API request failed", status_code=429)
        assert classify_market_error(exc) is ErrorCategory.RATE_LIMIT

        exc404 = _StatusExc("not found", status_code=404)
        assert classify_market_error(exc404) is ErrorCategory.NOT_FOUND

    def test_chained_cause_walked(self) -> None:
        """A MarketDataError wrapping a Timeout still classifies as network."""
        inner = TimeoutError("read timed out")
        outer = MarketDataError("Alpaca quote fetch failed for SPY")
        outer.__cause__ = inner
        assert classify_market_error(outer) is ErrorCategory.NETWORK_TIMEOUT

    def test_category_label_round_trip(self) -> None:
        for cat in ErrorCategory:
            label = category_label(cat)
            assert isinstance(label, str) and label
            # No leaking enum identifier into the human label.
            assert cat.value not in label or cat is ErrorCategory.UNKNOWN


# ===========================================================================
# 2. validate_quote
# ===========================================================================

class TestValidateQuote:
    def test_happy_path(self) -> None:
        v = validate_quote(_good_quote())
        assert v.ok is True
        assert v.issues == ()
        assert v.label == "OK"

    def test_nan_price_flagged(self) -> None:
        q = Quote("AAPL", float("nan"), 149.95, 150.05,
                  datetime.now(timezone.utc), False, "test")
        v = validate_quote(q)
        assert v.ok is False
        assert any("price" in i for i in v.issues)
        assert v.label.startswith("⚠")

    def test_zero_price_flagged(self) -> None:
        q = Quote("AAPL", 0.0, float("nan"), float("nan"),
                  datetime.now(timezone.utc), False, "test")
        v = validate_quote(q)
        assert not v.ok

    def test_inverted_bid_ask_flagged(self) -> None:
        q = Quote("AAPL", 150.0, bid=151.0, ask=149.0,
                  timestamp=datetime.now(timezone.utc), is_stale=False, source="test")
        v = validate_quote(q)
        assert not v.ok
        assert any("bid" in i and "ask" in i for i in v.issues)

    def test_missing_one_side_does_not_flag(self) -> None:
        """Outside RTH some providers emit only the last trade — that is OK."""
        q = Quote("AAPL", 150.0, bid=float("nan"), ask=150.05,
                  timestamp=datetime.now(timezone.utc), is_stale=True, source="test")
        v = validate_quote(q)
        assert v.ok is True


# ===========================================================================
# 3. FetchHealthTracker
# ===========================================================================

class TestFetchHealthTracker:
    def test_empty_is_healthy_neutral(self) -> None:
        h = FetchHealthTracker(window=5)
        r = h.status()
        assert r.status is HealthStatus.HEALTHY
        assert r.total == 0
        assert math.isnan(r.success_rate)
        assert "no fetches yet" in r.badge()

    def test_all_success_healthy(self) -> None:
        h = FetchHealthTracker(window=5)
        for _ in range(5):
            h.record_success()
        r = h.status()
        assert r.status is HealthStatus.HEALTHY
        assert r.success_rate == 1.0

    def test_mixed_below_healthy_threshold_degrades(self) -> None:
        """3/5 successes (60%) is below the 90% healthy bar but above 50% → DEGRADED."""
        h = FetchHealthTracker(window=5)
        for _ in range(3):
            h.record_success()
        for _ in range(2):
            h.record_failure()
        assert h.status().status is HealthStatus.DEGRADED

    def test_all_failure_down(self) -> None:
        h = FetchHealthTracker(window=4)
        for _ in range(4):
            h.record_failure()
        assert h.status().status is HealthStatus.DOWN

    def test_window_rolls_off(self) -> None:
        h = FetchHealthTracker(window=3)
        for _ in range(3):
            h.record_failure()
        assert h.status().status is HealthStatus.DOWN
        for _ in range(3):
            h.record_success()
        # The 3 failures should now have rolled out of the window.
        assert h.status().status is HealthStatus.HEALTHY

    def test_invalid_thresholds_rejected(self) -> None:
        with pytest.raises(ValueError):
            FetchHealthTracker(window=0)
        with pytest.raises(ValueError):
            FetchHealthTracker(degraded_threshold=0.9, healthy_threshold=0.5)


# ===========================================================================
# 4. BatchQuoteFetcher
# ===========================================================================

class TestBatchQuoteFetcher:
    def test_streams_one_result_per_symbol(self) -> None:
        good = {"AAPL": _good_quote("AAPL"), "MSFT": _good_quote("MSFT", 420.0)}
        fetcher = BatchQuoteFetcher(
            fetch_fn=lambda s: good[s],
            spacing_seconds=0.0,
        )
        results = fetcher.fetch_all(["AAPL", "MSFT"])
        assert [r.symbol for r in results] == ["AAPL", "MSFT"]
        assert all(r.ok for r in results)
        assert all(r.error is None for r in results)

    def test_failure_classified_and_tracked(self) -> None:
        tracker = FetchHealthTracker(window=10)

        def raise_429(_s: str) -> Quote:
            raise MarketDataError("429 Too Many Requests")

        fetcher = BatchQuoteFetcher(
            fetch_fn=raise_429, spacing_seconds=0.0, health_tracker=tracker,
        )
        results = fetcher.fetch_all(["AAPL", "MSFT"])
        assert all(not r.ok for r in results)
        assert {r.category for r in results} == {ErrorCategory.RATE_LIMIT}
        # Both calls should have hit the failure ledger.
        assert tracker.status().failures == 2

    def test_health_tracker_records_successes(self) -> None:
        tracker = FetchHealthTracker(window=10)
        fetcher = BatchQuoteFetcher(
            fetch_fn=lambda s: _good_quote(s),
            spacing_seconds=0.0, health_tracker=tracker,
        )
        fetcher.fetch_all(["AAPL", "MSFT", "GOOG"])
        report = tracker.status()
        assert report.successes == 3
        assert report.failures == 0

    def test_throttle_spacing_observed(self) -> None:
        """The fetcher must sleep at least once for a >0 spacing across N>1 calls."""
        sleeps: List[float] = []
        fetcher = BatchQuoteFetcher(
            fetch_fn=lambda s: _good_quote(s),
            spacing_seconds=0.05,
            sleep_fn=lambda d: sleeps.append(d),
        )
        fetcher.fetch_all(["A", "B", "C"])
        # First call should not sleep (no prior call); subsequent should.
        assert len(sleeps) >= 1
        assert all(d > 0 for d in sleeps)

    def test_invalid_spacing_rejected(self) -> None:
        with pytest.raises(ValueError):
            BatchQuoteFetcher(fetch_fn=lambda s: _good_quote(s), spacing_seconds=-1)

    def test_malformed_quote_marked_not_ok(self) -> None:
        """A successful fetch returning a NaN-price quote is .ok=False."""
        bad = Quote("AAPL", float("nan"), 0, 0,
                    datetime.now(timezone.utc), False, "test")
        fetcher = BatchQuoteFetcher(fetch_fn=lambda s: bad, spacing_seconds=0.0)
        [r] = fetcher.fetch_all(["AAPL"])
        assert r.quote is bad
        assert r.validation is not None and not r.validation.ok
        assert r.ok is False

    def test_summarise_categories(self) -> None:
        good = _good_quote()
        results = [
            BatchResult(0, "AAPL", good, validate_quote(good), None, None),
            BatchResult(1, "ZZZZ", None, None,
                        MarketDataError("Symbol not found"), ErrorCategory.NOT_FOUND),
            BatchResult(2, "QQQ", None, None,
                        MarketDataError("429"), ErrorCategory.RATE_LIMIT),
        ]
        tally = summarise_categories(results)
        assert tally == {"ok": 1, "not_found": 1, "rate_limit": 1}
