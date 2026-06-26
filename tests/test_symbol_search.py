"""
tests/test_symbol_search.py
=============================
Unit tests for :func:`gui.symbol_search.filter_by_symbol`.

All tests are fully offline and Streamlit-free.

Verified invariants
-------------------
*   Empty query → full DataFrame unchanged.
*   None query → full DataFrame unchanged.
*   Whitespace query → full DataFrame unchanged.
*   Exact match (case-insensitive) → 1 row.
*   Partial prefix match → N rows.
*   No match → empty DataFrame.
*   NaN symbol rows are kept (pass-through — not silently dropped).
*   Custom column parameter works.
*   Missing column → falls back to first column.
*   Empty DataFrame → empty DataFrame (no error).
*   Case-insensitive matching ("aapl" matches "AAPL").
"""

from __future__ import annotations

import pandas as pd
import pytest

from gui.symbol_search import filter_by_symbol


def _df(*symbols: str | None) -> pd.DataFrame:
    return pd.DataFrame({"Symbol": list(symbols), "Value": range(len(symbols))})


# ===========================================================================
# Empty / None / whitespace query → passthrough
# ===========================================================================

def test_empty_string_returns_all():
    df = _df("AAPL", "MSFT", "TSLA")
    assert len(filter_by_symbol(df, "")) == 3


def test_none_returns_all():
    df = _df("AAPL", "MSFT")
    assert len(filter_by_symbol(df, None)) == 2


def test_whitespace_returns_all():
    df = _df("AAPL", "MSFT")
    assert len(filter_by_symbol(df, "   ")) == 2


# ===========================================================================
# Matching behaviour
# ===========================================================================

def test_exact_match():
    df = _df("AAPL", "MSFT", "TSLA")
    result = filter_by_symbol(df, "MSFT")
    assert list(result["Symbol"]) == ["MSFT"]


def test_case_insensitive_match():
    df = _df("AAPL", "MSFT", "TSLA")
    result = filter_by_symbol(df, "aapl")
    assert list(result["Symbol"]) == ["AAPL"]


def test_partial_prefix_match():
    df = _df("APPS", "APPN", "AAPL", "MSFT")
    result = filter_by_symbol(df, "APP")
    # "APP" is a substring of "APPS" and "APPN" but NOT of "AAPL"
    assert set(result["Symbol"]) == {"APPS", "APPN"}


def test_no_match_returns_empty():
    df = _df("AAPL", "MSFT")
    result = filter_by_symbol(df, "ZZZZZ")
    assert result.empty


def test_contains_not_prefix_only():
    """'AX' matches 'AAPL' at position 1 is not matched; but 'PL' matches 'AAPL'."""
    df = _df("AAPL", "AMZN", "GOOG")
    result = filter_by_symbol(df, "PL")
    # 'AAPL' contains 'PL'
    assert "AAPL" in result["Symbol"].values


# ===========================================================================
# NaN rows are kept
# ===========================================================================

def test_nan_rows_kept():
    df = _df("AAPL", None, "MSFT")
    result = filter_by_symbol(df, "AAPL")
    # NaN row passes through (na=True in str.contains)
    assert "AAPL" in result["Symbol"].values
    # NaN row is also kept
    assert result["Symbol"].isna().any()


# ===========================================================================
# Custom column
# ===========================================================================

def test_custom_column():
    df = pd.DataFrame({"Ticker": ["AAPL", "MSFT"], "Val": [1, 2]})
    result = filter_by_symbol(df, "AAPL", column="Ticker")
    assert len(result) == 1
    assert result["Ticker"].iloc[0] == "AAPL"


def test_missing_symbol_col_fallback_to_first():
    """If 'Symbol' column is absent, fall back to first column."""
    df = pd.DataFrame({"Ticker": ["AAPL", "MSFT"], "Val": [1, 2]})
    # column= defaults to "Symbol" which is absent → falls back to "Ticker"
    result = filter_by_symbol(df, "AAPL")
    assert len(result) == 1


# ===========================================================================
# Edge cases
# ===========================================================================

def test_empty_dataframe():
    df = pd.DataFrame({"Symbol": pd.Series(dtype=str)})
    result = filter_by_symbol(df, "AAPL")
    assert result.empty


def test_no_columns_dataframe():
    df = pd.DataFrame()
    result = filter_by_symbol(df, "AAPL")
    assert result.empty


def test_returns_view_not_copy():
    """Filter with empty query returns the same object (not a full copy)."""
    df = _df("AAPL", "MSFT")
    result = filter_by_symbol(df, "")
    assert result is df
