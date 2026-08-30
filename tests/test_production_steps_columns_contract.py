"""
tests/test_production_steps_columns_contract.py
=================================================
Shared CONSTRAINT #4 writeback contract, proved once and applied to both of
pipeline/production_steps.py's per-ticker dict -> dashboard_df column
writers: ``_apply_forecast_columns`` and ``_apply_options_columns``.
Formerly two structural-twin files (tests/test_production_steps_forecast_columns.py,
tests/test_production_steps_options_columns.py) independently re-proving the
same five-scenario contract for two different writers -- consolidated here
per this repo's own redundancy audit. Nothing lost: both writers keep their
own individually-reported test case per scenario, this only cuts the
duplicated boilerplate source.

Contract (identical for every writer):
  1. A fully-populated source dict populates every target column.
  2. A ticker entirely ABSENT from the source dict degrades every one of its
     columns to NaN -- never a fabricated 0.0 (CONSTRAINT #4).
  3. A ticker PRESENT in the source dict but missing one key leaves only
     that column NaN; siblings that did compute are unaffected.
  4. A mixed universe (one healthy ticker, one dead-lettered) leaves only
     the dead-lettered ticker's row NaN.
  5. An empty universe creates every target column with no crash.

``_apply_forecast_columns`` maps ForecastingStep's per-ticker forecast_results
dict onto Target_Days/ARIMA/MC_Target/MC_Lower/MC_Upper/Forecast_10/
Forecast_30/Forecast_60/Forecast_90/Forecast_30_Prophet_Lower/
Forecast_30_Prophet_Upper -- source keys equal column names.

``_apply_options_columns`` maps OptionsAnalysisStep's per-ticker
tech_opt_indicators dict onto GARCH_Vol/Realized_Vol_Rank/True_IVR/VRP/
Aroon Oscillator/Coppock Curve/Chandelier Exit -- three source keys
(Aroon_Oscillator, Coppock_Curve, Chandelier_Long) are deliberately renamed
onto differently-spelled/named dashboard columns, preserved exactly in the
``full_expected``/``partial_expected_present`` mappings below.

Deliberately targets the module-level functions directly rather than going
through ForecastingStep.run()/ProcessingStep.run() (which import
main_orchestrator and its full heavy engine chain at call time).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import pandas as pd
import pytest

from pipeline.production_steps import _apply_forecast_columns, _apply_options_columns

FORECAST_COLS = [
    'Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
    'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
    'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper',
]
OPTIONS_COLUMNS = (
    "GARCH_Vol", "Realized_Vol_Rank", "True_IVR", "VRP",
    "Aroon Oscillator", "Coppock Curve", "Chandelier Exit",
)


def _df(symbols):
    return pd.DataFrame({"Symbol": symbols})


@dataclass(frozen=True)
class WritebackCase:
    id: str
    apply: Callable[[pd.DataFrame, dict], None]
    columns: tuple
    full_source_symbol: str
    full_source: dict
    full_expected: dict
    partial_source_symbol: str
    partial_source: dict
    partial_expected_present: dict
    partial_expected_nan: tuple
    mixed_healthy_symbol: str
    mixed_dead_symbol: str
    mixed_healthy_source: dict
    mixed_healthy_expected_col: str
    mixed_healthy_expected_value: float


CASES = [
    WritebackCase(
        id="forecast",
        apply=lambda df, src: _apply_forecast_columns(df, src, FORECAST_COLS),
        columns=tuple(FORECAST_COLS),
        full_source_symbol="AAPL",
        full_source={"AAPL": {
            'Target_Days': 30, 'ARIMA': 190.0, 'MC_Target': 192.5,
            'MC_Lower': 175.0, 'MC_Upper': 210.0, 'Forecast_10': 188.0,
            'Forecast_30': 192.5, 'Forecast_60': 196.0, 'Forecast_90': 199.0,
            'Forecast_30_Prophet_Lower': 180.0, 'Forecast_30_Prophet_Upper': 205.0,
        }},
        full_expected={
            'Target_Days': 30, 'ARIMA': 190.0, 'MC_Target': 192.5,
            'MC_Lower': 175.0, 'MC_Upper': 210.0, 'Forecast_10': 188.0,
            'Forecast_30': 192.5, 'Forecast_60': 196.0, 'Forecast_90': 199.0,
            'Forecast_30_Prophet_Lower': 180.0, 'Forecast_30_Prophet_Upper': 205.0,
        },
        partial_source_symbol="NOPROPHET",
        partial_source={"NOPROPHET": {
            # Forecast_30_Prophet_Lower / _Upper deliberately absent (Prophet
            # unavailable/didn't produce output this cycle).
            'Target_Days': 60, 'ARIMA': 50.0, 'MC_Target': 51.0,
            'MC_Lower': 45.0, 'MC_Upper': 57.0, 'Forecast_10': 49.5,
            'Forecast_30': 51.0, 'Forecast_60': 52.5, 'Forecast_90': 54.0,
        }},
        partial_expected_present={'ARIMA': 50.0, 'MC_Target': 51.0, 'Forecast_90': 54.0},
        partial_expected_nan=('Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper'),
        mixed_healthy_symbol="OK",
        mixed_dead_symbol="DEADLETTERED",
        mixed_healthy_source={"OK": {
            'Target_Days': 30, 'ARIMA': 100.0, 'MC_Target': 101.0,
            'MC_Lower': 90.0, 'MC_Upper': 112.0, 'Forecast_10': 99.0,
            'Forecast_30': 101.0, 'Forecast_60': 103.0, 'Forecast_90': 105.0,
            'Forecast_30_Prophet_Lower': 95.0, 'Forecast_30_Prophet_Upper': 108.0,
        }},
        mixed_healthy_expected_col='Forecast_30',
        mixed_healthy_expected_value=101.0,
    ),
    WritebackCase(
        id="options",
        apply=lambda df, src: _apply_options_columns(df, src),
        columns=OPTIONS_COLUMNS,
        full_source_symbol="AAPL",
        full_source={"AAPL": {
            "GARCH_Vol": 0.25, "Realized_Vol_Rank": 60.0, "True_IVR": 55.0,
            "VRP": 0.03, "Aroon_Oscillator": 40.0, "Coppock_Curve": 12.0,
            "Chandelier_Long": 180.5,
        }},
        full_expected={
            "GARCH_Vol": 0.25, "Realized_Vol_Rank": 60.0, "True_IVR": 55.0,
            "VRP": 0.03, "Aroon Oscillator": 40.0, "Coppock Curve": 12.0,
            "Chandelier Exit": 180.5,
        },
        partial_source_symbol="PARTIAL",
        partial_source={"PARTIAL": {"GARCH_Vol": 0.30, "True_IVR": 45.0}},
        partial_expected_present={"GARCH_Vol": 0.30, "True_IVR": 45.0},
        partial_expected_nan=(
            "Realized_Vol_Rank", "VRP", "Aroon Oscillator", "Coppock Curve", "Chandelier Exit",
        ),
        mixed_healthy_symbol="OK",
        mixed_dead_symbol="FAILED",
        mixed_healthy_source={"OK": {
            "GARCH_Vol": 0.20, "Realized_Vol_Rank": 70.0, "True_IVR": 65.0,
            "VRP": 0.05, "Aroon_Oscillator": 20.0, "Coppock_Curve": 8.0,
            "Chandelier_Long": 99.0,
        }},
        mixed_healthy_expected_col='VRP',
        mixed_healthy_expected_value=0.05,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_full_source_populates_every_column(case):
    df = _df([case.full_source_symbol])
    case.apply(df, case.full_source)
    row = df.iloc[0]
    for col, expected in case.full_expected.items():
        assert row[col] == expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_missing_ticker_stays_nan_not_zero(case):
    """A ticker whose upstream step short-circuited/dead-lettered this
    cycle (absent from the source dict) must degrade every one of its
    columns to NaN -- never a fabricated 0.0 that would misread as a
    genuinely-computed zero."""
    df = _df(["__DEADLETTERED__"])
    case.apply(df, {})
    row = df.iloc[0]
    for col in case.columns:
        assert math.isnan(row[col]), f"{col} should be NaN, got {row[col]!r}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_partial_source_leaves_missing_keys_nan(case):
    """A ticker present in the source dict but missing an individual key
    must leave only that cell NaN -- successfully-computed siblings are
    unaffected."""
    df = _df([case.partial_source_symbol])
    case.apply(df, case.partial_source)
    row = df.iloc[0]
    for col, expected in case.partial_expected_present.items():
        assert row[col] == expected
    for col in case.partial_expected_nan:
        assert math.isnan(row[col])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_mixed_universe_only_deadlettered_ticker_is_nan(case):
    df = _df([case.mixed_healthy_symbol, case.mixed_dead_symbol])
    case.apply(df, case.mixed_healthy_source)
    healthy_row = df.loc[df["Symbol"] == case.mixed_healthy_symbol].iloc[0]
    dead_row = df.loc[df["Symbol"] == case.mixed_dead_symbol].iloc[0]
    assert healthy_row[case.mixed_healthy_expected_col] == case.mixed_healthy_expected_value
    for col in case.columns:
        assert math.isnan(dead_row[col])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_empty_universe_creates_columns_no_crash(case):
    df = _df([])
    case.apply(df, {})
    for col in case.columns:
        assert col in df.columns


def test_forecast_genuine_zero_is_preserved_not_confused_with_missing():
    """Forecast-specific (no options-columns equivalent existed in the
    original suite): a ticker that genuinely forecasts a near-zero value
    (e.g. MC_Lower for a penny stock) must keep that real 0.0 -- only
    ABSENCE (missing ticker or missing key) becomes NaN, not a computed
    zero."""
    df = _df(["PENNY"])
    forecast_results = {
        "PENNY": {
            'Target_Days': 10, 'ARIMA': 0.05, 'MC_Target': 0.04,
            'MC_Lower': 0.0, 'MC_Upper': 0.08, 'Forecast_10': 0.04,
            'Forecast_30': 0.03, 'Forecast_60': 0.02, 'Forecast_90': 0.01,
            'Forecast_30_Prophet_Lower': 0.0, 'Forecast_30_Prophet_Upper': 0.06,
        }
    }
    _apply_forecast_columns(df, forecast_results, FORECAST_COLS)

    row = df.iloc[0]
    assert row["MC_Lower"] == 0.0
    assert not math.isnan(row["MC_Lower"])
    assert row["Forecast_30_Prophet_Lower"] == 0.0
    assert not math.isnan(row["Forecast_30_Prophet_Lower"])
