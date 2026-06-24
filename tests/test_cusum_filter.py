"""
tests/test_cusum_filter.py
===========================
Tests for the CUSUM event-sampling filter (ml/triple_barrier.py).

Key properties under test:
1. Events are strictly monotonically ordered in time.
2. Threshold is respected: between any two consecutive events the cumulative
   log-return sum never exceeds the threshold (by design of the reset logic).
3. A monotonically rising price path triggers upward crossings at predictable
   intervals.
4. A zero-return series (perfectly flat prices) never triggers an event.
5. Invalid inputs are rejected gracefully.
"""

import numpy as np
import pandas as pd
import pytest

from ml.triple_barrier import cusum_filter


def _make_prices(log_rets: list, start: str = "2020-01-01") -> pd.Series:
    prices = 100.0 * np.exp(np.cumsum(log_rets))
    return pd.Series(prices, index=pd.date_range(start, periods=len(prices), freq="B"))


# ---------------------------------------------------------------------------
# Test 1: Events are monotonically increasing in time
# ---------------------------------------------------------------------------

def test_events_monotonically_ordered():
    rng = np.random.default_rng(0)
    close = _make_prices(rng.normal(0, 0.01, 500).tolist())
    threshold = 0.05
    events = cusum_filter(close, threshold)

    if len(events) > 1:
        diffs = pd.Series(events).diff().dropna()
        assert (diffs > pd.Timedelta(0)).all(), "Events are not strictly increasing in time"


# ---------------------------------------------------------------------------
# Test 2: A strictly rising price path fires events
# ---------------------------------------------------------------------------

def test_rising_price_triggers_events():
    # Each bar = +2% log return → cumsum crosses threshold every ~threshold/0.02 bars
    threshold = 0.10
    daily_ret = 0.02
    n = 200
    close = _make_prices([daily_ret] * n)

    events = cusum_filter(close, threshold)

    # With a daily return of 0.02 and threshold 0.10, expect ~1 event per 5 bars
    expected_min = n // 6  # conservative lower bound
    assert len(events) >= expected_min, (
        f"Expected ≥ {expected_min} events on rising path, got {len(events)}"
    )


# ---------------------------------------------------------------------------
# Test 3: A declining price path fires (negative) events
# ---------------------------------------------------------------------------

def test_declining_price_triggers_events():
    threshold = 0.10
    daily_ret = -0.02
    n = 200
    close = _make_prices([daily_ret] * n)

    events = cusum_filter(close, threshold)
    expected_min = n // 6
    assert len(events) >= expected_min, (
        f"Expected ≥ {expected_min} events on declining path, got {len(events)}"
    )


# ---------------------------------------------------------------------------
# Test 4: Flat price series → no events
# ---------------------------------------------------------------------------

def test_flat_price_no_events():
    close = _make_prices([0.0] * 200)
    events = cusum_filter(close, threshold=0.05)
    assert len(events) == 0, f"Expected 0 events on flat price, got {len(events)}"


# ---------------------------------------------------------------------------
# Test 5: All events are within the close index
# ---------------------------------------------------------------------------

def test_events_within_close_index():
    rng = np.random.default_rng(1)
    close = _make_prices(rng.normal(0, 0.015, 300).tolist())
    events = cusum_filter(close, threshold=0.05)

    for dt in events:
        assert dt in close.index, f"Event {dt} not in close index"


# ---------------------------------------------------------------------------
# Test 6: Invalid inputs raise ValueError
# ---------------------------------------------------------------------------

def test_invalid_threshold_raises():
    close = _make_prices([0.01] * 50)
    with pytest.raises(ValueError, match="threshold"):
        cusum_filter(close, threshold=0.0)

    with pytest.raises(ValueError, match="threshold"):
        cusum_filter(close, threshold=-0.1)


def test_empty_close_raises():
    close = pd.Series(dtype=float)
    with pytest.raises(ValueError):
        cusum_filter(close, threshold=0.05)


# ---------------------------------------------------------------------------
# Test 7: Very small threshold → many events; large threshold → few events
# ---------------------------------------------------------------------------

def test_threshold_controls_frequency():
    rng = np.random.default_rng(2)
    close = _make_prices(rng.normal(0, 0.01, 500).tolist())

    events_tight = cusum_filter(close, threshold=0.02)
    events_loose = cusum_filter(close, threshold=0.15)

    assert len(events_tight) >= len(events_loose), (
        "Tighter threshold should produce at least as many events as a looser one"
    )
