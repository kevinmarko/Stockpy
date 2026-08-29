"""
tests/test_production_steps_forecast_columns.py
=================================================
Unit tests for pipeline/production_steps.py::_apply_forecast_columns -- the
writeback that maps ForecastingStep's per-ticker forecast_results dict onto
dashboard_df's Target_Days/ARIMA/MC_Target/MC_Lower/MC_Upper/Forecast_10/
Forecast_30/Forecast_60/Forecast_90/Forecast_30_Prophet_Lower/
Forecast_30_Prophet_Upper columns.

Regression coverage for a CONSTRAINT #4 violation: a ticker missing from
forecast_results (price was 0/missing, so ForecastingStep._forecast_one()
short-circuited without ever calling the forecasting engine) or missing an
individual key (e.g. Forecast_30_Prophet_Lower/_Upper, which
ForecastingEngine.generate_forecast only sets when Prophet actually produced
output that cycle) used to read back as a fabricated 0.0 -- e.g. a
"Forecast 30 Day: $0.00" cell that looks like a genuinely-computed price
target instead of "we don't know". These columns must be NaN in that case.

Deliberately targets the module-level `_apply_forecast_columns` function
directly rather than going through ForecastingStep.run() (which imports
main_orchestrator and its full heavy engine chain at call time).
"""
from __future__ import annotations

import math

import pandas as pd

from pipeline.production_steps import _apply_forecast_columns

_FORECAST_COLS = [
    'Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
    'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
    'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper',
]


def _df(symbols):
    return pd.DataFrame({"Symbol": symbols})


class TestApplyForecastColumns:
    def test_full_forecast_populates_every_column(self):
        df = _df(["AAPL"])
        forecast_results = {
            "AAPL": {
                'Target_Days': 30, 'ARIMA': 190.0, 'MC_Target': 192.5,
                'MC_Lower': 175.0, 'MC_Upper': 210.0, 'Forecast_10': 188.0,
                'Forecast_30': 192.5, 'Forecast_60': 196.0, 'Forecast_90': 199.0,
                'Forecast_30_Prophet_Lower': 180.0, 'Forecast_30_Prophet_Upper': 205.0,
            }
        }
        _apply_forecast_columns(df, forecast_results, _FORECAST_COLS)

        row = df.iloc[0]
        assert row["Target_Days"] == 30
        assert row["ARIMA"] == 190.0
        assert row["MC_Target"] == 192.5
        assert row["MC_Lower"] == 175.0
        assert row["MC_Upper"] == 210.0
        assert row["Forecast_10"] == 188.0
        assert row["Forecast_30"] == 192.5
        assert row["Forecast_60"] == 196.0
        assert row["Forecast_90"] == 199.0
        assert row["Forecast_30_Prophet_Lower"] == 180.0
        assert row["Forecast_30_Prophet_Upper"] == 205.0

    def test_ticker_missing_from_results_stays_nan_not_zero(self):
        """A ticker whose ForecastingStep._forecast_one() short-circuited
        this cycle (no price, absent from forecast_results) must degrade
        every one of its forecast columns to NaN -- never a fabricated 0.0
        that would misread as a genuine $0 price target."""
        df = _df(["DEADLETTERED"])
        _apply_forecast_columns(df, forecast_results={}, forecast_cols=_FORECAST_COLS)

        row = df.iloc[0]
        for col in _FORECAST_COLS:
            assert math.isnan(row[col]), f"{col} should be NaN, got {row[col]!r}"

    def test_partial_results_leave_missing_keys_nan(self):
        """A ticker present in forecast_results but missing the Prophet
        keys (Prophet unavailable/didn't produce output this cycle) must
        leave only those cells NaN -- the core MC/ARIMA forecasts that DID
        compute are unaffected."""
        df = _df(["NOPROPHET"])
        forecast_results = {
            "NOPROPHET": {
                'Target_Days': 60, 'ARIMA': 50.0, 'MC_Target': 51.0,
                'MC_Lower': 45.0, 'MC_Upper': 57.0, 'Forecast_10': 49.5,
                'Forecast_30': 51.0, 'Forecast_60': 52.5, 'Forecast_90': 54.0,
                # Forecast_30_Prophet_Lower / _Upper deliberately absent.
            }
        }
        _apply_forecast_columns(df, forecast_results, _FORECAST_COLS)

        row = df.iloc[0]
        assert row["ARIMA"] == 50.0
        assert row["MC_Target"] == 51.0
        assert row["Forecast_90"] == 54.0
        assert math.isnan(row["Forecast_30_Prophet_Lower"])
        assert math.isnan(row["Forecast_30_Prophet_Upper"])

    def test_mixed_universe_only_deadlettered_ticker_is_nan(self):
        df = _df(["OK", "DEADLETTERED"])
        forecast_results = {
            "OK": {
                'Target_Days': 30, 'ARIMA': 100.0, 'MC_Target': 101.0,
                'MC_Lower': 90.0, 'MC_Upper': 112.0, 'Forecast_10': 99.0,
                'Forecast_30': 101.0, 'Forecast_60': 103.0, 'Forecast_90': 105.0,
                'Forecast_30_Prophet_Lower': 95.0, 'Forecast_30_Prophet_Upper': 108.0,
            }
        }
        _apply_forecast_columns(df, forecast_results, _FORECAST_COLS)

        ok_row = df.loc[df["Symbol"] == "OK"].iloc[0]
        dead_row = df.loc[df["Symbol"] == "DEADLETTERED"].iloc[0]
        assert ok_row["Forecast_30"] == 101.0
        for col in _FORECAST_COLS:
            assert math.isnan(dead_row[col])

    def test_empty_universe_creates_columns_no_crash(self):
        df = _df([])
        _apply_forecast_columns(df, forecast_results={}, forecast_cols=_FORECAST_COLS)
        for col in _FORECAST_COLS:
            assert col in df.columns

    def test_genuine_zero_forecast_is_preserved_not_confused_with_missing(self):
        """A ticker that genuinely forecasts a near-zero value (e.g. MC_Lower
        for a penny stock) must keep that real 0.0 -- only ABSENCE (missing
        ticker or missing key) becomes NaN, not a computed zero."""
        df = _df(["PENNY"])
        forecast_results = {
            "PENNY": {
                'Target_Days': 10, 'ARIMA': 0.05, 'MC_Target': 0.04,
                'MC_Lower': 0.0, 'MC_Upper': 0.08, 'Forecast_10': 0.04,
                'Forecast_30': 0.03, 'Forecast_60': 0.02, 'Forecast_90': 0.01,
                'Forecast_30_Prophet_Lower': 0.0, 'Forecast_30_Prophet_Upper': 0.06,
            }
        }
        _apply_forecast_columns(df, forecast_results, _FORECAST_COLS)

        row = df.iloc[0]
        assert row["MC_Lower"] == 0.0
        assert not math.isnan(row["MC_Lower"])
        assert row["Forecast_30_Prophet_Lower"] == 0.0
        assert not math.isnan(row["Forecast_30_Prophet_Lower"])
