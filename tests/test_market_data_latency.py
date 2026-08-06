"""tests/test_market_data_latency.py — coverage for `market_data_latency.py`.

Exercises the module without any project imports (it has none itself) or
Streamlit. Three groups mirror its three surfaces:

1.  ``LatencySampleRing`` — record/read round-trip, bounded eviction, clear,
    and the ``get_ring()`` process-wide singleton identity.
2.  ``record_quote_latency`` — the honest-instrumentation contract: real
    latency is computed for a tz-aware timestamp, a naive timestamp is
    handled without raising, a missing timestamp records nothing (never a
    fabricated latency), and ``is_stale``/``symbol``/``source`` pass through
    onto the recorded sample.
3.  ``summarize_latency`` — empty-input shape, single-sample shape, and the
    pooled-percentile-vs-per-symbol-p95 logic (``worst_symbol``) across
    multiple symbols, including a symbol with only one sample.

``record_quote_latency`` writes to the module-level singleton ring
(``get_ring()``), which is process-global state — every test that touches it
clears it first via an autouse fixture so tests never leak samples into each
other.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market_data_latency import (
    LatencySample,
    LatencySampleRing,
    get_ring,
    record_quote_latency,
    summarize_latency,
)


@pytest.fixture(autouse=True)
def _clear_singleton_ring():
    """The ring backing ``record_quote_latency`` is a module-level singleton
    (``market_data_latency.get_ring()``) — clear it before AND after every
    test so no test observes samples recorded by another."""
    get_ring().clear()
    yield
    get_ring().clear()


def _sample(
    symbol: str = "AAPL",
    source: str = "test",
    latency_seconds: float = 1.0,
    is_stale: bool = False,
    quote_timestamp: datetime | None = None,
) -> LatencySample:
    qts = quote_timestamp or datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
    return LatencySample(
        symbol=symbol,
        source=source,
        quote_timestamp=qts,
        ingested_at=qts + timedelta(seconds=latency_seconds),
        latency_seconds=latency_seconds,
        is_stale=is_stale,
    )


# ===========================================================================
# 1. LatencySampleRing
# ===========================================================================

class TestLatencySampleRing:
    def test_record_then_samples_returns_what_was_recorded(self) -> None:
        ring = LatencySampleRing(maxlen=10)
        s = _sample("AAPL")
        ring.record(s)
        assert ring.samples() == [s]

    def test_bounded_ring_evicts_oldest_first(self) -> None:
        ring = LatencySampleRing(maxlen=3)
        for i in range(8):  # maxlen + 5
            ring.record(_sample(f"S{i}"))
        samples = ring.samples()
        assert len(samples) == 3
        # Oldest (S0..S4) evicted; only the last 3 inserted survive, in
        # insertion order (newest last).
        assert [s.symbol for s in samples] == ["S5", "S6", "S7"]

    def test_clear_empties(self) -> None:
        ring = LatencySampleRing(maxlen=5)
        ring.record(_sample())
        assert len(ring.samples()) == 1
        ring.clear()
        assert ring.samples() == []

    def test_get_ring_returns_same_singleton_across_calls(self) -> None:
        assert get_ring() is get_ring()


# ===========================================================================
# 2. record_quote_latency
# ===========================================================================

class TestRecordQuoteLatency:
    def test_real_fetch_records_one_sample_with_small_positive_latency(self) -> None:
        quote_ts = datetime.now(timezone.utc) - timedelta(seconds=2)
        record_quote_latency("AAPL", "alpaca", quote_ts, is_stale=False)

        samples = get_ring().samples()
        assert len(samples) == 1
        sample = samples[0]
        assert sample.latency_seconds >= 0.0
        assert sample.latency_seconds < 10.0  # ~2s ago, generous flake margin
        assert sample.symbol == "AAPL"
        assert sample.source == "alpaca"
        assert sample.is_stale is False

    def test_none_timestamp_records_nothing(self) -> None:
        record_quote_latency("AAPL", "alpaca", None, is_stale=False)
        assert get_ring().samples() == []

    def test_naive_timestamp_handled_without_raising(self) -> None:
        # No tzinfo -- built from a real UTC "now" with tzinfo stripped,
        # avoiding the deprecated datetime.utcnow().
        naive_ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        record_quote_latency("MSFT", "yfinance", naive_ts, is_stale=False)

        samples = get_ring().samples()
        assert len(samples) == 1
        assert samples[0].latency_seconds >= 0.0
        assert samples[0].quote_timestamp.tzinfo is not None

    @pytest.mark.parametrize("raw_is_stale, expected", [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("", False),
    ])
    def test_is_stale_coerced_to_bool(self, raw_is_stale, expected: bool) -> None:
        quote_ts = datetime.now(timezone.utc) - timedelta(seconds=1)
        record_quote_latency("GOOG", "finnhub", quote_ts, is_stale=raw_is_stale)

        [sample] = get_ring().samples()
        assert sample.is_stale is expected
        assert isinstance(sample.is_stale, bool)

    def test_symbol_and_source_pass_through(self) -> None:
        quote_ts = datetime.now(timezone.utc) - timedelta(seconds=1)
        record_quote_latency("TSLA", "composite_provider", quote_ts, is_stale=True)

        [sample] = get_ring().samples()
        assert sample.symbol == "TSLA"
        assert sample.source == "composite_provider"


# ===========================================================================
# 3. summarize_latency
# ===========================================================================

class TestSummarizeLatency:
    def test_empty_list_returns_honest_none_shape(self) -> None:
        summary = summarize_latency([])
        assert summary == {
            "count": 0,
            "p50": None,
            "p95": None,
            "worst_symbol": None,
            "worst_p95": None,
        }

    def test_single_sample_p50_equals_p95_equals_own_latency(self) -> None:
        s = _sample("AAPL", latency_seconds=3.5)
        summary = summarize_latency([s])
        assert summary["count"] == 1
        assert summary["p50"] == pytest.approx(3.5, abs=1e-5)
        assert summary["p95"] == pytest.approx(3.5, abs=1e-5)
        assert summary["worst_symbol"] == "AAPL"
        assert summary["worst_p95"] == pytest.approx(3.5, abs=1e-5)

    def test_pooled_percentiles_are_computed_across_all_samples_not_per_symbol(
        self,
    ) -> None:
        """AAPL has 5 low-latency samples (1..5s), MSFT has 5 high-latency
        samples (50..90s). Pooled p50/p95 over all 10 values must come from
        nearest-rank on the SORTED POOL — not a per-symbol average and not a
        naive mean of all values, which would give materially different
        numbers here (mean == 36.5, nowhere near the true pooled median)."""
        samples = [_sample("AAPL", latency_seconds=v) for v in (1.0, 2.0, 3.0, 4.0, 5.0)]
        samples += [_sample("MSFT", latency_seconds=v) for v in (50.0, 60.0, 70.0, 80.0, 90.0)]

        summary = summarize_latency(samples)

        naive_mean = sum(s.latency_seconds for s in samples) / len(samples)
        assert naive_mean == pytest.approx(36.5, abs=1e-5)

        assert summary["count"] == 10
        # Nearest-rank pooled p50 over the 10 sorted values lands on 5.0 --
        # far from the naive mean, proving the real percentile logic ran.
        assert summary["p50"] == pytest.approx(5.0, abs=1e-5)
        assert summary["p95"] == pytest.approx(90.0, abs=1e-5)
        # MSFT's own p95 (90.0) dominates AAPL's own p95 (5.0).
        assert summary["worst_symbol"] == "MSFT"
        assert summary["worst_p95"] == pytest.approx(90.0, abs=1e-5)

    def test_worst_symbol_comparison_handles_a_single_sample_group(self) -> None:
        """A symbol with exactly one sample (LOWVOL is a 3-sample group here,
        SPIKE only one) must not crash the per-symbol p95 comparison, and a
        single-sample group's own value (which IS its p95, per the n==1
        branch) must be able to win the worst-symbol comparison outright."""
        samples = [_sample("LOWVOL", latency_seconds=v) for v in (1.0, 2.0, 3.0)]
        samples += [_sample("SPIKE", latency_seconds=999.0)]

        summary = summarize_latency(samples)

        assert summary["count"] == 4
        assert summary["p50"] == pytest.approx(3.0, abs=1e-5)
        assert summary["p95"] == pytest.approx(999.0, abs=1e-5)
        assert summary["worst_symbol"] == "SPIKE"
        assert summary["worst_p95"] == pytest.approx(999.0, abs=1e-5)
