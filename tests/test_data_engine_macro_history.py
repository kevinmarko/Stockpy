"""
tests/test_data_engine_macro_history.py
========================================
Regression coverage for the T10YIE (10-Year Breakeven Inflation Rate) addition
to ``data_engine.DataEngine.fetch_macro_history()`` -- the "stagflation regime"
fix (see CLAUDE.md's "Gaussian HMM Regime Detector tuning & diagnostics" and
the ``settings.HMM_INFLATION_FEATURE_ENABLED`` field). Prior to this fix,
``fetch_macro_history()`` never fetched the ``T10YIE`` FRED series at all,
which left ``settings.HMM_INFLATION_FEATURE_ENABLED`` and
``macro_engine.py``'s ``HistoricalStore().get_macro("T10YIE", ...)`` call
silently no-op'd (no cached rows to ever top up from) even when the operator
turned the feature flag on.

Uses a fake stand-in for ``fredapi.Fred`` (monkeypatched onto
``data_engine.Fred``) rather than mocking the whole ``DataEngine`` instance,
so the assertions exercise the REAL ``fetch_macro_history()`` implementation,
not a test double of it.
"""

from __future__ import annotations

import pandas as pd

import data_engine
from data_engine import DataEngine


class _FakeFred:
    """Minimal stand-in for ``fredapi.Fred`` with a controllable, per-series
    ``get_series()``. Mirrors ``tests/test_macro_engine.py``'s ``_FakeFred``
    convention."""

    def __init__(self, series_map: dict, *, raise_on: frozenset = frozenset(), api_key: str = ""):
        self._series_map = series_map
        self._raise_on = raise_on

    def get_series(self, series_id: str) -> pd.Series:
        if series_id in self._raise_on:
            raise RuntimeError(f"FRED unavailable for {series_id}")
        series = self._series_map.get(series_id)
        if series is None:
            raise KeyError(f"_FakeFred has no series configured for {series_id!r}")
        return series


def _make_engine(monkeypatch, series_map: dict, *, raise_on: frozenset = frozenset()) -> DataEngine:
    """Construct a real DataEngine whose ``self.fred`` is a ``_FakeFred``
    (bypasses the real network-touching ``fredapi.Fred(api_key=...)`` call)."""
    monkeypatch.setattr(
        data_engine, "Fred",
        lambda api_key: _FakeFred(series_map, raise_on=raise_on),
    )
    return DataEngine(fred_api_key="fake-test-key")


def _daily_series(start_value: float, n: int = 30) -> pd.Series:
    dates = pd.bdate_range(end="2026-08-01", periods=n)
    return pd.Series([start_value + i * 0.01 for i in range(n)], index=dates)


class TestFetchMacroHistoryIncludesT10YIE:
    def test_output_includes_a_real_t10yie_column(self, monkeypatch):
        series_map = {
            "VIXCLS": _daily_series(15.0),
            "T10Y2Y": _daily_series(0.5),
            "BAMLH0A0HYM2": _daily_series(2.5),
            "BAA10Y": _daily_series(2.0),
            "UNRATE": _daily_series(4.0),
            "T10YIE": _daily_series(2.3),
        }
        engine = _make_engine(monkeypatch, series_map)

        history_df = engine.fetch_macro_history()

        assert "T10YIE" in history_df.columns
        assert not history_df["T10YIE"].dropna().empty
        # Real (mocked-but-realistic) values, not fabricated placeholders --
        # the values round-trip exactly from the fake FRED source.
        expected = series_map["T10YIE"]
        expected.index = pd.to_datetime(expected.index)
        pd.testing.assert_series_equal(
            history_df["T10YIE"].sort_index(), expected.sort_index(), check_names=False,
        )
        # All six series present -- the pre-existing five plus T10YIE.
        for col in ("VIXCLS", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "UNRATE", "T10YIE"):
            assert col in history_df.columns

    def test_no_fred_client_failure_path_column_list_includes_t10yie(self):
        # self.fred is None -> the first (no-FRED-initialized) empty-DataFrame
        # literal must also list T10YIE for schema consistency with the
        # success path.
        engine = DataEngine.__new__(DataEngine)
        engine.fred = None
        engine.fred_key = ""

        history_df = engine.fetch_macro_history()

        assert history_df.empty
        assert list(history_df.columns) == [
            "VIXCLS", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "UNRATE", "T10YIE",
        ]

    def test_fetch_exception_failure_path_column_list_includes_t10yie(self, monkeypatch):
        # A mid-fetch exception (e.g. T10YIE itself unavailable) must degrade
        # to the SAME six-column empty-DataFrame shape (CONSTRAINT #6 -- never
        # a fabricated partial frame), not raise.
        series_map = {
            "VIXCLS": _daily_series(15.0),
            "T10Y2Y": _daily_series(0.5),
            "BAMLH0A0HYM2": _daily_series(2.5),
            "BAA10Y": _daily_series(2.0),
            "UNRATE": _daily_series(4.0),
        }
        engine = _make_engine(monkeypatch, series_map, raise_on=frozenset({"T10YIE"}))

        history_df = engine.fetch_macro_history()

        assert history_df.empty
        assert list(history_df.columns) == [
            "VIXCLS", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "UNRATE", "T10YIE",
        ]
