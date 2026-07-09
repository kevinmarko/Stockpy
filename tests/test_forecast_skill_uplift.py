"""
tests/test_forecast_skill_uplift.py
=====================================
Skill-vs-static forecast **uplift experiment** for the (now production-wired)
inverse-RMSE skill weighting path (Tier 2.2).

`forecasting.forecast_tracker.ForecastTracker` is complete and lookahead-safe,
but until this sweep no production path injected it — skill weighting always
no-op'd to the static sector-preference blend. Agent 4 activated it behind the
opt-in ``settings.FORECAST_SKILL_WEIGHTING_ENABLED`` gate (default OFF) and
threaded a persistent tracker into every ``ForecastingEngine`` construction when
the flag is on.

This module is an **experiment, not a gate**: it runs ``generate_forecast`` WITH
a warmed tracker (skill-weighted blend) vs WITHOUT (tracker=None, static blend)
on the same synthetic history and asserts BOTH paths run end-to-end and produce
finite forecasts. The per-horizon realized-RMSE delta is reported as a printed
diagnostic — NOT a hard alpha assertion (real edge is data-dependent and cannot
be guaranteed on synthetic paths without overfitting the test).

The default-OFF contract IS asserted (fast, offline, unmarked) so a future
refactor that silently flips the default fails CI immediately.

Tracker-warming reuses the patterns in ``tests/test_forecast_tracker.py``.
"""

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest

from forecasting.forecast_tracker import (
    ForecastTracker,
    MODEL_ARIMA,
    MODEL_MONTE_CARLO,
    MODEL_HOLT_WINTERS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_history(n: int = 320, start: float = 100.0, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic OHLCV history with a mild upward drift + noise.

    Shape mirrors DataEngine.fetch_technical_raw(): tz-naive DatetimeIndex,
    columns [Open, High, Low, Close, Volume], sorted ascending.
    """
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(0.0004, 0.012, n)  # ~10%/yr drift, ~19%/yr vol
    closes = start * np.exp(np.cumsum(log_rets))
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": closes * 0.999,
            "High": closes * 1.006,
            "Low": closes * 0.994,
            "Close": closes,
            "Volume": np.full(n, 1_000_000),
        },
        index=idx,
    )


def _warm_tracker(tracker: ForecastTracker, symbol: str,
                  horizons: Tuple[int, ...] = (10, 30, 60, 90),
                  n_per_model: int = 35) -> None:
    """Populate ``forecast_errors`` with completed (actualized) rows so that
    ``get_skill_weights`` leaves cold-start for every horizon.

    ARIMA is given a small error edge over Monte Carlo / Holt-Winters so the
    resulting inverse-RMSE weights are non-uniform (the skill path is exercised,
    not just the equal-weight cold-start fallback). Mirrors
    tests/test_forecast_tracker.py::_fill_window.
    """
    base_price = 100.0
    for h in horizons:
        for i in range(n_per_model):
            ts = datetime.now(timezone.utc) - timedelta(days=h + 2 + i)
            tracker.record_forecasts(symbol, h, {
                MODEL_ARIMA: base_price + 0.2,        # near-perfect → low RMSE
                MODEL_MONTE_CARLO: base_price + 2.0,  # off by ~2 → higher RMSE
                MODEL_HOLT_WINTERS: base_price + 1.0,
            }, ts)
            tracker.update_actuals(symbol, h, base_price,
                                   datetime.now(timezone.utc), tolerance_days=5)


def _finite(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Fast, offline, unmarked — the default-OFF contract
# ---------------------------------------------------------------------------

class TestDefaultOffContract:
    def test_skill_weighting_disabled_by_default(self):
        """Opt-in gate: FORECAST_SKILL_WEIGHTING_ENABLED must default to False so
        a fresh clone / CI reproduces today's exact static-blend behavior."""
        from settings import Settings
        assert Settings().FORECAST_SKILL_WEIGHTING_ENABLED is False

    def test_settings_singleton_default_off(self):
        from settings import settings
        assert settings.FORECAST_SKILL_WEIGHTING_ENABLED is False

    def test_skill_window_exceeds_max_horizon(self):
        """The window must exceed the max forecast horizon (90d) or h=60/h=90
        can never leave cold-start (see settings.py docstring / task rationale)."""
        from settings import Settings
        assert Settings().FORECAST_SKILL_WINDOW_DAYS > 90

    def test_static_blend_runs_without_tracker(self):
        """tracker=None (the default construction) must produce finite forecasts —
        this is the byte-identical-to-today path that ships enabled."""
        from forecasting_engine import ForecastingEngine

        hist = _make_price_history()
        fe = ForecastingEngine()  # no tracker → static blend
        assert fe._tracker is None

        row = pd.Series({"Symbol": "SKILLTEST", "sector": "Technology"})
        current_price = float(hist["Close"].iloc[-1])
        out = fe.generate_forecast(
            row=row, current_price=current_price,
            history_series=hist["Close"], history_df=hist,
        )
        for key in ("Forecast_10", "Forecast_30", "Forecast_60", "Forecast_90"):
            assert _finite(out.get(key)), f"{key} not finite in static blend: {out.get(key)}"


# ---------------------------------------------------------------------------
# Skill-vs-static uplift experiment (heavier: real ARIMA/HW fits)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestSkillVsStaticUplift:
    def test_both_paths_produce_finite_forecasts_and_report_rmse_delta(self, tmp_path, capsys):
        """Run generate_forecast WITH a warmed tracker (skill blend) vs WITHOUT
        (static blend) on the same synthetic history. Assert both run end-to-end
        and produce finite forecasts; print the per-horizon realized-RMSE delta as
        a diagnostic. This is an experiment — NOT a hard alpha assertion."""
        from forecasting_engine import ForecastingEngine

        symbol = "SKILLTEST"
        full = _make_price_history(n=340, seed=11)

        # Walk-forward split: reserve the final `horizon` bars as realized future.
        # We forecast from the train endpoint and score each horizon against the
        # actual price `horizon` business days later where that data exists.
        train = full.iloc[:-90]  # hold out 90 bars so h<=90 has a realized actual
        current_price = float(train["Close"].iloc[-1])
        row = pd.Series({"Symbol": symbol, "sector": "Technology"})

        # Realized future actuals: price `h` business days after the train endpoint.
        future_closes = full["Close"].iloc[len(train):]
        realized_actual: Dict[int, float] = {}
        for h in (10, 30, 60, 90):
            if len(future_closes) >= h:
                realized_actual[h] = float(future_closes.iloc[h - 1])

        # --- Static blend (tracker=None) ---
        fe_static = ForecastingEngine()
        out_static = fe_static.generate_forecast(
            row=row, current_price=current_price,
            history_series=train["Close"], history_df=train,
        )

        # --- Skill blend (warmed in-memory tracker on a temp sqlite file) ---
        db_path = os.path.join(str(tmp_path), "skill_uplift.db")
        tracker = ForecastTracker(db_path=db_path)
        _warm_tracker(tracker, symbol)
        fe_skill = ForecastingEngine(tracker=tracker)
        assert fe_skill._tracker is tracker
        out_skill = fe_skill.generate_forecast(
            row=row, current_price=current_price,
            history_series=train["Close"], history_df=train,
        )

        # Both paths must produce finite forecasts at every horizon.
        for key in ("Forecast_10", "Forecast_30", "Forecast_60", "Forecast_90"):
            assert _finite(out_static.get(key)), f"static {key} not finite: {out_static.get(key)}"
            assert _finite(out_skill.get(key)), f"skill {key} not finite: {out_skill.get(key)}"

        # Diagnostic: per-horizon realized squared error, static vs skill.
        lines: List[str] = ["", "=== Forecast skill-vs-static realized-error diagnostic ==="]
        for h in (10, 30, 60, 90):
            key = f"Forecast_{h}"
            if h not in realized_actual:
                continue
            actual = realized_actual[h]
            se_static = (float(out_static[key]) - actual) ** 2
            se_skill = (float(out_skill[key]) - actual) ** 2
            delta = se_skill - se_static  # negative => skill is better
            lines.append(
                f"h={h:>2}d | actual={actual:8.2f} | static={float(out_static[key]):8.2f} "
                f"(SE={se_static:8.3f}) | skill={float(out_skill[key]):8.2f} "
                f"(SE={se_skill:8.3f}) | delta={delta:+8.3f}"
            )
        lines.append("(delta < 0 => skill-weighted blend had lower realized error this run)")
        report = "\n".join(lines)
        print(report)

        # No hard alpha gate — just confirm the diagnostic was produced, then
        # re-emit it outside pytest's capture so it is visible under `-s`.
        captured = capsys.readouterr()
        assert "skill-vs-static" in captured.out
        with capsys.disabled():
            print(report)

    def test_warmed_tracker_returns_non_cold_start_weights(self, tmp_path):
        """Sanity check that the warming helper actually leaves cold-start, so the
        skill path (not the equal-weight fallback) is genuinely exercised above."""
        db_path = os.path.join(str(tmp_path), "warm_check.db")
        tracker = ForecastTracker(db_path=db_path)
        _warm_tracker(tracker, "SKILLTEST")
        weights = tracker.get_skill_weights(
            "SKILLTEST", 30, window_days=180, min_obs=30
        )
        assert weights, "expected non-empty skill weights after warming"
        # ARIMA was given the accuracy edge → it must outweigh Monte Carlo.
        assert weights.get(MODEL_ARIMA, 0.0) > weights.get(MODEL_MONTE_CARLO, 0.0)
