"""Tests for ``pilots/performance.py`` — honest, read-only backtest metrics.

All fixture-backed; no network, no heavy engines. The fixture
``tests/fixtures/timeseries_momentum_validation_summary.json`` is the shared
Wave-1 artifact (schema = ``ValidationReport.to_summary_dict()``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilots.catalog import get_pilot
from pilots.performance import (
    load_validation_summary,
    pilot_headline,
    pilot_performance,
)

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


# ---------------------------------------------------------------------------
# load_validation_summary
# ---------------------------------------------------------------------------
class TestLoadValidationSummary:
    def test_hit_returns_parsed_dict(self):
        summary = load_validation_summary("timeseries_momentum", reports_dir=FIXTURES_DIR)
        assert summary is not None
        assert summary["strategy_id"] == "timeseries_momentum"
        assert summary["deployable"] is True
        assert summary["sharpe"] == pytest.approx(1.14)
        assert summary["dsr"] == pytest.approx(0.972)
        assert summary["pbo"] == pytest.approx(0.18)
        assert summary["max_drawdown"] == pytest.approx(0.176)

    def test_miss_returns_none(self):
        assert load_validation_summary("does_not_exist", reports_dir=FIXTURES_DIR) is None

    def test_empty_strategy_id_returns_none(self):
        assert load_validation_summary("", reports_dir=FIXTURES_DIR) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        bad = tmp_path / "broken_validation_summary.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert load_validation_summary("broken", reports_dir=str(tmp_path)) is None

    def test_non_object_json_returns_none(self, tmp_path):
        arr = tmp_path / "arr_validation_summary.json"
        arr.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_validation_summary("arr", reports_dir=str(tmp_path)) is None


# ---------------------------------------------------------------------------
# pilot_headline
# ---------------------------------------------------------------------------
class TestPilotHeadline:
    def test_fixture_backed_pilot(self):
        pilot = get_pilot("trend-following")  # validation_strategy_id == timeseries_momentum
        assert pilot is not None
        headline = pilot_headline(pilot, reports_dir=FIXTURES_DIR)
        assert headline == {
            "sharpe": pytest.approx(1.14),
            "dsr": pytest.approx(0.972),
            "pbo": pytest.approx(0.18),
            "max_drawdown": pytest.approx(0.176),
            "deployable": True,
        }

    def test_none_validation_id_all_none(self):
        pilot = get_pilot("balanced-blend")  # validation_strategy_id is None
        assert pilot is not None
        headline = pilot_headline(pilot, reports_dir=FIXTURES_DIR)
        assert headline == {
            "sharpe": None,
            "dsr": None,
            "pbo": None,
            "max_drawdown": None,
            "deployable": None,
        }

    def test_missing_summary_all_none(self):
        pilot = get_pilot("dip-buyer")  # validation_strategy_id == rsi2_mean_reversion (no fixture)
        assert pilot is not None
        headline = pilot_headline(pilot, reports_dir=FIXTURES_DIR)
        assert all(v is None for v in headline.values())

    def test_absent_field_stays_none_not_fabricated(self, tmp_path):
        # Summary missing 'dsr' -> headline dsr must be None, never 0.0.
        (tmp_path / "partial_validation_summary.json").write_text(
            json.dumps({"strategy_id": "partial", "sharpe": 0.9, "deployable": False}),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "partial"

        headline = pilot_headline(_P(), reports_dir=str(tmp_path))
        assert headline["sharpe"] == pytest.approx(0.9)
        assert headline["deployable"] is False
        assert headline["dsr"] is None
        assert headline["pbo"] is None
        assert headline["max_drawdown"] is None


# ---------------------------------------------------------------------------
# pilot_performance
# ---------------------------------------------------------------------------
class TestPilotPerformance:
    def test_fixture_backed_pilot_metrics_and_curve_present(self):
        pilot = get_pilot("trend-following")
        perf = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)
        assert perf["metrics"] is not None
        assert perf["metrics"]["strategy_id"] == "timeseries_momentum"
        # The fixture carries a persisted equity_curve -> a real curve is served.
        curve = perf["curve"]
        assert isinstance(curve, list) and len(curve) >= 2
        assert all(set(p) == {"date", "value"} for p in curve)
        assert all(isinstance(p["value"], (int, float)) for p in curve)
        # benchmark is not persisted yet; reason is None when a curve is present.
        assert perf["benchmark"] is None
        assert perf["reason"] is None
        assert perf["range"] == "2Y"

    def test_none_validation_id_is_honest_null(self):
        pilot = get_pilot("balanced-blend")
        perf = pilot_performance(pilot, reports_dir=FIXTURES_DIR)
        assert perf["metrics"] is None
        assert perf["curve"] is None
        assert perf["benchmark"] is None
        assert perf["reason"] == "no validated backtest for this pilot"

    def test_missing_summary_is_honest_null(self):
        pilot = get_pilot("dip-buyer")  # rsi2_mean_reversion — no fixture on disk
        perf = pilot_performance(pilot, reports_dir=FIXTURES_DIR)
        assert perf["metrics"] is None
        assert perf["curve"] is None
        assert "rsi2_mean_reversion" in perf["reason"]

    def test_range_echoed(self):
        pilot = get_pilot("trend-following")
        for rng in ("1W", "1M", "3M", "6M", "1Y", "2Y"):
            perf = pilot_performance(pilot, range=rng, reports_dir=FIXTURES_DIR)
            assert perf["range"] == rng

    def test_range_is_a_tail_slice_of_the_same_series(self):
        """Shorter ranges are honest zooms: never more points than a longer range,
        and every range shares the same last point (the series' latest date)."""
        pilot = get_pilot("trend-following")
        full = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)["curve"]
        one_m = pilot_performance(pilot, range="1M", reports_dir=FIXTURES_DIR)["curve"]
        one_y = pilot_performance(pilot, range="1Y", reports_dir=FIXTURES_DIR)["curve"]
        assert full and one_y and one_m
        # zoom: shorter window -> fewer-or-equal points
        assert len(one_m) <= len(one_y) <= len(full)
        # a chart always needs >= 2 points, even for the shortest range
        assert len(one_m) >= 2
        # same latest point across ranges (pure tail slice, not a re-run)
        assert one_m[-1] == one_y[-1] == full[-1]

    def test_summary_without_curve_is_honest_null(self, tmp_path):
        """A summary that predates the equity_curve field -> curve None + honest
        reason, never a fabricated line (CONSTRAINT #4)."""
        (tmp_path / "legacy_validation_summary.json").write_text(
            json.dumps({"strategy_id": "legacy", "sharpe": 1.0, "deployable": True}),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "legacy"

        perf = pilot_performance(_P(), range="1M", reports_dir=str(tmp_path))
        assert perf["metrics"] is not None
        assert perf["curve"] is None
        assert perf["reason"] == "no backtest series persisted"

    def test_single_point_curve_is_treated_as_absent(self, tmp_path):
        """A degenerate 1-point curve can't render a chart -> honest None."""
        (tmp_path / "onept_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "onept",
                "sharpe": 1.0,
                "deployable": True,
                "equity_curve": [{"date": "2024-01-31", "value": 100.0}],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "onept"

        perf = pilot_performance(_P(), reports_dir=str(tmp_path))
        assert perf["curve"] is None
        assert perf["reason"] == "no backtest series persisted"

    def test_never_raises_on_unknown_pilot_shape(self):
        # A bare object without validation_strategy_id attribute degrades honestly.
        class _Empty:
            pass

        perf = pilot_performance(_Empty(), reports_dir=FIXTURES_DIR)
        assert perf["metrics"] is None
        assert perf["reason"] == "no validated backtest for this pilot"
