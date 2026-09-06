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
            "Forecast_30": price * 1.03, "Forecast_30_Is_Fallback": False,
            "Forecast_60": price * 1.05, "Forecast_60_Is_Fallback": False,
            "Forecast_90": price * 1.07, "Forecast_90_Is_Fallback": True,
        }


class _FakeEngineBlowsUp:
    """Mimics generate_forecast() raising -- ForecastingStep's own except
    branch must build its coarse Monte-Carlo recovery AND honestly disclose
    every horizon of that recovery as a fallback."""

    def generate_forecast(self, *a, **k):
        raise RuntimeError("simulated ForecastingEngine failure")

    def run_monte_carlo(self, price, mu, sigma, days_forward):
        return price, price * 0.9, price * 1.1


class TestForecastingStepFallbackDisclosureHappyPath:
    def test_per_horizon_flags_round_trip_from_generate_forecast(self):
        df = pd.DataFrame({"Symbol": ["AAPL"], "Price": [150.0]})
        ForecastingStep().run(_ctx(df, _FakeEngineHealthy()))
        row = df.iloc[0]
        assert row["Forecast_10_Is_Fallback"] == False
        assert row["Forecast_30_Is_Fallback"] == False
        assert row["Forecast_60_Is_Fallback"] == False
        # Mixed within one ticker is a real, supported case -- horizons are
        # blended independently in generate_forecast's per-horizon loop.
        assert row["Forecast_90_Is_Fallback"] == True

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


class TestForecastingStepExceptionPathDisclosure:
    def test_engine_exception_marks_every_horizon_as_fallback(self):
        df = pd.DataFrame({"Symbol": ["MSFT"], "Price": [300.0]})
        ForecastingStep().run(_ctx(df, _FakeEngineBlowsUp()))
        row = df.iloc[0]
        for col in _FALLBACK_COLS:
            assert row[col] == True, f"{col} must be True after a ForecastingEngine exception"
        # The coarse Monte-Carlo recovery still produces real (non-NaN,
        # non-zero) prices -- only the disclosure flag changes here.
        assert row["Forecast_30"] == pytest.approx(300.0)
