"""
tests/test_production_steps_options_columns.py
================================================
Unit tests for pipeline/production_steps.py::_apply_options_columns -- the
writeback that maps OptionsAnalysisStep's per-ticker tech_opt_indicators dict
onto dashboard_df's GARCH_Vol/Realized_Vol_Rank/True_IVR/VRP/Aroon
Oscillator/Coppock Curve/Chandelier Exit columns.

Regression coverage for a CONSTRAINT #4 violation: a ticker missing from
tech_opt_indicators (options analysis failed/dead-lettered for it this cycle)
or missing an individual key used to read back as a fabricated 0.0 -- e.g. a
"VRP: 0.0%" cell that looks like a genuinely-computed, zero volatility risk
premium instead of "we don't know". These columns must be NaN in that case.

Deliberately targets the module-level `_apply_options_columns` function
directly rather than going through ProcessingStep.run() (which imports
main_orchestrator and its full heavy engine chain at call time).
"""
from __future__ import annotations

import math

import pandas as pd

from pipeline.production_steps import _apply_options_columns

_OPTIONS_COLUMNS = (
    "GARCH_Vol", "Realized_Vol_Rank", "True_IVR", "VRP",
    "Aroon Oscillator", "Coppock Curve", "Chandelier Exit",
)


def _df(symbols):
    return pd.DataFrame({"Symbol": symbols})


class TestApplyOptionsColumns:
    def test_full_indicators_populate_every_column(self):
        df = _df(["AAPL"])
        tech_opt_indicators = {
            "AAPL": {
                "GARCH_Vol": 0.25, "Realized_Vol_Rank": 60.0, "True_IVR": 55.0,
                "VRP": 0.03, "Aroon_Oscillator": 40.0, "Coppock_Curve": 12.0,
                "Chandelier_Long": 180.5,
            }
        }
        _apply_options_columns(df, tech_opt_indicators)

        row = df.iloc[0]
        assert row["GARCH_Vol"] == 0.25
        assert row["Realized_Vol_Rank"] == 60.0
        assert row["True_IVR"] == 55.0
        assert row["VRP"] == 0.03
        assert row["Aroon Oscillator"] == 40.0
        assert row["Coppock Curve"] == 12.0
        assert row["Chandelier Exit"] == 180.5

    def test_ticker_missing_from_indicators_stays_nan_not_zero(self):
        """A ticker whose OptionsAnalysisStep._options_one() call failed this
        cycle (dead-lettered, absent from tech_opt_indicators) must degrade
        every one of its options columns to NaN -- never a fabricated 0.0
        that would misread as a genuine zero VRP / zero True IVR."""
        df = _df(["FAILED"])
        _apply_options_columns(df, tech_opt_indicators={})

        row = df.iloc[0]
        for col in _OPTIONS_COLUMNS:
            assert math.isnan(row[col]), f"{col} should be NaN, got {row[col]!r}"

    def test_partial_indicators_leave_missing_keys_nan(self):
        """A ticker present in tech_opt_indicators but missing an individual
        key (e.g. VRP computation failed independently of GARCH) must leave
        only that cell NaN -- successfully-computed siblings are unaffected."""
        df = _df(["PARTIAL"])
        tech_opt_indicators = {"PARTIAL": {"GARCH_Vol": 0.30, "True_IVR": 45.0}}
        _apply_options_columns(df, tech_opt_indicators)

        row = df.iloc[0]
        assert row["GARCH_Vol"] == 0.30
        assert row["True_IVR"] == 45.0
        assert math.isnan(row["Realized_Vol_Rank"])
        assert math.isnan(row["VRP"])
        assert math.isnan(row["Aroon Oscillator"])
        assert math.isnan(row["Coppock Curve"])
        assert math.isnan(row["Chandelier Exit"])

    def test_mixed_universe_only_failed_ticker_is_nan(self):
        df = _df(["OK", "FAILED"])
        tech_opt_indicators = {
            "OK": {
                "GARCH_Vol": 0.20, "Realized_Vol_Rank": 70.0, "True_IVR": 65.0,
                "VRP": 0.05, "Aroon_Oscillator": 20.0, "Coppock_Curve": 8.0,
                "Chandelier_Long": 99.0,
            }
        }
        _apply_options_columns(df, tech_opt_indicators)

        ok_row = df.loc[df["Symbol"] == "OK"].iloc[0]
        failed_row = df.loc[df["Symbol"] == "FAILED"].iloc[0]
        assert ok_row["VRP"] == 0.05
        for col in _OPTIONS_COLUMNS:
            assert math.isnan(failed_row[col])

    def test_empty_universe_creates_columns_no_crash(self):
        df = _df([])
        _apply_options_columns(df, tech_opt_indicators={})
        for col in _OPTIONS_COLUMNS:
            assert col in df.columns
