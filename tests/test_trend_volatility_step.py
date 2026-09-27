"""
tests/test_trend_volatility_step.py
===================================
Regression coverage for pipeline/production_steps.py::TrendVolatilityStep's
``_trend_vol_one`` closure (the per-ticker half of the old
``OptionsAnalysisStep``, split out in 2026-09, step 3d).

A ticker with too little price history (< 22 rows) to measure ANY volatility
figure makes ``GarchVolatilityEstimator.estimate_gjr_garch_volatility_term_structure``
return ``None`` (CONSTRAINT #4). That must degrade ``GARCH_Vol`` to ``NaN``
while the Aroon/Coppock/Chandelier indicators (which don't depend on GARCH)
are still computed -- the whole per-ticker entry must never be dropped.
Originally fixed in the options step; see
docs/known_issues/forecast_ito_double_correction_and_horizon_units.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from data_engine import MockDataEngine
from macro_engine import MacroEngine
from main_orchestrator import EngineContext
from pipeline.context import RunContext
from pipeline.production_steps import TrendVolatilityStep


class _FakeFred:
    def __init__(self, series_map=None):
        self._series_map = series_map or {}

    def get_series(self, series_id, limit=None):
        return self._series_map.get(series_id, pd.Series(dtype=float))


class _FakeEngineWithFred:
    def __init__(self, fred):
        self.fred = fred


def _short_ohlcv(n: int, seed: int = 1) -> pd.DataFrame:
    """Fewer than 22 rows -- too little history for
    estimate_gjr_garch_volatility_term_structure to measure ANYTHING (GARCH
    or the historical-stdev fallback), so it returns None."""
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    rng = np.random.RandomState(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    return pd.DataFrame(
        {
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


def _make_ctx(symbols, tech_raw) -> RunContext:
    fred = _FakeFred(series_map={"SAHMREALTIME": pd.Series([0.1, 0.2, 0.15])})
    me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
    macro_raw = {"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0, "VIXCLS": 16.0}
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
        symbols=symbols,
        market=None,
        tech_raw=tech_raw,
        macro_raw=macro_raw,
        engine_context=EngineContext(macro_engine=me),
    )


class TestTrendVolOneSurvivesInsufficientGarchHistory:
    def test_short_history_ticker_still_gets_a_result_not_dropped_entirely(self):
        """The regression: a ticker with < 22 rows of history must still
        produce a trend_vol_indicators entry (Aroon/Coppock/Chandelier), not
        be silently absent from the dict because GARCH alone couldn't be
        measured."""
        ctx = _make_ctx(["SHORTHIST"], {"SHORTHIST": _short_ohlcv(15)})

        TrendVolatilityStep().run(ctx)  # must not raise

        trend_vol = ctx.context_extras["trend_vol_indicators"]
        assert "SHORTHIST" in trend_vol, (
            "ticker was dropped entirely instead of degrading GARCH_Vol to NaN"
        )
        result = trend_vol["SHORTHIST"]
        assert math.isnan(result["GARCH_Vol"])
        # The options-only outputs are no longer produced at all.
        for gone in ("VRP", "True_IVR", "Realized_Vol_Rank", "Option_Strategy_Matrix"):
            assert gone not in result
        # Indicators independent of GARCH must still be real, finite floats
        # -- proving the rest of the per-ticker computation actually ran.
        for key in ("Aroon_Oscillator", "Coppock_Curve", "Chandelier_Long", "Chandelier_Short"):
            assert math.isfinite(result[key]), f"{key} should still be computed"

    def test_garch_term_structures_extras_has_no_entry_for_the_short_ticker(self):
        """garch_term_structures (threaded to ForecastingStep for the
        per-horizon Monte Carlo sigma) must simply omit a ticker whose GARCH
        term structure is None, rather than storing a None value or crashing."""
        ctx = _make_ctx(["SHORTHIST"], {"SHORTHIST": _short_ohlcv(15)})
        TrendVolatilityStep().run(ctx)
        garch_term_structures = ctx.context_extras.get("garch_term_structures", {})
        assert "SHORTHIST" not in garch_term_structures

    def test_sufficient_history_ticker_unaffected_by_the_fix(self):
        """A ticker with plenty of history still gets a real, finite
        GARCH_Vol -- the fix only changes the < 22-row degenerate path."""
        ctx = _make_ctx(["LONGHIST"], {"LONGHIST": _short_ohlcv(150, seed=7)})
        TrendVolatilityStep().run(ctx)
        result = ctx.context_extras["trend_vol_indicators"]["LONGHIST"]
        assert math.isfinite(result["GARCH_Vol"])
        assert result["GARCH_Vol"] > 0.0
