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
from datetime import datetime, timedelta, timezone
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
    ts = datetime.now(timezone.utc) - timedelta(days=horizon + 1)  # already past horizon
    tracker.record_forecasts(symbol, horizon, dict(model_prices), ts)
    return ts


def _fill_window(tracker: ForecastTracker, symbol: str, horizon: int, n: int,
                 actual: float, arima_delta: float = 0.0, mc_delta: float = 0.5) -> None:
    """Insert ``n`` completed observations with controlled errors."""
    base_price = 100.0
    for i in range(n):
        ts = datetime.now(timezone.utc) - timedelta(days=horizon + 2 + i)
        tracker.record_forecasts(symbol, horizon, {
            MODEL_ARIMA: base_price + arima_delta,
            MODEL_MONTE_CARLO: base_price + mc_delta,
        }, ts)
        tracker.update_actuals(symbol, horizon, base_price, datetime.now(timezone.utc), tolerance_days=5)


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
        ts = datetime.now(timezone.utc)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0, MODEL_MONTE_CARLO: 152.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 2

    def test_skips_zero_and_negative_prices(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 0.0, MODEL_MONTE_CARLO: -5.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 0

    def test_symbol_uppercased(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc)
        tracker.record_forecasts("aapl", 30, {MODEL_ARIMA: 150.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 1

    def test_does_not_raise_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        # Should not raise; logs a warning
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# update_actuals
# ---------------------------------------------------------------------------

class TestUpdateActuals:
    def test_actualizes_past_due_forecasts(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc) - timedelta(days=35)  # 35 days ago, horizon 30
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        n = tracker.update_actuals("AAPL", 30, 155.0, datetime.now(timezone.utc), tolerance_days=5)
        assert n == 1

    def test_does_not_actualize_recent_forecasts(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc) - timedelta(days=10)  # only 10 days ago, horizon 30
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        n = tracker.update_actuals("AAPL", 30, 155.0, datetime.now(timezone.utc), tolerance_days=5)
        assert n == 0

    def test_tolerance_window_boundary(self, tmp_path):
        """A forecast at horizon-tolerance_days should be actualized."""
        tracker = _make_tracker(tmp_path)
        horizon, tol = 30, 5
        # Made exactly (horizon - tolerance) days ago → on the boundary, should actualize
        ts = datetime.now(timezone.utc) - timedelta(days=horizon - tol)
        tracker.record_forecasts("AAPL", horizon, {MODEL_ARIMA: 150.0}, ts)
        n = tracker.update_actuals("AAPL", horizon, 155.0, datetime.now(timezone.utc), tolerance_days=tol)
        assert n == 1

    def test_idempotent_already_actualized(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc) - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        n1 = tracker.update_actuals("AAPL", 30, 155.0, datetime.now(timezone.utc))
        n2 = tracker.update_actuals("AAPL", 30, 155.0, datetime.now(timezone.utc))
        assert n1 == 1
        assert n2 == 0  # already actualized → no rows updated

    def test_squared_error_written_correctly(self, tmp_path):
        import sqlite3
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc) - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        tracker.update_actuals("AAPL", 30, 160.0, datetime.now(timezone.utc))
        with sqlite3.connect(tracker._db_path) as conn:
            row = conn.execute(
                "SELECT squared_error FROM forecast_errors WHERE model_name='arima'"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 100.0) < 0.01  # (160 - 150)^2 = 100

    def test_does_not_raise_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        result = tracker.update_actuals("AAPL", 30, 155.0, datetime.now(timezone.utc))
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
        old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
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
# get_error_by_model
# ---------------------------------------------------------------------------

class TestGetErrorByModel:
    def test_empty_when_no_history(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        rows = tracker.get_error_by_model("AAPL", 30, window_days=60)
        assert rows == []

    def test_rmse_and_mae_computed_correctly(self, tmp_path):
        """Known, hand-computable errors: 3 rows off by exactly +10, +10, -10
        (actual=110/110/90 vs forecast=100 each time) -> squared_error =
        100/100/100 -> RMSE = 10; abs_error = 10/10/10 -> MAE = 10. RMSE and
        MAE agree here only because every error has the same magnitude —
        proves both are computed from the persisted price columns (no
        migration), not just RMSE."""
        tracker = _make_tracker(tmp_path)
        for actual in (110.0, 110.0, 90.0):
            ts = datetime.now(timezone.utc) - timedelta(days=35)
            tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, ts)
            tracker.update_actuals("AAPL", 30, actual, datetime.now(timezone.utc))

        rows = tracker.get_error_by_model("AAPL", 30, window_days=180)
        assert len(rows) == 1
        row = rows[0]
        assert row["model_name"] == MODEL_ARIMA
        assert row["n"] == 3
        assert row["rmse"] == pytest.approx(10.0, abs=1e-6)
        assert row["mae"] == pytest.approx(10.0, abs=1e-6)

    def test_mae_diverges_from_rmse_with_mixed_error_magnitudes(self, tmp_path):
        """RMSE penalizes large errors more than MAE — errors of 0, 0, 30
        give MAE=10 (simple average) but RMSE=sqrt(900/3)=~17.3 (quadratic
        average). If MAE were accidentally computed as sqrt(mse) instead of
        its own AVG(ABS(...)), this would fail."""
        tracker = _make_tracker(tmp_path)
        for actual in (100.0, 100.0, 130.0):
            ts = datetime.now(timezone.utc) - timedelta(days=35)
            tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, ts)
            tracker.update_actuals("AAPL", 30, actual, datetime.now(timezone.utc))

        rows = tracker.get_error_by_model("AAPL", 30, window_days=180)
        assert len(rows) == 1
        row = rows[0]
        assert row["mae"] == pytest.approx(10.0, abs=1e-6)
        assert row["rmse"] == pytest.approx(17.32, abs=0.01)
        assert row["rmse"] != pytest.approx(row["mae"], abs=1e-6)

    def test_sorted_ascending_by_rmse_best_first(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _fill_window(tracker, "AAPL", 30, n=35, actual=100.0, arima_delta=0.0, mc_delta=5.0)
        rows = tracker.get_error_by_model("AAPL", 30, window_days=180)
        assert [r["model_name"] for r in rows] == [MODEL_ARIMA, MODEL_MONTE_CARLO]
        assert rows[0]["rmse"] <= rows[1]["rmse"]

    def test_pending_only_rows_excluded(self, tmp_path):
        """A forecast recorded but never actualized must not contribute --
        matches get_skill_weights' and get_forecast_reliability_curve's own
        actual_price IS NOT NULL filter."""
        tracker = _make_tracker(tmp_path)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, datetime.now(timezone.utc))
        rows = tracker.get_error_by_model("AAPL", 30)
        assert rows == []

    def test_window_excludes_old_rows(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        import sqlite3
        old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(tracker._db_path) as conn:
            conn.execute(
                "INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts, "
                "forecast_price, actual_price, squared_error, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("AAPL", MODEL_ARIMA, 30, old_ts, 100.0, 110.0, 100.0, now_iso),
            )
            conn.commit()
        rows = tracker.get_error_by_model("AAPL", 30, window_days=60)
        assert rows == []

    def test_does_not_raise_on_db_error(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._db_path = "/nonexistent/path/db.sqlite"
        rows = tracker.get_error_by_model("AAPL", 30)
        assert rows == []

    def test_no_migration_needed_uses_existing_columns_only(self, tmp_path):
        """Regression guard for the specific claim in the method's docstring:
        forecast_price/actual_price were already persisted columns before
        this method existed, so MAE must be derivable without any DDL change.
        Asserts the schema is untouched (still exactly the documented 9
        columns) while get_error_by_model still returns a real MAE."""
        import sqlite3
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc) - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, ts)
        tracker.update_actuals("AAPL", 30, 108.0, datetime.now(timezone.utc))

        with sqlite3.connect(tracker._db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(forecast_errors)").fetchall()}
        assert cols == {
            "id", "symbol", "model_name", "horizon_days", "forecast_ts",
            "forecast_price", "actual_price", "squared_error", "recorded_at",
        }

        rows = tracker.get_error_by_model("AAPL", 30, window_days=180)
        assert rows[0]["mae"] == pytest.approx(8.0, abs=1e-6)


# ---------------------------------------------------------------------------
# pending_count and completed_count
# ---------------------------------------------------------------------------

class TestCountHelpers:
    def test_pending_count_increases_on_record(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0, MODEL_MONTE_CARLO: 151.0}, ts)
        assert tracker.pending_count("AAPL", 30) == 2

    def test_pending_decreases_after_actualize(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        ts = datetime.now(timezone.utc) - timedelta(days=35)
        tracker.record_forecasts("AAPL", 30, {MODEL_ARIMA: 150.0}, ts)
        tracker.update_actuals("AAPL", 30, 155.0, datetime.now(timezone.utc))
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
    def test_all_model_names_contains_seven_entries(self):
        """Extended for the BERT-LLA ablations (lstm_baseline,
        lstm_attention, bert_lla) alongside the original four."""
        assert len(ALL_MODEL_NAMES) == 7

    def test_model_name_constants_are_strings(self):
        for name in ALL_MODEL_NAMES:
            assert isinstance(name, str)

    def test_min_rmse_positive(self):
        assert _MIN_RMSE > 0

    def test_forecast_tracker_importable_from_package(self):
        from forecasting import ForecastTracker as FT  # noqa: F401
        assert FT is ForecastTracker


class TestDefaultDbPathResolvesThroughDbConfig:
    """Regression coverage for a real incident: ForecastTracker.__init__ used
    to default db_path to the bare literal "quant_platform.db" (CWD-relative),
    bypassing db_config.resolve_database_url()/settings.LOCAL_DATA_ROOT
    entirely -- every sibling store (data/historical_store.py,
    data/paper_account_store.py, transactions_store.py, ...) resolves through
    that seam, but this one didn't. Confirmed live: with
    FORECAST_SKILL_WEIGHTING_ENABLED=true (a real operator's actual .env),
    main_orchestrator.py's EngineContext.build() constructs a bare
    ForecastTracker() every cycle, so this wrote ~2 million real
    forecast_errors rows to a stray CWD-relative file that never migrated
    alongside the rest of the platform's data when settings.LOCAL_DATA_ROOT
    was introduced -- a live split-brain between the intended shared location
    and this one stray file, undetected until caught by direct inspection."""

    def test_omitted_db_path_resolves_via_resolve_database_url(self, monkeypatch, tmp_path):
        fake_db = str(tmp_path / "resolved.db")
        monkeypatch.setattr(
            "db_config.resolve_database_url", lambda: f"sqlite:///{fake_db}"
        )
        tracker = ForecastTracker()
        assert tracker._db_path == fake_db

    def test_explicit_db_path_still_wins(self, tmp_path):
        explicit = str(tmp_path / "explicit.db")
        tracker = ForecastTracker(db_path=explicit)
        assert tracker._db_path == explicit

    def test_non_sqlite_database_url_falls_back_to_the_historical_literal(self, monkeypatch):
        """This class only ever talks to sqlite (sqlite3.connect(), not
        SQLAlchemy) -- an operator-configured postgresql:// DATABASE_URL
        can't be honored here, so it degrades to the pre-fix literal rather
        than raising or silently mis-resolving."""
        monkeypatch.setattr(
            "db_config.resolve_database_url",
            lambda: "postgresql://user:pass@host/db",
        )
        tracker = ForecastTracker()
        assert tracker._db_path == "quant_platform.db"


class TestGetForecastReliabilityCurve:
    """Tests for get_forecast_reliability_curve() -- distinct from
    evaluation_engine.py's calibration_curve() (conviction-vs-win-rate from
    closed trades, not forecast accuracy)."""

    def _insert_completed_row(
        self, tracker, symbol="AAPL", horizon=30, model="arima",
        forecast_price=100.0, actual_price=100.0,
    ):
        """Record a forecast far enough in the past, then actualize it --
        exercises the real record_forecasts()/update_actuals() API rather
        than raw SQL, matching this file's existing test conventions."""
        forecast_ts = datetime.now(timezone.utc) - timedelta(days=horizon + 1)
        tracker.record_forecasts(symbol, horizon, {model: forecast_price}, forecast_ts)
        tracker.update_actuals(symbol, horizon, actual_price, datetime.now(timezone.utc))

    def test_hand_computable_curve(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        # 3 rows with a known +10% realized error (actual > forecast) --
        # (actual - forecast) / actual = (110 - 100) / 110 ~= 0.0909
        for _ in range(3):
            self._insert_completed_row(tracker, forecast_price=100.0, actual_price=110.0)

        curve = tracker.get_forecast_reliability_curve(symbol="AAPL", horizon_days=30, min_per_bin=3)

        assert not curve.empty
        row = curve.iloc[0]
        assert row["model_name"] == "arima"
        assert row["horizon_days"] == 30
        assert row["count"] == 3
        expected_pct_error = (110.0 - 100.0) / 110.0
        assert row["mean_pct_error"] == pytest.approx(expected_pct_error, abs=1e-6)
        assert row["bin_low"] <= expected_pct_error <= row["bin_high"]

    def test_filter_by_symbol(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        for _ in range(3):
            self._insert_completed_row(tracker, symbol="AAPL")
        for _ in range(3):
            self._insert_completed_row(tracker, symbol="MSFT")

        curve = tracker.get_forecast_reliability_curve(symbol="AAPL", min_per_bin=3)
        assert not curve.empty
        # Only AAPL rows contributed -- 3, not 6.
        assert curve["count"].sum() == 3

    def test_filter_by_horizon_days(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        for _ in range(3):
            self._insert_completed_row(tracker, horizon=30)
        for _ in range(3):
            self._insert_completed_row(tracker, horizon=60)

        curve = tracker.get_forecast_reliability_curve(horizon_days=30, min_per_bin=3)
        assert not curve.empty
        assert (curve["horizon_days"] == 30).all()
        assert curve["count"].sum() == 3

    def test_empty_tracker_returns_correct_schema(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        curve = tracker.get_forecast_reliability_curve()
        assert curve.empty
        expected_cols = {"model_name", "horizon_days", "bin_low", "bin_high", "bin_center", "mean_pct_error", "count"}
        assert expected_cols.issubset(set(curve.columns))

    def test_pending_only_rows_return_empty(self, tmp_path):
        """A forecast recorded but never actualized (actual_price IS NULL)
        must not appear in the curve."""
        tracker = _make_tracker(tmp_path)
        tracker.record_forecasts("AAPL", 30, {"arima": 100.0}, datetime.now(timezone.utc))
        curve = tracker.get_forecast_reliability_curve()
        assert curve.empty

    def test_db_error_returns_empty_never_raises(self, tmp_path):
        tracker = _make_tracker(tmp_path)

        def _broken_conn(*a, **kw):
            raise RuntimeError("simulated DB failure")

        tracker._get_conn = _broken_conn
        curve = tracker.get_forecast_reliability_curve()
        assert curve.empty

    def test_sparse_bin_gets_nan_others_unaffected(self, tmp_path):
        """A bin with fewer than min_per_bin rows gets NaN mean_pct_error
        (never fabricated -- CONSTRAINT #4), while a well-populated bin in
        the same result still gets a real value."""
        tracker = _make_tracker(tmp_path)
        # Sparse bin: 1 row with a large positive error (~+40%).
        self._insert_completed_row(tracker, forecast_price=60.0, actual_price=100.0)
        # Well-populated bin: 3 rows with ~0% error.
        for _ in range(3):
            self._insert_completed_row(tracker, forecast_price=100.0, actual_price=100.0)

        curve = tracker.get_forecast_reliability_curve(min_per_bin=3)
        assert not curve.empty

        sparse_row = curve[curve["count"] == 1]
        assert not sparse_row.empty
        assert math.isnan(sparse_row.iloc[0]["mean_pct_error"])

        populated_row = curve[curve["count"] == 3]
        assert not populated_row.empty
        assert not math.isnan(populated_row.iloc[0]["mean_pct_error"])

    def test_count_and_horizon_days_are_int(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        for _ in range(3):
            self._insert_completed_row(tracker, horizon=30)

        curve = tracker.get_forecast_reliability_curve(min_per_bin=3)
        assert not curve.empty
        assert curve["count"].dtype.kind in "iu"
        assert curve["horizon_days"].dtype.kind in "iu"


# ---------------------------------------------------------------------------
# readonly=True — DATABASE-LEVEL read-only tracker
# ---------------------------------------------------------------------------

class TestReadonlyMode:
    def test_reads_data_written_by_a_write_mode_tracker(self, tmp_path):
        db = os.path.join(str(tmp_path), "t.db")
        writer = ForecastTracker(db_path=db)
        _record(writer, symbol="AAPL", horizon=30, arima=100.0)

        reader = ForecastTracker(db_path=db, readonly=True)
        # get_skill_weights needs completed (actualized) rows to return
        # anything nonempty; pending_count is the more direct proof of a read.
        assert reader.pending_count("AAPL", 30) == 1

    def test_write_is_blocked_and_leaves_no_trace(self, tmp_path):
        db = os.path.join(str(tmp_path), "t.db")
        ForecastTracker(db_path=db)  # write-mode: creates the schema first
        reader = ForecastTracker(db_path=db, readonly=True)
        reader.record_forecasts("MSFT", 30, {"arima": 50.0}, datetime.now(timezone.utc))
        writer = ForecastTracker(db_path=db)
        assert writer.pending_count("MSFT", 30) == 0

    def test_degrades_to_empty_on_missing_table(self, tmp_path):
        """No write-mode tracker has ever run -> forecast_errors doesn't exist.
        A readonly instance must degrade gracefully (CONSTRAINT #6), not crash."""
        db = os.path.join(str(tmp_path), "never_written.db")
        open(db, "w").close()
        reader = ForecastTracker(db_path=db, readonly=True)
        assert reader.get_skill_weights("AAPL", 30) == {}
        assert reader.pending_count("AAPL", 30) == 0
        assert reader.completed_count("AAPL", 30) == 0

    def test_construction_skips_ensure_table(self, tmp_path, monkeypatch):
        """readonly=True must not attempt DDL at construction — a read-only
        engine would reject it anyway, and it would fire on every construction."""
        calls = []
        monkeypatch.setattr(
            ForecastTracker, "_ensure_table", lambda self: calls.append("ensure")
        )
        db = os.path.join(str(tmp_path), "t.db")
        ForecastTracker(db_path=db, readonly=True)
        assert calls == []

    def test_readonly_connection_sets_no_journal_mode(self, tmp_path):
        """Regression for the WAL trap (see db_config.create_readonly_db_engine):
        the readonly connection must issue busy_timeout only, never journal_mode
        -- reading a NON-WAL db read-only must not raise."""
        import sqlite3
        db = os.path.join(str(tmp_path), "plain.db")
        conn = sqlite3.connect(db)
        conn.execute(ForecastTracker._TABLE_DDL)
        conn.commit()
        conn.close()
        assert not os.path.exists(db + "-wal")  # confirm non-WAL

        reader = ForecastTracker(db_path=db, readonly=True)
        # Would raise here if the readonly hook issued journal_mode=WAL.
        assert reader.pending_count("AAPL", 30) == 0
