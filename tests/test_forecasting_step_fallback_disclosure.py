"""
tests/test_forecasting_step_fallback_disclosure.py
====================================================
Coverage for the ``Forecast_{10,30,60,90}_Is_Fallback`` disclosure columns
``pipeline/production_steps.py::ForecastingStep`` writes into ``dashboard_df``
alongside ``Forecast_{10,30,60,90}``.

These columns exist so a downstream reader (state_snapshot.json, a future
webapp/GUI surface) can tell "ForecastingEngine.generate_forecast() had a
real model contribute this horizon's price" apart from "every model failed
and _blend_with_skill fell back to today's real current_price" -- the two
are numerically indistinguishable in Forecast_{h} alone (see
forecasting_engine.py::generate_forecast's own comment and
tests/test_forecasting_engine.py::TestGenerateForecast's
test_thin_history_falls_back_to_current_price_and_discloses_it for the
engine-level half of this contract).

Deliberately NOT registered in config.COLUMN_SCHEMA (Pandera's
DashboardSchema is non-strict, so this is a safe additive column) -- see
pipeline/production_steps.py::ForecastingStep's own comment for why.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from main_orchestrator import EngineContext
from pipeline.context import RunContext
from pipeline.production_steps import ForecastingStep

_FALLBACK_COLS = (
    "Forecast_10_Is_Fallback", "Forecast_30_Is_Fallback",
    "Forecast_60_Is_Fallback", "Forecast_90_Is_Fallback",
)


def _ctx(dashboard_df: pd.DataFrame, engine: object) -> RunContext:
    return RunContext(
        force_account=False,
        started_at=datetime.now(timezone.utc),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda: None,
        get_provider_fn=lambda: None,
        fetch_bars_fn=lambda *a, **k: {},
        build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
        symbols=[],
        dashboard_df=dashboard_df,
        tech_raw={},
        context_extras={},
        engine_context=EngineContext(forecasting_engine=engine),
    )


class _FakeEngineHealthy:
    """Mimics a real ForecastingEngine.generate_forecast() success -- a real
    model contributed every horizon (Is_Fallback=False everywhere)."""

    def generate_forecast(self, row, price, history_series, history_df=None,
                           precomputed_garch_term_structure=None):
        return {
            "Target_Days": 60, "ARIMA": price, "MC_Target": price,
            "MC_Lower": price * 0.9, "MC_Upper": price * 1.1,
            "Forecast_10": price * 1.01, "Forecast_10_Is_Fallback": False,
            "Forecast_10_Lower": price * 0.95, "Forecast_10_Upper": price * 1.05,
            "Forecast_30": price * 1.03, "Forecast_30_Is_Fallback": False,
            "Forecast_30_Lower": price * 0.90, "Forecast_30_Upper": price * 1.10,
            "Forecast_60": price * 1.05, "Forecast_60_Is_Fallback": False,
            "Forecast_60_Lower": price * 0.85, "Forecast_60_Upper": price * 1.15,
            "Forecast_90": price * 1.07, "Forecast_90_Is_Fallback": True,
            # Forecast_90_Lower / Forecast_90_Upper deliberately omitted (e.g.
            # MC band dropped by an F2 guard on h=90).
        }


class _FakeEngineBlowsUp:
    """Mimics generate_forecast() raising -- ForecastingStep's own except
    branch must build its coarse Monte-Carlo recovery AND honestly disclose
    every horizon of that recovery as a fallback."""

    def generate_forecast(self, *a, **k):
        raise RuntimeError("simulated ForecastingEngine failure")

    def run_monte_carlo(self, price, mu, sigma, days_forward):
        scale = days_forward / 100.0
        return price, price * (1.0 - scale), price * (1.0 + scale)


class _FakeEngineBlowsUpInvalidBands:
    """Mimics generate_forecast() raising while run_monte_carlo returns
    non-positive or non-finite bounds on certain horizons."""

    def generate_forecast(self, *a, **k):
        raise RuntimeError("simulated ForecastingEngine failure")

    def run_monte_carlo(self, price, mu, sigma, days_forward):
        if days_forward == 10:
            return price, 0.0, -5.0
        if days_forward == 30:
            return price, float("nan"), float("inf")
        if days_forward == 60:
            return price, price * 0.8, 0.0
        return price, -1.0, price * 1.2


class TestForecastingStepFallbackDisclosureHappyPath:
    def test_per_horizon_flags_and_bands_round_trip_from_generate_forecast(self):
        df = pd.DataFrame({"Symbol": ["AAPL"], "Price": [150.0]})
        ForecastingStep().run(_ctx(df, _FakeEngineHealthy()))
        row = df.iloc[0]
        assert row["Forecast_10_Is_Fallback"] == False
        assert row["Forecast_30_Is_Fallback"] == False
        assert row["Forecast_60_Is_Fallback"] == False
        # Mixed within one ticker is a real, supported case -- horizons are
        # blended independently in generate_forecast's per-horizon loop.
        assert row["Forecast_90_Is_Fallback"] == True
        # Present bands copied onto dashboard_df; omitted h=90 bands stay NaN (not 0.0).
        assert row["Forecast_10_Lower"] == pytest.approx(150.0 * 0.95)
        assert row["Forecast_10_Upper"] == pytest.approx(150.0 * 1.05)
        assert row["Forecast_30_Lower"] == pytest.approx(150.0 * 0.90)
        assert row["Forecast_30_Upper"] == pytest.approx(150.0 * 1.10)
        assert row["Forecast_60_Lower"] == pytest.approx(150.0 * 0.85)
        assert row["Forecast_60_Upper"] == pytest.approx(150.0 * 1.15)
        assert pd.isna(row["Forecast_90_Lower"]) and row["Forecast_90_Lower"] != 0.0
        assert pd.isna(row["Forecast_90_Upper"]) and row["Forecast_90_Upper"] != 0.0

    def test_ticker_skipped_for_zero_price_stays_nan_not_false(self):
        """A row _forecast_one() never reaches (Price 0/missing) must NOT
        report Is_Fallback=False -- that would be an active false claim that
        a real model ran. It must stay NaN, matching every other
        forecast_cols column's own NaN-fill-first contract (CONSTRAINT #4)."""
        df = pd.DataFrame({"Symbol": ["DEADSYM"], "Price": [0.0]})
        ForecastingStep().run(_ctx(df, _FakeEngineHealthy()))
        row = df.iloc[0]
        for col in _FALLBACK_COLS:
            assert pd.isna(row[col]), f"{col} must be NaN for a dead-lettered row, got {row[col]!r}"
        for h in (10, 30, 60, 90):
            for side in ("Lower", "Upper"):
                col = f"Forecast_{h}_{side}"
                assert pd.isna(row[col]), f"{col} must be NaN for a dead-lettered row, got {row[col]!r}"


class TestForecastingStepExceptionPathDisclosure:
    def test_engine_exception_marks_every_horizon_as_fallback_and_writes_positive_finite_bands(self):
        df = pd.DataFrame({"Symbol": ["MSFT"], "Price": [300.0]})
        ForecastingStep().run(_ctx(df, _FakeEngineBlowsUp()))
        row = df.iloc[0]
        for col in _FALLBACK_COLS:
            assert row[col] == True, f"{col} must be True after a ForecastingEngine exception"
        # The coarse Monte-Carlo recovery still produces real (non-NaN,
        # non-zero) prices -- only the disclosure flag changes here.
        assert row["Forecast_30"] == pytest.approx(300.0)
        for h in (10, 30, 60, 90):
            scale = h / 100.0
            assert row[f"Forecast_{h}_Lower"] == pytest.approx(300.0 * (1.0 - scale))
            assert row[f"Forecast_{h}_Upper"] == pytest.approx(300.0 * (1.0 + scale))

    def test_engine_exception_omits_non_positive_or_non_finite_bands(self):
        df = pd.DataFrame({"Symbol": ["MSFT"], "Price": [300.0]})
        ForecastingStep().run(_ctx(df, _FakeEngineBlowsUpInvalidBands()))
        row = df.iloc[0]
        # h=10: (0.0, -5.0) -> both omitted (NaN)
        assert pd.isna(row["Forecast_10_Lower"])
        assert pd.isna(row["Forecast_10_Upper"])
        # h=30: (nan, inf) -> both omitted (NaN)
        assert pd.isna(row["Forecast_30_Lower"])
        assert pd.isna(row["Forecast_30_Upper"])
        # h=60: (240.0, 0.0) -> lower copied, upper NaN
        assert row["Forecast_60_Lower"] == pytest.approx(240.0)
        assert pd.isna(row["Forecast_60_Upper"])
        # h=90: (-1.0, 360.0) -> lower NaN, upper copied
        assert pd.isna(row["Forecast_90_Lower"])
        assert row["Forecast_90_Upper"] == pytest.approx(360.0)
