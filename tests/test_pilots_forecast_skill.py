"""
tests/test_pilots_forecast_skill.py
====================================
Tests for ``pilots/forecast_skill.py`` — the per-symbol forecast reliability +
skill-weights + per-model error reader powering ``GET
/symbols/{ticker}/forecast``.

All tests are fully offline: no network, no real ``quant_platform.db`` — each
test writes to a fresh temporary SQLite database via
``forecasting.forecast_tracker.ForecastTracker``'s own write API (mirroring
``tests/test_forecast_tracker.py``'s own fixture convention) and monkeypatches
``ForecastTracker`` construction inside ``forecast_skill_view`` to point at it,
since the view hardcodes ``ForecastTracker(readonly=True)`` against the
default ``quant_platform.db`` path with no ``db_path`` parameter of its own.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from forecasting.forecast_tracker import ForecastTracker, MODEL_ARIMA, MODEL_MONTE_CARLO
from pilots import forecast_skill


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _point_at(monkeypatch, db_path: str) -> None:
    """Make forecast_skill_view's internal ForecastTracker(readonly=True)
    construction resolve to a tracker over our temp DB instead of the real
    quant_platform.db, without changing the module's public signature."""
    def _fake_tracker(*, readonly: bool = False):
        assert readonly is True  # the view always constructs read-only
        return ForecastTracker(db_path=db_path, readonly=True)

    monkeypatch.setattr(
        "forecasting.forecast_tracker.ForecastTracker", _fake_tracker
    )


def _seed_completed(db_path: str, symbol: str, horizon: int, model: str,
                     forecast_price: float, actual_price: float) -> None:
    writer = ForecastTracker(db_path=db_path)
    ts = datetime.now(timezone.utc) - timedelta(days=horizon + 5)
    writer.record_forecasts(symbol, horizon, {model: forecast_price}, ts)
    writer.update_actuals(symbol, horizon, actual_price, datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# error_by_model wiring
# ---------------------------------------------------------------------------


class TestErrorByModel:
    def test_empty_view_includes_empty_error_by_model(self):
        """A blank symbol short-circuits to _empty_view before any DB touch --
        error_by_model must still be present and empty (never a missing key,
        which would break a frontend that reads it unconditionally)."""
        result = forecast_skill.forecast_skill_view("", horizon_days=30)
        assert result["error_by_model"] == []

    def test_no_history_returns_empty_error_by_model_with_reason(self, tmp_path, monkeypatch):
        db = str(tmp_path / "empty.db")
        ForecastTracker(db_path=db)  # write-mode: creates schema, no rows
        _point_at(monkeypatch, db)

        result = forecast_skill.forecast_skill_view("AAPL", horizon_days=30)
        assert result["error_by_model"] == []
        assert result["reason"] is not None

    def test_populated_history_shapes_error_by_model(self, tmp_path, monkeypatch):
        db = str(tmp_path / "t.db")
        _seed_completed(db, "AAPL", 30, MODEL_ARIMA, forecast_price=100.0, actual_price=110.0)
        _point_at(monkeypatch, db)

        result = forecast_skill.forecast_skill_view("AAPL", horizon_days=30)
        assert result["reason"] is None
        assert len(result["error_by_model"]) == 1
        row = result["error_by_model"][0]
        assert row["model_name"] == MODEL_ARIMA
        assert row["n"] == 1
        assert row["rmse"] == pytest.approx(10.0, abs=1e-6)
        assert row["mae"] == pytest.approx(10.0, abs=1e-6)
        # Every value is JSON-safe (no NaN/Infinity — CONSTRAINT #4).
        assert isinstance(row["rmse"], float)
        assert isinstance(row["mae"], float)

    def test_multiple_models_sorted_best_first(self, tmp_path, monkeypatch):
        db = str(tmp_path / "t.db")
        _seed_completed(db, "AAPL", 30, MODEL_ARIMA, forecast_price=100.0, actual_price=101.0)
        _seed_completed(db, "AAPL", 30, MODEL_MONTE_CARLO, forecast_price=100.0, actual_price=120.0)
        _point_at(monkeypatch, db)

        result = forecast_skill.forecast_skill_view("AAPL", horizon_days=30)
        names = [r["model_name"] for r in result["error_by_model"]]
        assert names == [MODEL_ARIMA, MODEL_MONTE_CARLO]

    def test_symbol_scoping_does_not_leak_across_tickers(self, tmp_path, monkeypatch):
        db = str(tmp_path / "t.db")
        _seed_completed(db, "AAPL", 30, MODEL_ARIMA, forecast_price=100.0, actual_price=110.0)
        _seed_completed(db, "MSFT", 30, MODEL_ARIMA, forecast_price=200.0, actual_price=250.0)
        _point_at(monkeypatch, db)

        result = forecast_skill.forecast_skill_view("AAPL", horizon_days=30)
        assert len(result["error_by_model"]) == 1
        # MSFT's much larger error (50 vs 10) would be obvious if it leaked in.
        assert result["error_by_model"][0]["mae"] == pytest.approx(10.0, abs=1e-6)

    def test_tracker_construction_failure_degrades_to_empty(self, monkeypatch):
        """If ForecastTracker itself can't even construct (e.g. import error),
        the whole view must degrade to _empty_view rather than raising
        (CONSTRAINT #6) -- exercises the view's outer try/except, not just
        the tracker's own internal dead-lettering."""
        def _boom(*, readonly: bool = False):
            raise RuntimeError("simulated construction failure")

        monkeypatch.setattr(
            "forecasting.forecast_tracker.ForecastTracker", _boom
        )
        result = forecast_skill.forecast_skill_view("AAPL", horizon_days=30)
        assert result["error_by_model"] == []
        assert result["reason"] is not None

    def test_error_by_model_failure_alone_does_not_blank_other_fields(self, tmp_path, monkeypatch):
        """A failure isolated to get_error_by_model must not take down
        reliability_curve/skill_weights/pending/completed -- each field
        degrades independently (mirrors the view's per-field try/except)."""
        db = str(tmp_path / "t.db")
        _seed_completed(db, "AAPL", 30, MODEL_ARIMA, forecast_price=100.0, actual_price=110.0)
        _point_at(monkeypatch, db)

        def _boom(self, symbol, horizon_days, window_days=60):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(ForecastTracker, "get_error_by_model", _boom)

        result = forecast_skill.forecast_skill_view("AAPL", horizon_days=30)
        assert result["error_by_model"] == []
        # completed_count still works -- proves the outage was scoped.
        assert result["completed"] == 1
        assert result["reason"] is None


# ---------------------------------------------------------------------------
# Import scope guard
# ---------------------------------------------------------------------------


_HEAVY_ENGINE_DENYLIST = {
    "processing_engine",
    "strategy_engine",
    "forecasting_engine",
    "macro_engine",
    "technical_options_engine",
    "main_orchestrator",
    "desktop",
    "signals",
}


def test_never_imports_a_heavy_engine():
    """pilots/forecast_skill.py's own module docstring claims it's a
    'light-module read' safe on the AST-guarded api/pilots_api.py import
    path (forecasting.forecast_tracker, not the forbidden
    forecasting_engine). This pins that claim mechanically, the same pattern
    tests/test_pilots_rolling_beta.py uses for its own module."""
    path = pathlib.Path(forecast_skill.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert not (roots & _HEAVY_ENGINE_DENYLIST), (
        f"pilots/forecast_skill.py imports a forbidden heavy engine: {roots & _HEAVY_ENGINE_DENYLIST}"
    )
