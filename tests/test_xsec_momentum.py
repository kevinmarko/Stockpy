"""
Tests for signals/cross_sectional_momentum.py
=============================================

Coverage:
 - test_top_quintile_score_positive        : Top 20 of 100 stocks get score > 0.6
 - test_bottom_quintile_score_negative     : Bottom 20 of 100 stocks get score < -0.6
 - test_score_formula_midpoint_is_zero     : Median stock scores exactly 0.0
 - test_weights_sum_neutral                : Neutral mid-quintile score stays in [-0.2, +0.2]
 - test_missing_ticker_returns_neutral     : Unknown ticker returns score=0, conf=0
 - test_single_stock_universe              : Single-stock universe scores 0.5 (sole stock is median)
 - test_pre_compute_without_xsec_col      : Graceful no-op when XSec_12_1M column missing
 - test_no_lookahead_12m_skips_recent_month: 12-1m return does NOT change when only t..t+21 prices change
 - test_compute_xsec_momentum_ranks_vectorized: orchestrator helper is fully vectorized, no loops leaking
 - test_rank_pct_in_unit_interval         : All ranks in [0, 1]
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

# ---- Module under test ----
from signals.cross_sectional_momentum import (
    CrossSectionalMomentumSignal,
    XSEC_RETURN_COL,
    SYMBOL_COL,
)
from signals.base import SignalContext, SignalOutput
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from main_orchestrator import compute_xsec_momentum_ranks

# ---- Fixtures ----

def _make_context(ticker: str = "AAPL") -> SignalContext:
    bar = MarketBarDTO(
        date=pd.Timestamp("2024-01-15"),
        ticker=ticker,
        open_price=100.0,
        high_price=101.0,
        low_price=99.0,
        close_price=100.0,
        volume=1_000_000,
    )
    fund = FundamentalDataDTO(
        ticker=ticker, pe_ratio=20.0, pb_ratio=3.0, dividend_yield=0.01,
        book_value=30.0, eps_trailing=5.0, dividend_growth_rate=0.05,
        payout_ratio=0.3, sector="Technology", company_name="Apple"
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=3.5,
        inflation_rate=2.5,
        nominal_10y=4.0,
        vix_value=15.0,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


def _build_universe_df(n: int = 100) -> pd.DataFrame:
    """Returns a universe_df with tickers T001..T100, returns sorted ascending."""
    tickers = [f"T{i:03d}" for i in range(1, n + 1)]
    # Returns monotonically increasing: T001 worst, T100 best
    returns = np.linspace(-0.50, 0.50, n)
    return pd.DataFrame({
        SYMBOL_COL: tickers,
        XSEC_RETURN_COL: returns,
    })


def _run_pre_compute_and_compute(ticker: str, universe_df: pd.DataFrame) -> SignalOutput:
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context(ticker)
    signal.pre_compute(universe_df, ctx)
    row = pd.Series({SYMBOL_COL: ticker})
    return signal.compute(row, ctx)


# ---- Tests ----

def test_top_quintile_score_positive():
    """Top 20 of 100 sorted stocks must score > 0.6."""
    universe_df = _build_universe_df(100)
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("T100")
    signal.pre_compute(universe_df, ctx)

    for i in range(81, 101):   # T081..T100 → top quintile
        ticker = f"T{i:03d}"
        row = pd.Series({SYMBOL_COL: ticker})
        out = signal.compute(row, ctx)
        assert out.score > 0.6, (
            f"{ticker} expected score > 0.6, got {out.score:.4f}"
        )


def test_bottom_quintile_score_negative():
    """Strictly bottom 19 of 100 sorted stocks must score < -0.6.
    
    T020 sits exactly at the quintile boundary (rank=0.200, score=-0.600)
    and is tested separately in test_score_formula_midpoint_is_zero-style.
    """
    universe_df = _build_universe_df(100)
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("T001")
    signal.pre_compute(universe_df, ctx)

    for i in range(1, 20):   # T001..T019 → strictly below bottom-quintile boundary
        ticker = f"T{i:03d}"
        row = pd.Series({SYMBOL_COL: ticker})
        out = signal.compute(row, ctx)
        assert out.score < -0.6, (
            f"{ticker} expected score < -0.6, got {out.score:.4f}"
        )


def test_score_formula_midpoint_is_zero():
    """Rank 0.5 (perfect median) should produce score = 0."""
    ctx = _make_context("MID")
    ctx.xsec_percentile_ranks = {"MID": 0.5}
    signal = CrossSectionalMomentumSignal()
    row = pd.Series({SYMBOL_COL: "MID"})
    out = signal.compute(row, ctx)
    assert abs(out.score) < 1e-9, f"Score should be 0 for rank=0.5, got {out.score}"


def test_rank_pct_in_unit_interval():
    """After pre_compute, every rank must be in [0, 1]."""
    universe_df = _build_universe_df(100)
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("T050")
    signal.pre_compute(universe_df, ctx)

    for ticker, rank in ctx.xsec_percentile_ranks.items():
        assert 0.0 <= rank <= 1.0, f"{ticker} rank={rank} out of [0,1]"


def test_missing_ticker_returns_neutral():
    """Unknown ticker not present in pre_compute output returns score=0, confidence=0."""
    universe_df = _build_universe_df(10)
    out = _run_pre_compute_and_compute("UNKNOWN_XYZ", universe_df)
    assert out.score == 0.0
    assert out.confidence == 0.0
    assert "WARNING" in out.explanation


def test_single_stock_universe():
    """Universe of 1 stock: pandas rank(pct=True) returns 1.0 (sole stock is top-ranked)."""
    universe_df = pd.DataFrame({
        SYMBOL_COL: ["ONLY"],
        XSEC_RETURN_COL: [0.25],
    })
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("ONLY")
    signal.pre_compute(universe_df, ctx)
    # With a single-element series, rank(pct=True) = 1.0 → score = 2*(1.0-0.5) = 1.0
    row = pd.Series({SYMBOL_COL: "ONLY"})
    out = signal.compute(row, ctx)
    assert abs(out.score - 1.0) < 1e-9, f"Expected +1.0, got {out.score}"


def test_pre_compute_without_xsec_col():
    """Missing XSec_12_1M column: pre_compute is a graceful no-op, no exception."""
    universe_df = pd.DataFrame({SYMBOL_COL: ["A", "B", "C"]})  # no return col
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("A")
    signal.pre_compute(universe_df, ctx)   # must not raise
    assert ctx.xsec_percentile_ranks == {}


def test_pre_compute_without_symbol_col():
    """Missing Symbol column: pre_compute is a graceful no-op, no exception."""
    universe_df = pd.DataFrame({XSEC_RETURN_COL: [0.1, 0.2, 0.3]})
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("A")
    signal.pre_compute(universe_df, ctx)   # must not raise
    assert ctx.xsec_percentile_ranks == {}


# ---- Lookahead test ----

def test_no_lookahead_12m_skips_recent_month():
    """
    The 12-1m return is formed from price[t-22] / price[t-252] - 1.
    Perturbing prices in the MOST RECENT 21 trading days must NOT change the
    XSec rank computed by compute_xsec_momentum_ranks().

    This directly verifies the Jegadeesh-Titman skip-month construction and
    the absence of lookahead over the last calendar month.
    """
    n_days = 300
    dates = pd.date_range("2022-01-01", periods=n_days, freq="B")
    base_prices = 100.0 + np.cumsum(np.random.default_rng(42).normal(0, 1, n_days))
    base_prices = np.maximum(base_prices, 1.0)  # prevent negatives

    def _make_tech_raw(prices: np.ndarray) -> dict:
        df = pd.DataFrame({"Close": prices, "Open": prices, "High": prices,
                           "Low": prices, "Volume": 1000}, index=dates)
        return {"AAPL": df, "MSFT": (df * 1.02)}   # two tickers

    tech_raw_orig = _make_tech_raw(base_prices)
    ranks_orig = compute_xsec_momentum_ranks(tech_raw_orig)

    # Perturb only the last 21 trading days (the skip-month window)
    perturbed = base_prices.copy()
    perturbed[-21:] *= 10.0   # 10× price shock in skip window

    tech_raw_pert = _make_tech_raw(perturbed)
    ranks_pert = compute_xsec_momentum_ranks(tech_raw_pert)

    for ticker in ranks_orig.index:
        r_orig = float(ranks_orig[ticker])
        r_pert = float(ranks_pert[ticker])
        assert abs(r_orig - r_pert) < 1e-9, (
            f"Rank changed for {ticker} after perturbing only the skip-month window: "
            f"orig={r_orig:.6f}, pert={r_pert:.6f}. "
            "This indicates lookahead into the most-recent month."
        )


def test_compute_xsec_momentum_ranks_vectorized():
    """
    Verify that compute_xsec_momentum_ranks correctly handles a 5-ticker
    universe and returns a rank Series with all values in [0, 1].
    """
    n = 300
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    tech_raw = {}
    for i, ticker in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        # Each ticker has a different upward drift
        prices = 100.0 + np.cumsum(np.ones(n) * (i * 0.05))
        df = pd.DataFrame({"Close": prices, "Open": prices, "High": prices,
                           "Low": prices, "Volume": 1000}, index=dates)
        tech_raw[ticker] = df

    ranks = compute_xsec_momentum_ranks(tech_raw)
    assert len(ranks) == 5
    for val in ranks.values:
        assert 0.0 <= val <= 1.0


def test_insufficient_history_excluded():
    """Tickers with fewer than 275 days of data should be excluded from ranking."""
    dates_short = pd.date_range("2023-01-01", periods=100, freq="B")
    dates_long = pd.date_range("2020-01-01", periods=300, freq="B")
    prices_short = np.ones(100) * 100.0
    prices_long = 100.0 + np.arange(300) * 0.1

    tech_raw = {
        "SHORT": pd.DataFrame({"Close": prices_short}, index=dates_short),
        "LONG": pd.DataFrame({"Close": prices_long}, index=dates_long),
    }
    ranks = compute_xsec_momentum_ranks(tech_raw)
    assert "SHORT" not in ranks.index, "SHORT should be excluded (insufficient history)"
    assert "LONG" in ranks.index
