"""Tests for ``pilots/strategy_health.py`` — per-gate deployability breakdown.

All fixture-backed; no network, no heavy engines. Reuses the shared Wave-1
fixture ``tests/fixtures/timeseries_momentum_validation_summary.json`` (schema
= ``ValidationReport.to_summary_dict()``) for the all-gates-pass happy path,
and hand-writes small synthetic summaries/history files under ``tmp_path`` for
the failing-gate and run-over-run-trend cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilots.catalog import Pilot, get_pilot, list_pilots
from pilots.strategy_health import pilot_strategy_health, strategy_health_rows
from validation import thresholds

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_NO_HISTORY_DIR = str(Path(__file__).parent / "fixtures" / "no_such_history_dir")


# ---------------------------------------------------------------------------
# Happy path — real fixture, all four gates pass
# ---------------------------------------------------------------------------
class TestHappyPath:
    def test_all_gates_pass_for_fixture_backed_pilot(self):
        pilot = get_pilot("trend-following")  # validation_strategy_id == timeseries_momentum
        assert pilot is not None
        result = pilot_strategy_health(
            pilot, reports_dir=FIXTURES_DIR, history_dir=_NO_HISTORY_DIR
        )
        assert result["pilot_id"] == "trend-following"
        assert result["pilot_name"] == pilot.name
        assert result["strategy_id"] == "timeseries_momentum"
        assert result["deployable"] is True
        assert result["reason"] is None
        assert result["is_options_selling"] is False
        assert result["stress_gate_passed"] is True
        assert result["report_date"] == "2026-07-11"
        assert result["trend"] == []  # no history dir configured for this test

        gates_by_key = {g["key"]: g for g in result["gates"]}
        assert set(gates_by_key) == {"pbo", "dsr", "sharpe", "max_drawdown"}

        assert gates_by_key["pbo"]["value"] == pytest.approx(0.18)
        assert gates_by_key["pbo"]["threshold"] == thresholds.PBO_MAX
        assert gates_by_key["pbo"]["direction"] == "below"
        assert gates_by_key["pbo"]["passed"] is True

        assert gates_by_key["dsr"]["value"] == pytest.approx(0.972)
        assert gates_by_key["dsr"]["threshold"] == thresholds.DSR_MIN
        assert gates_by_key["dsr"]["direction"] == "above"
        assert gates_by_key["dsr"]["passed"] is True

        assert gates_by_key["sharpe"]["value"] == pytest.approx(1.14)
        assert gates_by_key["sharpe"]["threshold"] == thresholds.NET_SHARPE_MIN
        assert gates_by_key["sharpe"]["passed"] is True

        assert gates_by_key["max_drawdown"]["value"] == pytest.approx(0.176)
        assert gates_by_key["max_drawdown"]["threshold"] == thresholds.MAX_DRAWDOWN_MAX
        assert gates_by_key["max_drawdown"]["direction"] == "below"
        assert gates_by_key["max_drawdown"]["passed"] is True


# ---------------------------------------------------------------------------
# Honesty: no validated backtest for this pilot (CONSTRAINT #4)
# ---------------------------------------------------------------------------
class TestNoValidatedBacktest:
    def test_none_validation_id_honest_empty_entry(self):
        pilot = get_pilot("balanced-blend")  # validation_strategy_id is None
        assert pilot is not None
        result = pilot_strategy_health(pilot, reports_dir=FIXTURES_DIR)
        assert result["strategy_id"] is None
        assert result["deployable"] is None
        assert result["gates"] == []
        assert result["is_options_selling"] is None
        assert result["stress_gate_passed"] is None
        assert result["report_date"] is None
        assert result["trend"] == []
        assert result["reason"] == "no validated backtest for this pilot"


# ---------------------------------------------------------------------------
# Dead-letter: summary file missing/unreadable (CONSTRAINT #6)
# ---------------------------------------------------------------------------
class TestMissingSummary:
    def test_missing_summary_file_degrades_honestly(self, tmp_path):
        pilot = Pilot(
            id="ghost",
            name="Ghost",
            category="Momentum",
            description="",
            weights={"timeseries_momentum": 1.0},
            validation_strategy_id="does_not_exist",
        )
        result = pilot_strategy_health(pilot, reports_dir=str(tmp_path))
        assert result["strategy_id"] == "does_not_exist"
        assert result["deployable"] is None
        assert result["gates"] == []
        assert result["trend"] == []
        assert "does_not_exist" in result["reason"]

    def test_corrupt_summary_file_degrades_honestly(self, tmp_path):
        bad = tmp_path / "broken_validation_summary.json"
        bad.write_text("{not valid json", encoding="utf-8")
        pilot = Pilot(
            id="broken-pilot",
            name="Broken",
            category="Momentum",
            description="",
            weights={},
            validation_strategy_id="broken",
        )
        result = pilot_strategy_health(pilot, reports_dir=str(tmp_path))
        assert result["deployable"] is None
        assert result["gates"] == []
        assert result["reason"] is not None


# ---------------------------------------------------------------------------
# Per-gate failure breakdown (a genuinely failing strategy)
# ---------------------------------------------------------------------------
class TestGateFailureBreakdown:
    @staticmethod
    def _write_summary(tmp_path, strategy_id, **overrides):
        summary = {
            "strategy_id": strategy_id,
            "deployable": False,
            "pbo": 0.62,
            "dsr": 0.80,
            "sharpe": 0.30,
            "max_drawdown": 0.45,
            "is_options_selling": False,
            "stress_gate_passed": None,
            "report_date": "2026-07-16",
        }
        summary.update(overrides)
        path = Path(tmp_path) / f"{strategy_id}_validation_summary.json"
        path.write_text(json.dumps(summary), encoding="utf-8")
        return summary

    def _pilot(self, strategy_id):
        return Pilot(
            id=f"pilot-{strategy_id}",
            name="Failing Strategy",
            category="Momentum",
            description="",
            weights={},
            validation_strategy_id=strategy_id,
        )

    def test_each_failing_gate_is_flagged_individually(self, tmp_path):
        self._write_summary(tmp_path, "failing_strat")
        result = pilot_strategy_health(
            self._pilot("failing_strat"), reports_dir=str(tmp_path)
        )
        gates_by_key = {g["key"]: g for g in result["gates"]}
        # 0.62 is NOT < PBO_MAX (0.50)
        assert gates_by_key["pbo"]["passed"] is False
        # 0.80 is NOT > DSR_MIN (0.95)
        assert gates_by_key["dsr"]["passed"] is False
        # 0.30 is NOT > NET_SHARPE_MIN (0.50)
        assert gates_by_key["sharpe"]["passed"] is False
        # 0.45 is NOT < MAX_DRAWDOWN_MAX (0.30)
        assert gates_by_key["max_drawdown"]["passed"] is False
        assert result["deployable"] is False

    def test_missing_field_reports_unknown_never_fabricated(self, tmp_path):
        self._write_summary(tmp_path, "partial_strat", sharpe=None)
        result = pilot_strategy_health(
            self._pilot("partial_strat"), reports_dir=str(tmp_path)
        )
        gates_by_key = {g["key"]: g for g in result["gates"]}
        assert gates_by_key["sharpe"]["value"] is None
        assert gates_by_key["sharpe"]["passed"] is None  # unknown, never guessed
        # Other gates are unaffected by the missing sharpe field.
        assert gates_by_key["pbo"]["passed"] is False
        assert gates_by_key["dsr"]["passed"] is False
        assert gates_by_key["max_drawdown"]["passed"] is False

    def test_nan_value_reports_unknown_never_fabricated(self, tmp_path):
        self._write_summary(tmp_path, "nan_strat", dsr=float("nan"))
        result = pilot_strategy_health(self._pilot("nan_strat"), reports_dir=str(tmp_path))
        gates_by_key = {g["key"]: g for g in result["gates"]}
        assert gates_by_key["dsr"]["passed"] is None

    def test_non_numeric_value_reports_unknown_never_fabricated(self, tmp_path):
        self._write_summary(tmp_path, "weird_strat", pbo="not-a-number")
        result = pilot_strategy_health(self._pilot("weird_strat"), reports_dir=str(tmp_path))
        gates_by_key = {g["key"]: g for g in result["gates"]}
        assert gates_by_key["pbo"]["passed"] is None

    def test_options_selling_stress_gate_surfaced(self, tmp_path):
        self._write_summary(
            tmp_path,
            "options_strat",
            is_options_selling=True,
            stress_gate_passed=False,
            deployable=False,
        )
        result = pilot_strategy_health(
            self._pilot("options_strat"), reports_dir=str(tmp_path)
        )
        assert result["is_options_selling"] is True
        assert result["stress_gate_passed"] is False


# ---------------------------------------------------------------------------
# Run-over-run trend (best-effort; never fatal)
# ---------------------------------------------------------------------------
class TestTrend:
    def test_trend_reads_history_oldest_first_capped(self, tmp_path):
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [
            {
                "report_date": "2026-06-01", "pbo": 0.50, "dsr": 0.90,
                "sharpe": 0.40, "max_drawdown": 0.20, "deployable": False,
            },
            {
                "report_date": "2026-06-08", "pbo": 0.30, "dsr": 0.96,
                "sharpe": 0.60, "max_drawdown": 0.15, "deployable": True,
            },
            {
                "report_date": "2026-06-15", "pbo": 0.18, "dsr": 0.972,
                "sharpe": 1.14, "max_drawdown": 0.176, "deployable": True,
            },
        ]
        (history_dir / "timeseries_momentum_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        pilot = get_pilot("trend-following")
        result = pilot_strategy_health(
            pilot, reports_dir=FIXTURES_DIR, history_dir=str(history_dir), trend_limit=2
        )
        assert len(result["trend"]) == 2
        assert result["trend"][0]["report_date"] == "2026-06-08"
        assert result["trend"][-1]["report_date"] == "2026-06-15"
        assert result["trend"][-1]["deployable"] is True

    def test_missing_history_file_degrades_to_empty_list(self, tmp_path):
        pilot = get_pilot("trend-following")
        result = pilot_strategy_health(
            pilot, reports_dir=FIXTURES_DIR, history_dir=str(tmp_path / "nope")
        )
        assert result["trend"] == []

    def test_corrupt_history_line_is_skipped_not_fatal(self, tmp_path):
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        good = {
            "report_date": "2026-06-15", "pbo": 0.18, "dsr": 0.972,
            "sharpe": 1.14, "max_drawdown": 0.176, "deployable": True,
        }
        (history_dir / "timeseries_momentum_validation_history.jsonl").write_text(
            "not valid json\n" + json.dumps(good) + "\n", encoding="utf-8"
        )
        pilot = get_pilot("trend-following")
        result = pilot_strategy_health(
            pilot, reports_dir=FIXTURES_DIR, history_dir=str(history_dir)
        )
        assert len(result["trend"]) == 1
        assert result["trend"][0]["report_date"] == "2026-06-15"

    def test_trend_never_fatal_even_when_summary_missing(self, tmp_path):
        """A Pilot with no summary at all should still resolve (trend stays
        empty as part of the overall honest-empty entry, never raises)."""
        pilot = Pilot(
            id="ghost", name="Ghost", category="Momentum", description="",
            weights={}, validation_strategy_id="does_not_exist",
        )
        result = pilot_strategy_health(pilot, reports_dir=str(tmp_path))
        assert result["trend"] == []


# ---------------------------------------------------------------------------
# Catalog-wide aggregation
# ---------------------------------------------------------------------------
class TestStrategyHealthRows:
    def test_one_entry_per_catalog_pilot_in_order(self):
        rows = strategy_health_rows(reports_dir=FIXTURES_DIR)
        assert [r["pilot_id"] for r in rows] == [p.id for p in list_pilots()]

    def test_never_raises_on_nonexistent_reports_dir(self, tmp_path):
        rows = strategy_health_rows(reports_dir=str(tmp_path / "does_not_exist"))
        assert len(rows) == len(list_pilots())
        assert all(r["reason"] is not None for r in rows)
        assert all(r["gates"] == [] for r in rows)

    def test_deployable_pilot_present_among_the_rows(self):
        rows = strategy_health_rows(reports_dir=FIXTURES_DIR)
        row = next(r for r in rows if r["pilot_id"] == "trend-following")
        assert row["deployable"] is True
