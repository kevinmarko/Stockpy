"""Regression tests for ZeroDivisionError when all position_sizes are zero.

Reproduces the production crash logged on 2026-06-26:
  "Platform execution pipeline crashed: float division by zero"

Root cause: evaluate_portfolio's Brinson-Fachler block divided by
df['position_size'].sum() without guarding against the watchlist-only
case where Shares * Price == 0 for every row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import transactions_store

from evaluation_engine import EvaluationEngine
from tests._db_isolation import redirect_class_to_memory_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_watchlist_df() -> pd.DataFrame:
    """Three watchlist-only tickers with 0 shares → position_size = 0."""
    return pd.DataFrame(
        {
            "Symbol": ["AAPL", "MSFT", "GOOG"],
            "sector": ["Technology", "Technology", "Technology"],
            "Shares": [0.0, 0.0, 0.0],
            "Price": [180.0, 420.0, 175.0],
            "position_size": [0.0, 0.0, 0.0],  # Shares * Price = 0
            "stop_loss_pct": [0.05, 0.05, 0.05],
            "Relative_Strength": [0.05, 0.03, 0.07],
        }
    )


def _make_benchmark_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"sector": ["Technology"], "weight": [1.0], "return": [0.02]}
    )


def _patched_ee() -> EvaluationEngine:
    """EvaluationEngine backed by an empty in-memory DB so no real trades are read.

    CORRECTED (found during a PR review, verified by direct execution):
    EvaluationEngine.__init__ never constructs a TransactionsStore -- only
    evaluate_portfolio() does, internally, with no override parameter. The
    previous version of this helper patched TransactionsStore.__init__ only
    for the duration of EvaluationEngine() construction and restored it in a
    finally block immediately afterward -- by the time a test called
    ee.evaluate_portfolio(...), the patch was already gone, so every test in
    this file silently read the real, git-committed on-disk quant_platform.db
    instead of an in-memory one (a read, not a write, so `git status` never
    caught it). Wrapping evaluate_portfolio() itself, rather than the
    constructor, keeps the redirect active for exactly the call that needs it.
    """
    ee = EvaluationEngine()
    original_evaluate_portfolio = ee.evaluate_portfolio

    def _wrapped_evaluate_portfolio(*args, **kwargs):
        with redirect_class_to_memory_db(transactions_store.TransactionsStore):
            return original_evaluate_portfolio(*args, **kwargs)

    ee.evaluate_portfolio = _wrapped_evaluate_portfolio
    return ee


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestZeroPositionSizes:
    """evaluate_portfolio must not crash when all position_sizes are zero."""

    def test_no_zero_division_error(self) -> None:
        """Calling evaluate_portfolio with all-zero position_sizes must not raise."""
        ee = _patched_ee()
        df = _make_watchlist_df()
        bench = _make_benchmark_df()
        # This was raising ZeroDivisionError before the fix.
        result = ee.evaluate_portfolio(df, bench)
        assert result is not None

    def test_bf_columns_default_to_zero_on_zero_positions(self) -> None:
        """BF_Allocation and BF_Selection must be 0.0, not NaN, when skipped."""
        ee = _patched_ee()
        df = _make_watchlist_df()
        bench = _make_benchmark_df()
        result = ee.evaluate_portfolio(df, bench)
        assert (result["BF_Allocation"] == 0.0).all()
        assert (result["BF_Selection"] == 0.0).all()

    def test_portfolio_heat_column_present(self) -> None:
        """Portfolio_Heat column must exist and be a valid float even at zero positions."""
        ee = _patched_ee()
        df = _make_watchlist_df()
        bench = _make_benchmark_df()
        result = ee.evaluate_portfolio(df, bench)
        assert "Portfolio_Heat" in result.columns
        assert not result["Portfolio_Heat"].isna().any()

    def test_mix_of_zero_and_nonzero_positions(self) -> None:
        """A mix of held and watchlist tickers (some 0, some >0) must run BF normally."""
        ee = _patched_ee()
        df = pd.DataFrame(
            {
                "Symbol": ["AAPL", "MSFT"],
                "sector": ["Technology", "Technology"],
                "position_size": [15000.0, 0.0],  # MSFT is watchlist-only
                "stop_loss_pct": [0.05, 0.05],
                "Relative_Strength": [0.05, 0.03],
            }
        )
        bench = _make_benchmark_df()
        # Should NOT crash; BF runs on the nonzero total.
        result = ee.evaluate_portfolio(df, bench)
        assert "BF_Allocation" in result.columns

    def test_no_rows_at_all(self) -> None:
        """Empty DataFrame must not crash."""
        ee = _patched_ee()
        df = pd.DataFrame(
            columns=["Symbol", "sector", "position_size", "stop_loss_pct", "Relative_Strength"]
        )
        bench = _make_benchmark_df()
        result = ee.evaluate_portfolio(df, bench)
        assert result is not None and result.empty
