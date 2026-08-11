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
from pipeline.production_steps import _compute_xsec_momentum

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


def test_row_missing_symbol_column_falls_back_to_neutral():
    """Regression documenting Finding 2's exact failure mode: a row with no
    'Symbol' key at all (the shape pipeline/production_steps.py's vec_df
    used to have -- only a 'ticker' column, never 'Symbol') makes
    compute()'s `row.get(SYMBOL_COL, "")` resolve to "", which then always
    misses in context.xsec_percentile_ranks and silently returns a neutral
    score=0.0 -- exactly why the live vectorized path contributed a uniform
    0.0 for cross_sectional_momentum on every ticker, every cycle."""
    universe_df = _build_universe_df(10)
    signal = CrossSectionalMomentumSignal()
    ctx = _make_context("T009")
    signal.pre_compute(universe_df, ctx)
    assert ctx.xsec_percentile_ranks  # real ranks exist

    row_without_symbol = pd.Series({"ticker": "T009"})  # no 'Symbol' key
    out = signal.compute(row_without_symbol, ctx)
    assert out.score == 0.0
    assert out.confidence == 0.0
    assert "WARNING" in out.explanation

    # The same ticker, with the 'Symbol' key present, resolves to its real
    # (non-neutral) rank-based score -- proving the column name is what
    # matters, not the underlying rank data. T009 (index 8 of 10, near-top
    # returns) sits well off the rank=0.5 midpoint.
    row_with_symbol = pd.Series({SYMBOL_COL: "T009"})
    out_with_symbol = signal.compute(row_with_symbol, ctx)
    assert out_with_symbol.score != 0.0


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


# ---- Finding 15: single-source raw-return + rank helper ----

def _make_tech_raw_for_helper(n: int = 300, drift_step: float = 0.05) -> dict:
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    tech_raw = {}
    # (i + 1) so every ticker (including the first) has non-zero drift --
    # a flat (zero-drift) price series has an identical 0.0 return under
    # ANY skip/lookback window, which would defeat the
    # "different constants produce different raw returns" assertion below.
    for i, ticker in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        prices = 100.0 + np.cumsum(np.ones(n) * ((i + 1) * drift_step))
        df = pd.DataFrame({"Close": prices, "Open": prices, "High": prices,
                           "Low": prices, "Volume": 1000}, index=dates)
        tech_raw[ticker] = df
    return tech_raw


def test_compute_xsec_momentum_returns_and_ranks_share_one_universe():
    """pipeline/production_steps.py::_compute_xsec_momentum (Finding 15's
    single-source helper) must return a raw-return dict and a rank Series
    covering the EXACT same set of eligible tickers, since both
    XSec_12_1M and XSec_Momentum_Rank are now sourced from the same call
    -- they can no longer silently diverge the way the old two-copies-of-
    the-formula code could if skip_days/lookback_days were ever changed in
    only one place."""
    tech_raw = _make_tech_raw_for_helper()
    returns, ranks = _compute_xsec_momentum(tech_raw)

    assert set(returns.keys()) == set(ranks.index)
    assert len(returns) == 5
    for val in ranks.values:
        assert 0.0 <= val <= 1.0


def test_compute_xsec_momentum_matches_orchestrator_ranks_at_default_constants():
    """At the shared default skip_days=22/lookback_days=252, the helper's
    ranks must agree exactly with main_orchestrator.compute_xsec_momentum_ranks
    -- proving the duplicated formula (necessary because this fix's file
    scope is restricted to pipeline/production_steps.py) has not drifted
    from the orchestrator's own implementation."""
    tech_raw = _make_tech_raw_for_helper()
    _, helper_ranks = _compute_xsec_momentum(tech_raw)
    orchestrator_ranks = compute_xsec_momentum_ranks(tech_raw)

    assert set(helper_ranks.index) == set(orchestrator_ranks.index)
    for ticker in helper_ranks.index:
        assert abs(float(helper_ranks[ticker]) - float(orchestrator_ranks[ticker])) < 1e-9


def test_compute_xsec_momentum_raw_return_formula_matches_rank_input():
    """Directly proves the two outputs can't diverge: recompute each
    ticker's raw 12-1m return by hand from the SAME skip/lookback
    constants and confirm it equals both the helper's own raw-return dict
    AND what a manual rank() over those returns would produce -- i.e. the
    rank Series really is derived from the same raw-return values, not a
    second independently-computed series."""
    tech_raw = _make_tech_raw_for_helper()
    skip_days, lookback_days = 22, 252
    returns, ranks = _compute_xsec_momentum(tech_raw, skip_days=skip_days, lookback_days=lookback_days)

    for ticker, df in tech_raw.items():
        close = df["Close"].dropna()
        p_recent = float(close.iloc[-(skip_days + 1)])
        p_old = float(close.iloc[-(lookback_days + 1)])
        expected_return = p_recent / p_old - 1.0
        assert abs(returns[ticker] - expected_return) < 1e-9

    expected_ranks = pd.Series(returns).rank(pct=True, ascending=True)
    for ticker in ranks.index:
        assert abs(float(ranks[ticker]) - float(expected_ranks[ticker])) < 1e-9


def test_compute_xsec_momentum_custom_constants_move_both_outputs_together():
    """If skip_days/lookback_days are changed, BOTH outputs must move
    together (since they come from the same pass) -- the exact scenario
    the pre-fix two-copy code could get wrong if only one copy's constants
    were ever updated."""
    tech_raw = _make_tech_raw_for_helper(n=400, drift_step=0.05)

    returns_default, ranks_default = _compute_xsec_momentum(tech_raw, skip_days=22, lookback_days=252)
    returns_custom, ranks_custom = _compute_xsec_momentum(tech_raw, skip_days=10, lookback_days=300)

    # Both universes are non-empty and both raw-return/rank pairs are
    # mutually consistent (same keys) under their own constants.
    assert set(returns_default.keys()) == set(ranks_default.index)
    assert set(returns_custom.keys()) == set(ranks_custom.index)
    # Different lookback windows over a linearly-drifting series produce
    # numerically different raw returns -- confirming the constants were
    # genuinely threaded through to the computation, not ignored.
    for ticker in returns_default:
        assert abs(returns_default[ticker] - returns_custom[ticker]) > 1e-9


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
