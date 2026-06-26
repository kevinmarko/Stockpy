"""
gui/symbol_search.py
====================
Pure, Streamlit-free helper for filtering tabular data by ticker symbol.

Shared by the **Live Inventory** tab and the **Report Viewer** tab so the
filtering logic lives in one place and is testable without a running app
(CONSTRAINT #7 — integrate, don't duplicate).

Public API
----------
``filter_by_symbol(df, query) -> pd.DataFrame``
    Case-insensitive prefix/contains match on a ``Symbol`` column.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Canonical column name we search against. Falls back to the first column
# if absent so callers with differently-cased headers still work.
_SYMBOL_COL = "Symbol"


def filter_by_symbol(
    df: pd.DataFrame,
    query: Optional[str],
    *,
    column: Optional[str] = None,
) -> pd.DataFrame:
    """Return rows whose ticker symbol matches ``query``.

    Matching rules
    --------------
    *   Empty or whitespace-only query → return the full DataFrame unchanged.
    *   Otherwise: case-insensitive *contains* match on the symbol column so
        the operator can type a partial ticker ("APP" matches "AAPL", "APP",
        "APPS", …).
    *   Rows whose symbol column is NaN or non-string are kept (they pass the
        filter) so ``EQUITY_ONLY`` sentinels don't vanish silently.

    Parameters
    ----------
    df:
        Input DataFrame.  Must not be mutated — a filtered view is returned.
    query:
        Search term supplied by the operator (``st.text_input`` value).
    column:
        Name of the symbol column to search.  Defaults to ``"Symbol"``; falls
        back to the first column if ``"Symbol"`` is absent.

    Returns
    -------
    pd.DataFrame
        Filtered view (never a copy of the whole frame when unfiltered).
    """
    if df.empty:
        return df

    q = (query or "").strip().upper()
    if not q:
        return df

    # Resolve the target column.
    col = column or _SYMBOL_COL
    if col not in df.columns:
        if df.columns.empty:
            return df
        col = df.columns[0]
        logger.debug("filter_by_symbol: '%s' not found, using '%s'", _SYMBOL_COL, col)

    # Capture NaN rows before .astype(str) converts them to the literal "nan"/"None".
    # NaN rows always pass through so EQUITY_ONLY sentinels without a symbol are
    # never silently dropped by the filter.
    nan_mask = df[col].isna()
    match_mask = df[col].astype(str).str.upper().str.contains(q, na=False, regex=False)
    return df[nan_mask | match_mask]
