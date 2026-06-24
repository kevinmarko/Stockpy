"""
tests/test_dual_momentum.py
===========================
Unit, edge-case, and lookahead-leakage tests for DualMomentumAllocator.

Scenarios covered
-----------------
1. US equity wins (SPY >> VEU > BIL)          -> allocate to SPY
2. International equity wins (VEU >> SPY > BIL) -> allocate to VEU
3. Absolute momentum is negative (BIL wins)    -> allocate to BIL (safe)
4. US equity ties safe (boundary condition)    -> allocate to BIL (safe)
5. Missing data for one risky asset            -> graceful fallback
6. Insufficient history                        -> default to safe asset
7. Lookahead-leakage test: perturbing future data must NOT change current decision
"""

import math
from datetime import date
from typing import Dict

import numpy as np
import pandas as pd
import pytest

from allocators.dual_momentum import DualMomentumAllocator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prices(
    n_days: int,
    start_price: float,
    end_price: float,
    ticker: str,
    start_date: str = "2023-01-01",
) -> Dict[str, pd.DataFrame]:
    """Build a deterministic price series as a mock price_data dict entry."""
    prices = np.linspace(start_price, end_price, n_days)
    dates = pd.date_range(start=start_date, periods=n_days, freq="B")
    df = pd.DataFrame(
        {
            "Open": prices * 0.99,
            "High": prices * 1.01,
            "Low": prices * 0.98,
            "Close": prices,
            "Volume": [1_000_000] * n_days,
        },
        index=dates,
    )
    return {ticker: df}


def _build_price_data(
    n_days: int = 280,
    spy_start: float = 100.0,
    spy_end: float = 120.0,
    veu_start: float = 100.0,
    veu_end: float = 105.0,
    bil_start: float = 100.0,
    bil_end: float = 101.5,  # ~1.5% return over the period
    start_date: str = "2023-01-01",
) -> Dict[str, pd.DataFrame]:
    """Convenience builder for a full three-ticker price_data dict."""
    data: Dict[str, pd.DataFrame] = {}
    for ticker, s, e in [
        ("SPY", spy_start, spy_end),
        ("VEU", veu_start, veu_end),
        ("BIL", bil_start, bil_end),
    ]:
        data.update(_make_prices(n_days, s, e, ticker, start_date))
    return data


def _as_of(price_data: Dict[str, pd.DataFrame], offset: int = 0) -> date:
    """Return last date in the price data + offset days (as decision date)."""
    last_idx = max(df.index[-1] for df in price_data.values())
    return (last_idx + pd.Timedelta(days=1 + offset)).date()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def allocator() -> DualMomentumAllocator:
    return DualMomentumAllocator(min_history_days=252)


# ---------------------------------------------------------------------------
# 1. Happy Path – SPY wins
# ---------------------------------------------------------------------------

def test_spy_wins(allocator):
    """SPY has the highest 12M return and beats the safe asset -> SPY allocated."""
    data = _build_price_data(spy_end=130.0, veu_end=108.0, bil_end=102.0)
    as_of = _as_of(data)
    alloc = allocator.decide(as_of, price_data=data)
    assert alloc == {"SPY": 1.0}, f"Expected SPY, got {alloc}"


# ---------------------------------------------------------------------------
# 2. VEU wins (relative momentum)
# ---------------------------------------------------------------------------

def test_veu_wins(allocator):
    """VEU beats SPY in relative momentum and both beat BIL -> VEU allocated."""
    data = _build_price_data(spy_end=110.0, veu_end=130.0, bil_end=102.0)
    as_of = _as_of(data)
    alloc = allocator.decide(as_of, price_data=data)
    assert alloc == {"VEU": 1.0}, f"Expected VEU, got {alloc}"


# ---------------------------------------------------------------------------
# 3. Absolute momentum negative -> safe asset
# ---------------------------------------------------------------------------

def test_absolute_momentum_negative(allocator):
    """Both risky assets underperform the safe asset -> BIL allocated."""
    # SPY and VEU decline; BIL rises slightly
    data = _build_price_data(spy_end=85.0, veu_end=90.0, bil_end=104.0)
    as_of = _as_of(data)
    alloc = allocator.decide(as_of, price_data=data)
    assert alloc == {"BIL": 1.0}, f"Expected BIL, got {alloc}"


# ---------------------------------------------------------------------------
# 4. Boundary: best risky ties safe asset (<=) -> safe
# ---------------------------------------------------------------------------

def test_boundary_tie_goes_to_safe(allocator):
    """When risky return equals safe return, the allocator takes the safe asset."""
    # Construct equal returns: SPY +2%, VEU +0%, BIL +2%
    # Use 280 days so lookback window (252) is satisfiable
    n = 280
    dates = pd.date_range("2023-01-01", periods=n, freq="B")

    def _df(start, end):
        prices = np.linspace(start, end, n)
        return pd.DataFrame(
            {"Open": prices, "High": prices, "Low": prices,
             "Close": prices, "Volume": [1e6] * n},
            index=dates,
        )

    data = {
        "SPY": _df(100, 102),
        "VEU": _df(100, 100),
        "BIL": _df(100, 102),  # exactly tied with SPY
    }
    as_of = (dates[-1] + pd.Timedelta(days=1)).date()
    alloc = allocator.decide(as_of, price_data=data)
    # best_risky_ret == safe_ret -> condition is <= -> BIL
    assert alloc == {"BIL": 1.0}, f"Expected BIL on tie, got {alloc}"


# ---------------------------------------------------------------------------
# 5. Missing data for one risky asset -> graceful degradation
# ---------------------------------------------------------------------------

def test_missing_risky_asset():
    """Allocator should survive when one risky asset has no data."""
    alloc_obj = DualMomentumAllocator(
        risky_assets=["SPY", "VEU"],
        safe_asset="BIL",
        min_history_days=252,
    )
    # Only provide SPY and BIL (no VEU)
    n = 280
    dates = pd.date_range("2023-01-01", periods=n, freq="B")

    def _df(s, e):
        prices = np.linspace(s, e, n)
        return pd.DataFrame(
            {"Open": prices, "High": prices, "Low": prices,
             "Close": prices, "Volume": [1e6] * n},
            index=dates,
        )

    data = {"SPY": _df(100, 130), "BIL": _df(100, 102)}
    as_of = (dates[-1] + pd.Timedelta(days=1)).date()
    alloc = alloc_obj.decide(as_of, price_data=data)
    # Should still make a valid decision (SPY or BIL), weights sum to 1.0
    assert math.isclose(sum(alloc.values()), 1.0, abs_tol=1e-9)
    assert all(w >= 0.0 for w in alloc.values())


# ---------------------------------------------------------------------------
# 6. Insufficient history -> default to safe
# ---------------------------------------------------------------------------

def test_insufficient_history(allocator):
    """Allocator defaults to safe when history is shorter than min_history_days."""
    # Only 100 days – well below the 252 minimum
    data = _build_price_data(n_days=100, spy_end=130.0, veu_end=105.0, bil_end=102.0)
    as_of = _as_of(data)
    alloc = allocator.decide(as_of, price_data=data)
    assert alloc == {"BIL": 1.0}, f"Expected BIL due to insufficient history, got {alloc}"


# ---------------------------------------------------------------------------
# 7. Lookahead-leakage test
# ---------------------------------------------------------------------------

def test_no_lookahead_leakage():
    """
    Perturbing prices AFTER the as_of_date must NOT change the allocation.

    Method: take a price history that produces a known allocation for a given
    as_of_date.  Then mutate all prices AFTER that date by 10x and re-run.
    The allocation must be identical, proving no future data leaked into the
    decision.
    """
    alloc_obj = DualMomentumAllocator(min_history_days=252)

    n = 280
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    as_of = (dates[260] + pd.Timedelta(days=1)).date()  # Decision date mid-series

    def _df(s, e):
        prices = np.linspace(s, e, n)
        return pd.DataFrame(
            {"Open": prices, "High": prices, "Low": prices,
             "Close": prices, "Volume": [1e6] * n},
            index=dates,
        )

    # Original data: SPY strong uptrend
    base_data = {
        "SPY": _df(100, 130),
        "VEU": _df(100, 106),
        "BIL": _df(100, 102),
    }
    alloc_base = alloc_obj.decide(as_of, price_data=base_data)

    # Perturbed data: multiply all prices AFTER as_of_date by 10 (extreme change)
    perturbed_data: Dict[str, pd.DataFrame] = {}
    for ticker, df in base_data.items():
        df_p = df.copy()
        future_mask = df_p.index > pd.Timestamp(as_of)
        df_p.loc[future_mask, "Close"] *= 10
        perturbed_data[ticker] = df_p

    alloc_perturbed = alloc_obj.decide(as_of, price_data=perturbed_data)

    assert alloc_base == alloc_perturbed, (
        f"Lookahead detected! Base={alloc_base}, Perturbed={alloc_perturbed}"
    )


# ---------------------------------------------------------------------------
# 8. Weights always sum to 1.0 and are non-negative
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spy_end,veu_end,bil_end", [
    (130.0, 108.0, 102.0),   # SPY wins
    (110.0, 130.0, 102.0),   # VEU wins
    (85.0,  90.0,  104.0),   # BIL wins
])
def test_weights_sum_to_one(spy_end, veu_end, bil_end):
    """Allocation weights must always sum to 1.0 and be non-negative."""
    alloc_obj = DualMomentumAllocator(min_history_days=252)
    data = _build_price_data(spy_end=spy_end, veu_end=veu_end, bil_end=bil_end)
    as_of = _as_of(data)
    alloc = alloc_obj.decide(as_of, price_data=data)
    assert math.isclose(sum(alloc.values()), 1.0, abs_tol=1e-9)
    assert all(w >= 0.0 for w in alloc.values())
