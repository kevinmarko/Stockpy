"""
tests/test_forecast_tracker.py
================================
Unit tests for ``forecasting.forecast_tracker.ForecastTracker`` (Tier 2.2).

Covers:
* Table creation on first init (DDL).
* record / update_actuals / get_skill_weights lifecycle.
* Cold-start: fewer than ``min_obs`` observations → equal weights.
* Warm path: inverse-RMSE weighting (better model gets higher weight).
* ``_MIN_RMSE`` guard prevents division-by-zero on perfect predictions.
* Missing file / corrupt DB → graceful degradation (returns {}, 0, never raises).
* Tolerance window boundary: forecast due 5 days early still actualized.
* ``update_actuals`` only touches unactualized rows (idempotency).
* ``pending_count`` and ``completed_count`` return correct values.
* ``ForecastingEngine.__init__`` accepts a ``tracker`` keyword argument.
* ``_blend_with_skill`` static method: skill path and static fallback path.
"""

import math
import os
import tempfile
from datetime import datetime, timedelta
from typing import Dict
from unittest import mock

import pytest

from forecasting.forecast_tracker import (
    ForecastTracker,
    MODEL_ARIMA,
    MODEL_MONTE_CARLO,
    MODEL_HOLT_WINTERS,
    MODEL_CNN_LSTM,
    ALL_MODEL_NAMES,
    _MIN_RMSE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(tmp_path) -> ForecastTracker:
    db = os.path.join(str(tmp_path), "test_tracker.db")
    return ForecastTracker(db_path=db)


def _record(tracker: ForecastTracker, symbol="AAPL", horizon=30, **model_prices):
    """Helper to record a set of model prices at a given timestamp."""
    ts = datetime.utcnow() - timedelta(days=horizon + 1)  # already past horizon
    tracker.record_forecasts(symbol, horizon, dict(model_prices), ts)
    return ts


def _fill_window(tracker: ForecastTracker, symbol: str, horizon: int, n: int,
                 actual: float, arima_delta: float = 0.0, mc_delta: float = 0.5) -> None:
    """Insert ``n`` completed observations with controlled errors."""
    base_price = 100.0
    for i in range(n):
        ts = datetime.utcnow() - timedelta(days=horizon + 2 + i)
        tracker.record_forecasts(symbol, horizon, {
            MODEL_ARIMA: base_price + arima_delta,
            MODEL_MONTE_CARLO: base_price + mc_delta,
        }, ts)
        tracker.update_actuals(symbol, horizon, base_price, datetime.utcnow(), tolerance_days=5)


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

class TestTableCreation:
    def test_table_created_on_init(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        import sqlite3
        db = os.path.join(str(tmp_path), "test_tracker.db")
        with sqlite3.connect(db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='forecast_errors'"
            )
            row = cursor.fetchone()
        assert row is not None, "forecast_errors table was not created"

    def test_table_has_required_columns(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        import sqlite3
        db = os.path.join(str(tmp_path), "test_tracker.db")
        with sqlite3.connect(db) as conn:
            cursor = conn.execute("PRAGMA table_info(forecast_errors)")
            cols = {r[1] for r in cursor.fetchall()}
        required = {
            "id", "symbol", "model_name", "horizon_days", "forecast_ts",
            "forecast_price", "actual_price", "squared_error", "recorded_at",
        }
        assert required <= cols

    def test_index_created(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        import sqlite3
        db = os.path.join(str(tmp_path), "test_tracker.db")
        with sqlite3.connect(db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_fe_symbol_model_horizon'"
            )
            row = cursor.fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# record_forecasts
# ---------------------------------------------------------------------------

class TestRecordForecasts:
    def test_records_positive_prices(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow()
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0, MODEL_MONTE_CARLO: 152.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 2

    def test_skips_zero_and_negative_prices(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow()
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 0.0, MODEL_MONTE_CARLO: -5.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 0

    def test_symbol_uppercased(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow()
        tracker.record_forecasts("aapl", 30, {MODEL_ARIMA: 150.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 1

    def test_does_not_raise_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        # Should not raise; logs a warning
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, datetime.utcnow())


# ---------------------------------------------------------------------------
# update_actuals
# ---------------------------------------------------------------------------

class TestUpdateActuals:
    def test_actualizes_past_due_forecasts(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow() - timedelta(days=35)  # 35 days ago, horizon 30
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        n = tracker.update_actuals("AAPL", 30, 155.0, datetime.utcnow(), tolerance_days=5)
        assert n == 1

    def test_does_not_actualize_recent_forecasts(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow() - timedelta(days=10)  # only 10 days ago, horizon 30
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        n = tracker.update_actuals("AAPL", 30, 155.0, datetime.utcnow(), tolerance_days=5)
        assert n == 0

    def test_tolerance_window_boundary(self, tmp_path):
        """A forecast at horizon-tolerance_days should be actualized."""
        tracker = _make_tracker(tmp_path)
        horizon, tol = 30, 5
        # Made exactly (horizon - tolerance) days ago → on the boundary, should actualize
        ts = datetime.utcnow() - timedelta(days=horizon - tol)
        tracker.record_forecasts("AAPL", horizon, {MODEL_ARIMA: 150.0}, ts)
        n = tracker.update_actuals("AAPL", horizon, 155.0, datetime.utcnow(), tolerance_days=tol)
        assert n == 1

    def test_idempotent_already_actualized(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow() - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        n1 = tracker.update_actuals("AAPL", 30, 155.0, datetime.utcnow())
        n2 = tracker.update_actuals("AAPL", 30, 155.0, datetime.utcnow())
        assert n1 == 1
        assert n2 == 0  # already actualized → no rows updated

    def test_squared_error_written_correctly(self, tmp_path):
        import sqlite3
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow() - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        tracker.update_actuals("AAPL", 30, 160.0, datetime.utcnow())
        with sqlite3.connect(tracker._db_path) as conn:
            row = conn.execute(
                "SELECT squared_error FROM forecast_errors WHERE model_name='arima'"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 100.0) < 0.01  # (160 - 150)^2 = 100

    def test_does_not_raise_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        result = tracker.update_actuals("AAPL", 30, 155.0, datetime.utcnow())
        assert result == 0


# ---------------------------------------------------------------------------
# get_skill_weights
# ---------------------------------------------------------------------------

class TestGetSkillWeights:
    def test_empty_when_no_history(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        weights = tracker.get_skill_weights("AAPL", 30, window_days=60, min_obs=30)
        assert weights == {}

    def test_cold_start_equal_weights_below_min_obs(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        # Insert only 5 completed rows (below min_obs=30)
        _fill_window(tracker, "AAPL", 30, n=5, actual=100.0)
        weights = tracker.get_skill_weights("AAPL", 30, window_days=60, min_obs=30)
        # Both models are present → equal weights
        assert len(weights) == 2
        for w in weights.values():
            assert abs(w - 0.5) < 1e-9

    def test_warm_path_at_exactly_min_obs(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        # Exactly 30 rows per model → warm path activates.
        # Use a wide window (180 days) so all 30 inserted rows are within the window
        # regardless of how far back _fill_window stamps them.
        _fill_window(tracker, "AAPL", 30, n=30, actual=100.0, arima_delta=0.0, mc_delta=2.0)
        weights = tracker.get_skill_weights("AAPL", 30, window_days=180, min_obs=30)
        # ARIMA (perfect prediction, RMSE→clamped) should outweigh MC (off by 2, RMSE=2)
        assert MODEL_ARIMA in weights
        assert MODEL_MONTE_CARLO in weights
        assert weights[MODEL_ARIMA] > weights[MODEL_MONTE_CARLO]

    def test_warm_path_weights_sum_to_one(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _fill_window(tracker, "AAPL", 30, n=35, actual=100.0, arima_delta=0.5, mc_delta=2.0)
        weights = tracker.get_skill_weights("AAPL", 30, window_days=180, min_obs=30)
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_min_rmse_guard_applied(self, tmp_path):
        """Perfect model (RMSE=0) should not get infinite weight (clamped to _MIN_RMSE)."""
        tracker = _make_tracker(tmp_path)
        # arima_delta=0 → perfect prediction (RMSE=0)
        _fill_window(tracker, "AAPL", 30, n=35, actual=100.0, arima_delta=0.0, mc_delta=5.0)
        weights = tracker.get_skill_weights("AAPL", 30, window_days=180, min_obs=30)
        # All weights should be valid floats in (0, 1)
        for w in weights.values():
            assert 0.0 < w <= 1.0
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_returns_empty_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        weights = tracker.get_skill_weights("AAPL", 30)
        assert weights == {}

    def test_window_excludes_old_rows(self, tmp_path):
        """Rows older than window_days should not count toward skill."""
        tracker = _make_tracker(tmp_path)
        import sqlite3
        # Manually insert a completed row with forecast_ts older than 60 days
        old_ts = (datetime.utcnow() - timedelta(days=90)).isoformat()
        now_iso = datetime.utcnow().isoformat()
        with sqlite3.connect(tracker._db_path) as conn:
            conn.execute(
                "INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts, "
                "forecast_price, actual_price, squared_error, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("AAPL", MODEL_ARIMA, 30, old_ts, 150.0, 155.0, 25.0, now_iso),
            )
            conn.commit()
        weights = tracker.get_skill_weights("AAPL", 30, window_days=60, min_obs=1)
        assert weights == {}  # row is outside the window


# ---------------------------------------------------------------------------
# pending_count and completed_count
# ---------------------------------------------------------------------------

class TestCountHelpers:
    def test_pending_count_increases_on_record(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow()
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0, MODEL_MONTE_CARLO: 151.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 2

    def test_pending_decreases_after_actualize(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.utcnow() - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        tracker.update_actuals("AAPL", 30, 155.0, datetime.utcnow())
        assert tracker.pending_count("AAPL", 30) == 0

    def test_completed_count_increases_after_actualize(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        # _fill_window records 2 models (arima + mc) per iteration
        # so n=5 iterations → 10 completed rows total
        _fill_window(tracker, "AAPL", 30, n=5, actual=100.0)
        assert tracker.completed_count("AAPL", 30, window_days=180) == 10

    def test_pending_count_returns_zero_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        assert tracker.pending_count("AAPL", 30) == 0

    def test_completed_count_returns_zero_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        assert tracker.completed_count("AAPL", 30) == 0


# ---------------------------------------------------------------------------
# ForecastingEngine integration
# ---------------------------------------------------------------------------

class TestForecastingEngineIntegration:
    def test_init_accepts_tracker_kwarg(self, tmp_path):
        """ForecastingEngine.__init__ must accept an optional tracker parameter."""
        from forecasting_engine import ForecastingEngine
        tracker = _make_tracker(tmp_path)
        fe = ForecastingEngine(tracker=tracker)
        assert fe._tracker is tracker

    def test_init_default_tracker_is_none(self):
        from forecasting_engine import ForecastingEngine
        fe = ForecastingEngine()
        assert fe._tracker is None

    def test_init_rejects_non_tracker_object(self):
        from forecasting_engine import ForecastingEngine
        fe = ForecastingEngine(tracker="not-a-tracker")
        assert fe._tracker is None


# ---------------------------------------------------------------------------
# _blend_with_skill static method
# ---------------------------------------------------------------------------

class TestBlendWithSkill:
    from forecasting_engine import ForecastingEngine as _FE

    def test_skill_weighted_blend_uses_provided_weights(self):
        from forecasting_engine import ForecastingEngine
        model_forecasts = {"arima": 100.0, "monte_carlo": 110.0}
        skill_weights = {"arima": 0.8, "monte_carlo": 0.2}
        result = ForecastingEngine._blend_with_skill(model_forecasts, skill_weights, "MC", 105.0)
        expected = 100.0 * 0.8 + 110.0 * 0.2
        assert abs(result - expected) < 1e-6

    def test_skill_blend_normalizes_uneven_weights(self):
        from forecasting_engine import ForecastingEngine
        model_forecasts = {"arima": 100.0, "monte_carlo": 110.0}
        # Weights don't sum to 1 — should be normalized
        skill_weights = {"arima": 4.0, "monte_carlo": 1.0}
        result = ForecastingEngine._blend_with_skill(model_forecasts, skill_weights, "MC", 105.0)
        expected = 100.0 * 0.8 + 110.0 * 0.2
        assert abs(result - expected) < 1e-6

    def test_empty_skill_weights_falls_back_to_static(self):
        from forecasting_engine import ForecastingEngine
        # preferred_model=ARIMA and a_res=90 → static path returns arima price
        model_forecasts = {"arima": 90.0, "monte_carlo": 100.0}
        result = ForecastingEngine._blend_with_skill(model_forecasts, {}, "ARIMA", 95.0)
        assert result == 90.0

    def test_no_model_forecasts_returns_current_price(self):
        from forecasting_engine import ForecastingEngine
        result = ForecastingEngine._blend_with_skill({}, {}, "MC", 123.45)
        assert result == 123.45

    def test_skill_weights_restrict_to_known_models(self):
        """Skill weights for models not in model_forecasts are ignored."""
        from forecasting_engine import ForecastingEngine
        model_forecasts = {"arima": 100.0}
        skill_weights = {"arima": 0.5, "cnn_lstm": 0.5}  # cnn_lstm not in forecasts
        result = ForecastingEngine._blend_with_skill(model_forecasts, skill_weights, "MC", 90.0)
        # Only arima contributes → weight normalized to 1.0 → result == arima price
        assert abs(result - 100.0) < 1e-6

    def test_hw_preferred_static_fallback(self):
        from forecasting_engine import ForecastingEngine
        model_forecasts = {"holt_winters": 105.0, "arima": 100.0, "monte_carlo": 110.0}
        result = ForecastingEngine._blend_with_skill(model_forecasts, {}, "HW", 107.0)
        assert result == 105.0


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_all_model_names_contains_four_entries(self):
        assert len(ALL_MODEL_NAMES) == 4

    def test_model_name_constants_are_strings(self):
        for name in ALL_MODEL_NAMES:
            assert isinstance(name, str)

    def test_min_rmse_positive(self):
        assert _MIN_RMSE > 0

    def test_forecast_tracker_importable_from_package(self):
        from forecasting import ForecastTracker as FT  # noqa: F401
        assert FT is ForecastTracker
