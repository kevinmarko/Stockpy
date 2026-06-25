"""
tests/test_triple_barrier_labels.py
=====================================
Verifies triple-barrier labels using hand-crafted price paths with known outcomes.

Each test constructs a price series where EXACTLY ONE barrier will be hit first,
then verifies that apply_triple_barrier returns the expected label.
"""

import numpy as np
import pandas as pd
import pytest

from ml.triple_barrier import apply_triple_barrier, get_volatility


def _flat_then_spike(entry: float, spike_factor: float, n_flat: int = 10, n_total: int = 50) -> pd.Series:
    """Flat prices then a sudden spike at bar n_flat+1."""
    prices = [entry] * n_total
    prices[n_flat] = entry * spike_factor
    dates = pd.date_range("2020-01-01", periods=n_total, freq="B")
    return pd.Series(prices, index=dates)


# ---------------------------------------------------------------------------
# Test 1: Upper barrier hit — price spikes above entry + 2*sigma
# ---------------------------------------------------------------------------

def test_upper_barrier_label():
    """A large upward price spike before the vertical barrier → label = +1.

    We use a warmup period to establish a non-zero sigma, then set bars
    between the event and the spike to exactly the entry price (so neither
    barrier is touched before the spike fires).
    """
    # Build a short warmup series to get a valid sigma estimate
    n_warmup = 90
    rng = np.random.default_rng(0)
    log_rets_warmup = rng.normal(0.0, 0.01, n_warmup)
    prices_warmup = 100.0 * np.exp(np.cumsum(log_rets_warmup))

    # After warmup: 20 flat bars, then a spike at bar 5
    n_future = 20
    entry = float(prices_warmup[-1])

    dates = pd.date_range("2020-01-01", periods=n_warmup + n_future, freq="B")
    prices = list(prices_warmup) + [entry] * n_future
    close = pd.Series(prices, index=dates)

    t0 = close.index[n_warmup]  # event: first flat bar

    # Compute sigma at t0
    sigma = float(get_volatility(close.loc[close.index <= t0]).iloc[-1])
    assert sigma > 0, "sigma must be > 0 after warmup"

    # Inject a spike far above the upper barrier at bar n_warmup + 5
    spike_price = entry * (1.0 + 10.0 * sigma)
    close.iloc[n_warmup + 5] = spike_price

    events = pd.DatetimeIndex([t0])
    tb = apply_triple_barrier(events, close, pt_sl_multiples=(2.0, 1.0), vertical_barrier_days=10)

    assert t0 in tb.index, "Event not in output"
    row = tb.loc[t0]
    assert row["label"] == 1, f"Expected label=+1 (upper), got {row['label']}"
    assert row["barrier_hit"] == "upper", f"Expected barrier_hit='upper', got {row['barrier_hit']}"


# ---------------------------------------------------------------------------
# Test 2: Lower barrier hit — price crashes below entry - 1*sigma
# ---------------------------------------------------------------------------

def test_lower_barrier_label():
    """A large downward price drop before the vertical barrier → label = -1."""
    n_warmup = 90
    rng = np.random.default_rng(1)
    log_rets_warmup = rng.normal(0.0, 0.01, n_warmup)
    prices_warmup = 100.0 * np.exp(np.cumsum(log_rets_warmup))

    n_future = 20
    entry = float(prices_warmup[-1])
    dates = pd.date_range("2020-01-01", periods=n_warmup + n_future, freq="B")
    prices = list(prices_warmup) + [entry] * n_future
    close = pd.Series(prices, index=dates)

    t0 = close.index[n_warmup]
    sigma = float(get_volatility(close.loc[close.index <= t0]).iloc[-1])
    assert sigma > 0

    # Inject a crash far below the lower barrier at bar 5
    crash_price = entry * (1.0 - 10.0 * sigma)
    close.iloc[n_warmup + 5] = crash_price

    events = pd.DatetimeIndex([t0])
    tb = apply_triple_barrier(events, close, pt_sl_multiples=(2.0, 1.0), vertical_barrier_days=10)

    assert t0 in tb.index
    row = tb.loc[t0]
    assert row["label"] == -1, f"Expected label=-1 (lower), got {row['label']}"
    assert row["barrier_hit"] == "lower", f"Expected 'lower', got {row['barrier_hit']}"


# ---------------------------------------------------------------------------
# Test 3: Vertical barrier (timeout) — price stays within barriers
# ---------------------------------------------------------------------------

def test_vertical_barrier_label():
    """Price stays flat (within both barriers) → label = 0 (vertical timeout)."""
    n = 60
    # Completely flat price: zero returns → zero vol; use manual very small sigma
    prices = [100.0] * n
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    close = pd.Series(prices, index=dates)

    # Start vol computation after a warmup that has tiny returns
    rng = np.random.default_rng(42)
    warmup = pd.Series(
        100.0 * np.exp(np.cumsum(rng.normal(0, 0.001, 50))),
        index=pd.date_range("2019-01-01", periods=50, freq="B"),
    )
    # Build a series: warmup (50 bars with real vol) + flat 60 bars
    full = pd.concat([warmup, close])
    full.index = pd.date_range("2019-01-01", periods=len(full), freq="B")
    t0 = full.index[60]  # first flat bar

    events = pd.DatetimeIndex([t0])
    tb = apply_triple_barrier(events, full, pt_sl_multiples=(2.0, 1.0), vertical_barrier_days=5)

    assert t0 in tb.index
    row = tb.loc[t0]
    assert row["label"] == 0, f"Expected label=0 (vertical), got {row['label']}"
    assert row["barrier_hit"] == "vertical", f"Expected 'vertical', got {row['barrier_hit']}"


# ---------------------------------------------------------------------------
# Test 4: Upper hit before lower → label = +1 (first-touch wins)
# ---------------------------------------------------------------------------

def test_first_touch_wins_upper():
    """When both upper and lower bars are hit within the window, upper first → +1."""
    n_warmup = 90
    rng = np.random.default_rng(99)
    prices_warmup = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n_warmup)))

    n_future = 20
    entry = float(prices_warmup[-1])
    dates = pd.date_range("2020-01-01", periods=n_warmup + n_future, freq="B")
    prices = list(prices_warmup) + [entry] * n_future
    close = pd.Series(prices, index=dates)

    t0 = close.index[n_warmup]
    sigma = float(get_volatility(close.loc[close.index <= t0]).iloc[-1])

    # Upper at bar +2, lower at bar +4 (both far out)
    close.iloc[n_warmup + 2] = entry * (1.0 + 10.0 * sigma)  # upper hit first
    close.iloc[n_warmup + 4] = entry * (1.0 - 10.0 * sigma)  # lower hit second

    events = pd.DatetimeIndex([t0])
    tb = apply_triple_barrier(events, close, pt_sl_multiples=(2.0, 1.0), vertical_barrier_days=15)

    row = tb.loc[t0]
    assert row["label"] == 1, "Upper was hit first, expected label=+1"


# ---------------------------------------------------------------------------
# Test 5: Label schema and types
# ---------------------------------------------------------------------------

def test_output_schema():
    """Output DataFrame has expected columns and correct dtypes."""
    n = 120
    rng = np.random.default_rng(7)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    close = pd.Series(prices, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    events = pd.DatetimeIndex([close.index[50], close.index[80]])

    tb = apply_triple_barrier(events, close)

    expected_cols = {"t1", "barrier_hit", "label", "entry", "upper_level", "lower_level"}
    assert expected_cols.issubset(set(tb.columns)), f"Missing columns: {expected_cols - set(tb.columns)}"

    for lbl in tb["label"]:
        assert lbl in (-1, 0, 1), f"Unexpected label value: {lbl}"
    for bh in tb["barrier_hit"]:
        assert bh in ("upper", "lower", "vertical"), f"Unexpected barrier_hit: {bh}"
