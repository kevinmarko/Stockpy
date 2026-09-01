"""
gui/export_utils.py
====================
Pure CSV-export helpers for the InvestYo Command Center.

**Zero Streamlit imports** — every function here takes/returns plain Python
objects (``pandas.DataFrame`` in, ``str``/``bytes`` out) so they are trivially
unit-testable without spinning up a Streamlit session. Callers wire the result
into a ``st.download_button(data=..., file_name=..., mime="text/csv")`` call.

Design notes
------------
* NaN values are rendered as an EMPTY string (never the literal text
  ``"nan"``) — a downstream analyst opening the CSV in Excel/pandas should
  see a blank cell, matching the "no fabricated metrics" spirit used
  elsewhere in this codebase (CONSTRAINT #4): we don't want a NaN sentinel
  masquerading as a printable value.
* An empty/`None` DataFrame produces valid (header-only, or fully empty)
  CSV output rather than raising — this module is read-only and must
  degrade gracefully like every other file-backed GUI helper.
* Never raises on ordinary pandas input; callers that need explicit
  failure handling should catch broadly at the UI boundary as elsewhere
  in ``gui/`` (``safe_panel`` pattern in ``gui/app.py``).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def dataframe_to_csv_bytes(df: Optional[pd.DataFrame], *, index: bool = False) -> bytes:
    """Serialize *df* to CSV and return UTF-8 encoded bytes.

    Parameters
    ----------
    df : pandas.DataFrame or None
        The table to export. ``None`` or an empty DataFrame yields an empty
        (zero-byte, or header-only) CSV rather than raising.
    index : bool, default False
        Whether to include the DataFrame index as a CSV column.

    Returns
    -------
    bytes
        UTF-8 encoded CSV content, ready for ``st.download_button(data=...)``.
    """
    return dataframe_to_csv_str(df, index=index).encode("utf-8")


def dataframe_to_csv_str(df: Optional[pd.DataFrame], *, index: bool = False) -> str:
    """Serialize *df* to a CSV string with NaN rendered as empty (never ``"nan"``).

    Parameters
    ----------
    df : pandas.DataFrame or None
        The table to export. ``None`` yields ``""``. An empty DataFrame (no
        rows, possibly no columns) yields a header-only (or empty) CSV.
    index : bool, default False
        Whether to include the DataFrame index as a CSV column.

    Returns
    -------
    str
        CSV text with ``\\r\\n``-free, ``\\n``-terminated lines (pandas
        default), and NaN/None cells rendered as empty strings.
    """
    if df is None:
        return ""
    # na_rep="" is the load-bearing bit: pandas' default would otherwise
    # write the literal text "nan" for missing values, which is misleading
    # in an offline analysis spreadsheet.
    return df.to_csv(index=index, na_rep="")


def signals_snapshot_to_dataframe(signals: Optional[list]) -> pd.DataFrame:
    """Flatten the ``state_snapshot.json`` ``"signals"`` list into a DataFrame.

    Parameters
    ----------
    signals : list[dict] or None
        The ``signals`` array as written by
        ``main_orchestrator._write_state_snapshot()`` — one dict per symbol
        with keys such as ``symbol``, ``action``, ``kelly_target``, ``score``,
        ``price``, ``shares``, ``macro_status``, ``hmm_risk_on``,
        ``buy_range``, ``sell_range``, and the ``advisory_*`` fields.

    Returns
    -------
    pandas.DataFrame
        One row per symbol. Returns an empty DataFrame (no columns) when
        *signals* is ``None`` or empty — never raises.
    """
    if not signals:
        return pd.DataFrame()
    return pd.DataFrame(signals)
