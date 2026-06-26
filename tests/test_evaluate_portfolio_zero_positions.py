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
    """EvaluationEngine backed by an empty in-memory DB so no real trades are read."""
    original_init = transactions_store.TransactionsStore.__init__

    def _mem_init(self, db_url=None):  # noqa: ANN001
        original_init(self, db_url="sqlite:///:memory:")

    transactions_store.TransactionsStore.__init__ = _mem_init
    try:
        return EvaluationEngine()
    finally:
        transactions_store.TransactionsStore.__init__ = original_init


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
