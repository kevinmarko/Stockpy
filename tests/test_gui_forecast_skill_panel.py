"""
tests/test_gui_forecast_skill_panel.py — PR1 Forecast Skill panel + Settings gaps
=================================================================================
Offline unit tests for the Command Center additions that surface forecast-skill
accuracy and fill the Settings-Manager widget gaps.

* ``_forecast_rmse_by_model`` (the read-only per-model RMSE helper behind the new
  Observability "Forecast Skill" sub-section) — verified against BOTH an empty DB
  (degrades to ``{}``, never raises — CONSTRAINT #6) and a warmed DB (returns real
  per-model RMSE, proving the query column names actually match the
  ``forecast_errors`` schema and it is not silently returning empty).
* The 8 previously-invisible settings keys are now in ``_SETTINGS_LAYOUT`` AND in
  ``gui.env_io.ALLOWED_KEYS`` (so a Settings-Manager write can't raise
  ``DisallowedKeyError``).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from forecasting.forecast_tracker import ForecastTracker
from gui.panels.observability import _forecast_rmse_by_model
from gui.panels.settings_manager import _SETTINGS_LAYOUT
from gui import env_io


# ── The 8 settings gaps we just wired ────────────────────────────────────────

_NEWLY_EXPOSED_KEYS = {
    "FORECAST_SKILL_WEIGHTING_ENABLED",
    "FORECAST_SKILL_WINDOW_DAYS",
    "SECTOR_FORECAST_CONFIG_PATH",
    "SECTOR_FORECAST_CONFIGS",
    "PROMPT_REGISTRY_ENABLED",
    "PROMPT_REGISTRY_BACKEND",
    "ORCHESTRATOR_DAEMON_ENABLED",
    "CORS_ALLOWED_ORIGINS",
}


def test_newly_exposed_keys_have_a_widget_and_are_allowlisted():
    layout_keys = {key for key, _kind in _SETTINGS_LAYOUT}
    for key in _NEWLY_EXPOSED_KEYS:
        assert key in layout_keys, f"{key} missing a Settings-Manager widget"
        assert key in env_io.ALLOWED_KEYS, f"{key} not allowlisted — write would raise"


def test_json_widget_kind_used_for_structured_keys():
    layout = dict(_SETTINGS_LAYOUT)
    # The two JSON-structured settings must use the new "json" widget kind so
    # env_io receives a Python object to JSON-encode (they are in _JSON_KEYS).
    assert layout["CORS_ALLOWED_ORIGINS"] == "json"
    assert layout["SECTOR_FORECAST_CONFIGS"] == "json"


# ── _forecast_rmse_by_model ──────────────────────────────────────────────────

def test_rmse_helper_empty_db_returns_empty(tmp_path):
    """No table / no rows → {} (never raises — dead-letter, CONSTRAINT #6)."""
    db = str(tmp_path / "empty.db")
    assert _forecast_rmse_by_model(db, "AAPL", 30, window_days=180) == {}


def test_rmse_helper_missing_db_creates_no_file(tmp_path):
    """DATABASE-LEVEL read-only (mode=ro): a missing DB degrades to {} WITHOUT
    creating a stray empty DB file as a side effect of rendering the panel."""
    db = tmp_path / "absent.db"
    assert _forecast_rmse_by_model(str(db), "AAPL", 30, window_days=180) == {}
    assert not db.exists()


def test_rmse_helper_returns_real_per_model_rmse(tmp_path):
    """A warmed DB yields finite per-model RMSE — proves the query column names
    match the forecast_errors schema (not silently returning {})."""
    db = str(tmp_path / "warm.db")
    tracker = ForecastTracker(db_path=db)

    # Forecast 30 days ago so the horizon has elapsed and update_actuals matches.
    fcast_ts = datetime.now(timezone.utc) - timedelta(days=30)
    as_of = datetime.now(timezone.utc)

    # Single row per model → RMSE == |forecast - actual|, deterministic.
    #   arima: forecast 110, actual 100 → RMSE 10
    #   monte_carlo: forecast 96, actual 100 → RMSE 4
    tracker.record_forecasts("AAPL", 30, {"arima": 110.0, "monte_carlo": 96.0}, fcast_ts)
    matched = tracker.update_actuals("AAPL", 30, actual_price=100.0, as_of=as_of)
    assert matched >= 1, "update_actuals matched no rows — fixture timing is off"

    rmse = _forecast_rmse_by_model(db, "AAPL", 30, window_days=180)
    assert set(rmse) == {"arima", "monte_carlo"}
    assert rmse["arima"] == pytest.approx(10.0, abs=1e-6)
    assert rmse["monte_carlo"] == pytest.approx(4.0, abs=1e-6)
    assert all(math.isfinite(v) for v in rmse.values())


def test_rmse_helper_excludes_unactualized_rows(tmp_path):
    """A recorded-but-not-yet-actualized forecast contributes no RMSE (its
    squared_error is NULL) — the panel must not fabricate accuracy."""
    db = str(tmp_path / "pending.db")
    tracker = ForecastTracker(db_path=db)
    tracker.record_forecasts(
        "MSFT", 30, {"arima": 300.0}, datetime.now(timezone.utc) - timedelta(days=1)
    )
    # No update_actuals → squared_error IS NULL → excluded.
    assert _forecast_rmse_by_model(db, "MSFT", 30, window_days=180) == {}
