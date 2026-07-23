"""
tests/test_production_steps_sector_heat.py
============================================
Unit tests for pipeline/production_steps.py::_apply_sector_heat_factor -- the
writeback that maps compute_sector_heat_factors()'s {sector: heat_value}
dict onto dashboard_df's Sector_Heat_Factor column, one GDELT query per
distinct sector (never per ticker).

Deliberately targets the module-level `_apply_sector_heat_factor` function
directly rather than going through StrategyEvalStep.run() (which imports
main_orchestrator and its full heavy engine chain at call time) -- this
keeps the test suite importable/runnable without pulling in yfinance/
fredapi/statsmodels/etc.

All GDELT network calls are monkeypatched via
data.sentiment_sources.compute_sector_heat_factors; no real network requests
are made.
"""
from __future__ import annotations

import math
from unittest.mock import patch

import pandas as pd
import pytest

from pipeline.production_steps import _apply_sector_heat_factor


def _df(rows):
    return pd.DataFrame(rows)


class TestApplySectorHeatFactor:
    def test_disabled_leaves_column_nan_with_no_compute_call(self):
        df = _df([
            {"Symbol": "AAPL", "sector": "Technology"},
            {"Symbol": "XOM", "sector": "Energy"},
        ])
        with patch("settings.settings.SECTOR_HEAT_ENABLED", False):
            with patch("data.sentiment_sources.compute_sector_heat_factors") as mock_compute:
                _apply_sector_heat_factor(df)
        mock_compute.assert_not_called()
        assert df["Sector_Heat_Factor"].isna().all()

    def test_enabled_maps_sector_heat_onto_every_ticker_row(self):
        df = _df([
            {"Symbol": "AAPL", "sector": "Technology"},
            {"Symbol": "MSFT", "sector": "Technology"},
            {"Symbol": "XOM", "sector": "Energy"},
        ])
        heat_map = {"Technology": 1.25, "Energy": 0.4}
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            with patch(
                "data.sentiment_sources.compute_sector_heat_factors", return_value=heat_map,
            ) as mock_compute:
                _apply_sector_heat_factor(df)

        assert df.loc[df["Symbol"] == "AAPL", "Sector_Heat_Factor"].iloc[0] == pytest.approx(1.25)
        assert df.loc[df["Symbol"] == "MSFT", "Sector_Heat_Factor"].iloc[0] == pytest.approx(1.25)
        assert df.loc[df["Symbol"] == "XOM", "Sector_Heat_Factor"].iloc[0] == pytest.approx(0.4)
        # One call, with the DEDUPLICATED sector list (2 distinct sectors for
        # 3 tickers) -- never one call per ticker.
        mock_compute.assert_called_once()
        called_sectors = sorted(mock_compute.call_args.args[0])
        assert called_sectors == ["Energy", "Technology"]

    def test_sector_absent_from_heat_map_stays_nan_not_fabricated(self):
        """A sector whose GDELT query failed (absent from the returned dict)
        must leave that ticker's cell NaN, never a fabricated fallback
        (CONSTRAINT #4) -- while other, successfully-computed sectors are
        unaffected."""
        df = _df([
            {"Symbol": "AAPL", "sector": "Technology"},
            {"Symbol": "XOM", "sector": "Energy"},
        ])
        heat_map = {"Technology": 2.0}  # Energy failed this cycle
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            with patch("data.sentiment_sources.compute_sector_heat_factors", return_value=heat_map):
                _apply_sector_heat_factor(df)

        assert df.loc[df["Symbol"] == "AAPL", "Sector_Heat_Factor"].iloc[0] == pytest.approx(2.0)
        assert math.isnan(df.loc[df["Symbol"] == "XOM", "Sector_Heat_Factor"].iloc[0])

    def test_unknown_or_missing_sector_never_included_in_query_and_stays_nan(self):
        df = _df([
            {"Symbol": "AAPL", "sector": "Technology"},
            {"Symbol": "ZZZ", "sector": "Unknown"},
            {"Symbol": "YYY", "sector": float("nan")},
        ])
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            with patch(
                "data.sentiment_sources.compute_sector_heat_factors",
                return_value={"Technology": 1.0},
            ) as mock_compute:
                _apply_sector_heat_factor(df)

        called_sectors = mock_compute.call_args.args[0]
        assert "Unknown" not in called_sectors
        assert called_sectors == ["Technology"]
        assert math.isnan(df.loc[df["Symbol"] == "ZZZ", "Sector_Heat_Factor"].iloc[0])
        assert math.isnan(df.loc[df["Symbol"] == "YYY", "Sector_Heat_Factor"].iloc[0])

    def test_empty_universe_returns_empty_column_no_call(self):
        df = _df([])
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            with patch("data.sentiment_sources.compute_sector_heat_factors") as mock_compute:
                _apply_sector_heat_factor(df)
        mock_compute.assert_not_called()
        assert "Sector_Heat_Factor" in df.columns

    def test_missing_sector_column_degrades_to_nan_no_crash(self):
        """CONSTRAINT #6: if dashboard_df somehow lacks a 'sector' column at
        all, this must degrade to an all-NaN column, never raise."""
        df = _df([{"Symbol": "AAPL"}])
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            _apply_sector_heat_factor(df)  # must not raise
        assert df["Sector_Heat_Factor"].isna().all()

    def test_compute_exception_degrades_whole_column_to_nan(self):
        """CONSTRAINT #6: an exception anywhere in the compute call must
        reset the WHOLE column to NaN, never leave it partially populated or
        propagate and crash the pipeline."""
        df = _df([
            {"Symbol": "AAPL", "sector": "Technology"},
            {"Symbol": "XOM", "sector": "Energy"},
        ])
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            with patch(
                "data.sentiment_sources.compute_sector_heat_factors",
                side_effect=RuntimeError("boom"),
            ):
                _apply_sector_heat_factor(df)  # must not raise
        assert df["Sector_Heat_Factor"].isna().all()

    def test_empty_heat_map_result_leaves_column_nan(self):
        df = _df([{"Symbol": "AAPL", "sector": "Technology"}])
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True):
            with patch("data.sentiment_sources.compute_sector_heat_factors", return_value={}):
                _apply_sector_heat_factor(df)
        assert df["Sector_Heat_Factor"].isna().all()
