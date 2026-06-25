"""
tests/test_triple_barrier_lookahead.py
=======================================
Verifies that the triple-barrier labeling is strictly point-in-time:
sigma at event time t uses ONLY data with timestamp ≤ t.

Two no-lookahead properties are tested:
1. Perturbation invariance: changing prices AFTER event t does not alter the
   barriers computed for event at t.
2. Prefix consistency: computing sigma on close[:t] gives the same result as
   computing it on the full series and indexing at t.
"""

import numpy as np
import pandas as pd
import pytest

from ml.triple_barrier import get_volatility, apply_triple_barrier, cusum_filter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_close(n: int = 300, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(0.0005, 0.01, size=n)
    prices = 100.0 * np.exp(np.cumsum(log_rets))
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.Series(prices, index=dates, name="Close")


# ---------------------------------------------------------------------------
# Test 1: sigma at event t == sigma computed on prefix close[:t]
# ---------------------------------------------------------------------------

def test_sigma_equals_prefix_sigma():
    """Sigma used in apply_triple_barrier equals get_volatility on the exact prefix."""
    close = _make_close(300)
    events = pd.DatetimeIndex([close.index[100], close.index[150]])

    # Use apply_triple_barrier to get the barrier levels
    tb = apply_triple_barrier(events, close, pt_sl_multiples=(2.0, 1.0))

    for t0 in events:
        if t0 not in tb.index:
            continue
        entry = float(close[t0])
        # Compute sigma using only the prefix up to and including t0
        prefix_sigma = float(get_volatility(close.loc[close.index <= t0]).iloc[-1])

        # Reconstruct barriers from the prefix sigma
        expected_upper = entry * (1.0 + 2.0 * prefix_sigma)
        expected_lower = entry * (1.0 - 1.0 * prefix_sigma)

        actual_upper = float(tb.loc[t0, "upper_level"])
        actual_lower = float(tb.loc[t0, "lower_level"])

        assert abs(actual_upper - expected_upper) < 1e-10, (
            f"Upper barrier mismatch at {t0}: got {actual_upper}, expected {expected_upper}"
        )
        assert abs(actual_lower - expected_lower) < 1e-10, (
            f"Lower barrier mismatch at {t0}: got {actual_lower}, expected {expected_lower}"
        )


# ---------------------------------------------------------------------------
# Test 2: Perturbation of future prices does not change barriers
# ---------------------------------------------------------------------------

def test_barriers_invariant_to_future_perturbation():
    """Perturbing prices strictly AFTER the event date must not change barriers."""
    close = _make_close(300)
    event_t = close.index[100]
    events = pd.DatetimeIndex([event_t])

    # Reference barriers
    tb_ref = apply_triple_barrier(events, close.copy(), pt_sl_multiples=(2.0, 1.0))

    # Perturb all prices AFTER event_t to extreme values
    close_perturbed = close.copy()
    close_perturbed.loc[close_perturbed.index > event_t] *= 10_000.0

    tb_perturbed = apply_triple_barrier(
        events, close_perturbed, pt_sl_multiples=(2.0, 1.0)
    )

    # Barrier levels (which depend on sigma and entry price at t0) must be identical
    assert event_t in tb_ref.index, "Event not in reference output"
    assert event_t in tb_perturbed.index, "Event not in perturbed output"

    assert abs(tb_ref.loc[event_t, "upper_level"] - tb_perturbed.loc[event_t, "upper_level"]) < 1e-10, (
        "Upper barrier changed after perturbing future prices"
    )
    assert abs(tb_ref.loc[event_t, "lower_level"] - tb_perturbed.loc[event_t, "lower_level"]) < 1e-10, (
        "Lower barrier changed after perturbing future prices"
    )
    assert tb_ref.loc[event_t, "entry"] == tb_perturbed.loc[event_t, "entry"], (
        "Entry price changed after perturbing future prices"
    )


# ---------------------------------------------------------------------------
# Test 3: get_volatility itself is causal (no lookahead)
# ---------------------------------------------------------------------------

def test_get_volatility_causal():
    """Vol at index i must equal vol computed on close[:i+1]."""
    close = _make_close(100)
    vol_full = get_volatility(close)

    # Check a few interior dates
    for i in [20, 50, 80]:
        dt = close.index[i]
        vol_prefix = get_volatility(close.iloc[: i + 1]).iloc[-1]
        assert abs(float(vol_full.iloc[i]) - float(vol_prefix)) < 1e-12, (
            f"Lookahead detected at index {i}: full={vol_full.iloc[i]:.8f} prefix={vol_prefix:.8f}"
        )


# ---------------------------------------------------------------------------
# Test 4: Empty / edge cases
# ---------------------------------------------------------------------------

def test_empty_events():
    close = _make_close(100)
    result = apply_triple_barrier(pd.DatetimeIndex([]), close)
    assert result.empty


def test_empty_close():
    close = pd.Series(dtype=float)
    events = pd.DatetimeIndex([pd.Timestamp("2020-01-02")])
    result = apply_triple_barrier(events, close)
    assert result.empty


def test_event_not_in_close_skipped():
    close = _make_close(100)
    bad_event = pd.DatetimeIndex([pd.Timestamp("1900-01-01")])
    result = apply_triple_barrier(bad_event, close)
    assert result.empty
