"""
tests/test_export_utils.py
===========================
Unit tests for ``gui/export_utils.py`` — pure CSV-export helpers.

All tests are fully offline: no Streamlit, no network, no filesystem I/O
beyond what pandas' ``to_csv`` does in-memory.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gui.export_utils import (
    dataframe_to_csv_bytes,
    dataframe_to_csv_str,
    signals_snapshot_to_dataframe,
)


class TestDataframeToCsvStr:
    def test_basic_roundtrip(self) -> None:
        df = pd.DataFrame({"symbol": ["AAPL", "MSFT"], "price": [190.5, 410.25]})
        csv = dataframe_to_csv_str(df)
        assert csv.startswith("symbol,price\n")
        assert "AAPL,190.5" in csv
        assert "MSFT,410.25" in csv

    def test_none_dataframe_returns_empty_string(self) -> None:
        assert dataframe_to_csv_str(None) == ""

    def test_empty_dataframe_no_columns(self) -> None:
        df = pd.DataFrame()
        csv = dataframe_to_csv_str(df)
        # An empty frame with no columns/rows serializes to an empty (or
        # near-empty) string — must not raise.
        assert isinstance(csv, str)
        assert csv.strip() == ""

    def test_empty_dataframe_with_columns_header_only(self) -> None:
        df = pd.DataFrame(columns=["symbol", "price"])
        csv = dataframe_to_csv_str(df)
        assert csv.strip() == "symbol,price"

    def test_nan_rendered_as_empty_not_literal_nan(self) -> None:
        df = pd.DataFrame({"symbol": ["AAPL", "MSFT"], "score": [1.23, np.nan]})
        csv = dataframe_to_csv_str(df)
        assert "nan" not in csv.lower()
        lines = csv.strip().splitlines()
        assert lines[0] == "symbol,score"
        assert lines[2] == "MSFT,"

    def test_none_values_rendered_as_empty(self) -> None:
        df = pd.DataFrame({"symbol": ["AAPL", "MSFT"], "note": ["ok", None]})
        csv = dataframe_to_csv_str(df)
        assert "None" not in csv
        lines = csv.strip().splitlines()
        assert lines[2] == "MSFT,"

    def test_index_excluded_by_default(self) -> None:
        df = pd.DataFrame({"a": [1, 2]}, index=["x", "y"])
        csv = dataframe_to_csv_str(df)
        assert csv.strip().splitlines()[0] == "a"

    def test_index_included_when_requested(self) -> None:
        df = pd.DataFrame({"a": [1, 2]}, index=["x", "y"])
        csv = dataframe_to_csv_str(df, index=True)
        lines = csv.strip().splitlines()
        assert lines[0].startswith(",a") or lines[0].startswith("a")
        assert "x,1" in csv


class TestDataframeToCsvBytes:
    def test_returns_bytes(self) -> None:
        df = pd.DataFrame({"symbol": ["AAPL"]})
        result = dataframe_to_csv_bytes(df)
        assert isinstance(result, bytes)
        assert result.decode("utf-8").startswith("symbol\n")

    def test_none_dataframe_returns_empty_bytes(self) -> None:
        assert dataframe_to_csv_bytes(None) == b""

    def test_bytes_match_str_encoding(self) -> None:
        df = pd.DataFrame({"symbol": ["AAPL"], "score": [np.nan]})
        as_bytes = dataframe_to_csv_bytes(df)
        as_str = dataframe_to_csv_str(df)
        assert as_bytes == as_str.encode("utf-8")


class TestSignalsSnapshotToDataframe:
    def test_none_returns_empty_dataframe(self) -> None:
        df = signals_snapshot_to_dataframe(None)
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_empty_list_returns_empty_dataframe(self) -> None:
        df = signals_snapshot_to_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_flattens_list_of_dicts(self) -> None:
        signals = [
            {"symbol": "AAPL", "action": "BUY", "macro_status": "RISK ON"},
            {"symbol": "MSFT", "action": "HOLD", "macro_status": "RISK ON"},
        ]
        df = signals_snapshot_to_dataframe(signals)
        assert list(df["symbol"]) == ["AAPL", "MSFT"]
        assert list(df["action"]) == ["BUY", "HOLD"]

    def test_round_trip_through_csv_helper(self) -> None:
        signals = [
            {"symbol": "AAPL", "kelly_target": 0.12, "hmm_risk_on": np.nan},
        ]
        df = signals_snapshot_to_dataframe(signals)
        csv = dataframe_to_csv_str(df)
        assert "AAPL" in csv
        assert "nan" not in csv.lower()
