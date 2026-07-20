"""Tests for ``pilots/validation_trend.py`` — cross-strategy validation
snapshot, run-over-run trend, and macro-regime timeline.

All fixture-backed or ``tmp_path``-synthesized; no network, no heavy engines.
Reuses the shared Wave-1 fixture
``tests/fixtures/timeseries_momentum_validation_summary.json`` and the
orphan-strategy fixture
``tests/fixtures/multifactor_lowvol_size_validation_summary.json`` (a
strategy with NO ``pilots.catalog`` Pilot pointing at it — the key case this
module exists to surface, since ``pilots.strategy_health`` is scoped to
catalog Pilots only and would never show it).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pilots.validation_trend import (
    cross_strategy_snapshot,
    macro_regime_timeline,
    validation_history_trend,
    validation_trend_snapshot,
)
from scripts.snapshot_diff import rotate_snapshot

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


# ---------------------------------------------------------------------------
# cross_strategy_snapshot
# ---------------------------------------------------------------------------
class TestCrossStrategySnapshot:
    def test_real_fixtures_include_orphan_strategy(self):
        result = cross_strategy_snapshot(FIXTURES_DIR)
        ids = {r["strategy_id"] for r in result["strategies"]}
        assert "timeseries_momentum" in ids
        assert "multifactor_lowvol_size" in ids
        assert result["reason"] is None

    def test_sorted_deterministically_by_strategy_id(self):
        result = cross_strategy_snapshot(FIXTURES_DIR)
        ids = [r["strategy_id"] for r in result["strategies"]]
        assert ids == sorted(ids)

    def test_no_reports_dir_is_honest_empty(self, tmp_path):
        result = cross_strategy_snapshot(str(tmp_path / "nope"))
        assert result["strategies"] == []
        assert result["reason"]

    def test_empty_reports_dir_is_honest_empty(self, tmp_path):
        result = cross_strategy_snapshot(str(tmp_path))
        assert result["strategies"] == []
        assert result["reason"]

    def test_corrupt_file_skipped_good_file_survives(self, tmp_path):
        (tmp_path / "good_validation_summary.json").write_text(
            json.dumps({"strategy_id": "good", "deployable": True}), encoding="utf-8"
        )
        (tmp_path / "bad_validation_summary.json").write_text(
            "not json at all {{{", encoding="utf-8"
        )
        result = cross_strategy_snapshot(str(tmp_path))
        assert [r["strategy_id"] for r in result["strategies"]] == ["good"]

    def test_non_object_json_skipped(self, tmp_path):
        (tmp_path / "listy_validation_summary.json").write_text("[1, 2, 3]", encoding="utf-8")
        result = cross_strategy_snapshot(str(tmp_path))
        assert result["strategies"] == []

    def test_missing_strategy_id_skipped(self, tmp_path):
        (tmp_path / "noid_validation_summary.json").write_text(
            json.dumps({"deployable": True}), encoding="utf-8"
        )
        result = cross_strategy_snapshot(str(tmp_path))
        assert result["strategies"] == []

    def test_nan_and_infinity_are_nulled_never_reserialized(self, tmp_path):
        # json.loads accepts bare NaN/Infinity tokens as a Python extension;
        # the response must never carry them back out as invalid JSON
        # literals (mirrors the bug fixed in pilots/live_inventory.py).
        (tmp_path / "nanstrat_validation_summary.json").write_text(
            '{"strategy_id": "nanstrat", "pbo": NaN, "dsr": Infinity, '
            '"sharpe": -Infinity, "max_drawdown": 0.1}',
            encoding="utf-8",
        )
        result = cross_strategy_snapshot(str(tmp_path))
        row = result["strategies"][0]
        assert row["pbo"] is None
        assert row["dsr"] is None
        assert row["sharpe"] is None
        assert row["max_drawdown"] == 0.1

    def test_non_bool_deployable_is_nulled_not_coerced(self, tmp_path):
        # A string/int in a bool-typed field must not be coerced to a
        # truthy/falsy guess (CONSTRAINT #4) -- None (unknown) is honest.
        (tmp_path / "weird_validation_summary.json").write_text(
            json.dumps({"strategy_id": "weird", "deployable": "yes", "is_options_selling": 1}),
            encoding="utf-8",
        )
        row = cross_strategy_snapshot(str(tmp_path))["strategies"][0]
        assert row["deployable"] is None
        assert row["is_options_selling"] is None


# ---------------------------------------------------------------------------
# validation_history_trend
# ---------------------------------------------------------------------------
class TestValidationHistoryTrend:
    def test_no_history_dir_is_honest_empty(self, tmp_path):
        (tmp_path / "solo_validation_summary.json").write_text(
            json.dumps({"strategy_id": "solo"}), encoding="utf-8"
        )
        result = validation_history_trend(str(tmp_path), str(tmp_path / "no_history"))
        assert result["trend"] == {}
        assert result["reason"]

    def test_single_run_omitted_not_fabricated(self, tmp_path):
        (tmp_path / "solo_validation_summary.json").write_text(
            json.dumps({"strategy_id": "solo"}), encoding="utf-8"
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        (history_dir / "solo_validation_history.jsonl").write_text(
            json.dumps({"report_date": "2026-06-01", "dsr": 0.9}) + "\n", encoding="utf-8"
        )
        result = validation_history_trend(str(tmp_path), str(history_dir))
        assert "solo" not in result["trend"]
        assert result["reason"]

    def test_two_plus_runs_oldest_first(self, tmp_path):
        (tmp_path / "duo_validation_summary.json").write_text(
            json.dumps({"strategy_id": "duo"}), encoding="utf-8"
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [
            {"report_date": "2026-06-01", "pbo": 0.4, "dsr": 0.90, "sharpe": 0.4,
             "max_drawdown": 0.20, "deployable": False},
            {"report_date": "2026-06-15", "pbo": 0.18, "dsr": 0.972, "sharpe": 1.14,
             "max_drawdown": 0.176, "deployable": True},
            {"report_date": "2026-07-01", "pbo": 0.15, "dsr": 0.98, "sharpe": 1.2,
             "max_drawdown": 0.15, "deployable": True},
        ]
        (history_dir / "duo_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        result = validation_history_trend(str(tmp_path), str(history_dir))
        assert result["reason"] is None
        pts = result["trend"]["duo"]
        assert [p["report_date"] for p in pts] == ["2026-06-01", "2026-06-15", "2026-07-01"]
        assert pts[-1]["dsr"] == 0.98

    def test_trend_limit_caps_most_recent(self, tmp_path):
        (tmp_path / "many_validation_summary.json").write_text(
            json.dumps({"strategy_id": "many"}), encoding="utf-8"
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [{"report_date": f"2026-06-{i:02d}", "dsr": 0.9 + i / 1000} for i in range(1, 11)]
        (history_dir / "many_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        result = validation_history_trend(str(tmp_path), str(history_dir), trend_limit=3)
        pts = result["trend"]["many"]
        assert len(pts) == 3
        assert [p["report_date"] for p in pts] == ["2026-06-08", "2026-06-09", "2026-06-10"]

    def test_strategy_without_a_summary_file_is_not_checked(self, tmp_path):
        # A lingering history file for a strategy whose summary was deleted
        # must NOT surface -- mirrors the legacy panel's own behavior of
        # iterating the just-loaded summaries list, not a separate glob.
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [{"report_date": "2026-06-01", "dsr": 0.9}, {"report_date": "2026-06-15", "dsr": 0.95}]
        (history_dir / "ghost_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        result = validation_history_trend(str(tmp_path), str(history_dir))
        assert result["trend"] == {}

    def test_corrupt_history_line_skipped(self, tmp_path):
        (tmp_path / "flaky_validation_summary.json").write_text(
            json.dumps({"strategy_id": "flaky"}), encoding="utf-8"
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        (history_dir / "flaky_validation_history.jsonl").write_text(
            json.dumps({"report_date": "2026-06-01", "dsr": 0.9}) + "\n"
            + "{not json\n"
            + json.dumps({"report_date": "2026-06-15", "dsr": 0.95}) + "\n",
            encoding="utf-8",
        )
        result = validation_history_trend(str(tmp_path), str(history_dir))
        assert [p["report_date"] for p in result["trend"]["flaky"]] == ["2026-06-01", "2026-06-15"]


# ---------------------------------------------------------------------------
# macro_regime_timeline
# ---------------------------------------------------------------------------
class TestMacroRegimeTimeline:
    def test_no_rotated_snapshots_is_honest_empty(self, tmp_path):
        result = macro_regime_timeline(tmp_path)
        assert result["transitions"] == []
        assert result["n_rotated_snapshots"] == 0
        assert result["reason"]

    def test_single_rotated_snapshot_insufficient(self, tmp_path):
        rotate_snapshot(
            {"timestamp": datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat(),
             "market_regime": "RISK ON"},
            tmp_path, max_age_days=0,
        )
        result = macro_regime_timeline(tmp_path)
        assert result["transitions"] == []
        assert result["n_rotated_snapshots"] == 1
        assert result["reason"]

    def test_only_genuine_transitions_returned(self, tmp_path):
        base = datetime(2026, 7, 1, tzinfo=timezone.utc)

        def snap(hours: int, regime: str) -> dict:
            return {"timestamp": (base + timedelta(hours=hours)).isoformat(), "market_regime": regime}

        rotate_snapshot(snap(0, "RISK ON"), tmp_path, max_age_days=0)
        rotate_snapshot(snap(24, "RISK ON"), tmp_path, max_age_days=0)  # no change
        rotate_snapshot(snap(48, "RISK OFF"), tmp_path, max_age_days=0)  # transition
        rotate_snapshot(snap(72, "RECESSION"), tmp_path, max_age_days=0)  # transition
        rotate_snapshot(snap(96, "RECESSION"), tmp_path, max_age_days=0)  # no change

        result = macro_regime_timeline(tmp_path)
        assert result["n_rotated_snapshots"] == 5
        assert result["reason"] is None
        assert [t["market_regime"] for t in result["transitions"]] == ["RISK ON", "RISK OFF", "RECESSION"]
        # First point is always a "transition" (prev=None), matching the
        # legacy panel's own .ne(.shift()) semantics.
        assert result["transitions"][0]["timestamp"] == snap(0, "RISK ON")["timestamp"]

    def test_snapshot_missing_regime_field_is_skipped(self, tmp_path):
        rotate_snapshot(
            {"timestamp": datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat()},
            tmp_path, max_age_days=0,
        )
        rotate_snapshot(
            {"timestamp": datetime(2026, 7, 2, tzinfo=timezone.utc).isoformat(),
             "market_regime": "RISK ON"},
            tmp_path, max_age_days=0,
        )
        result = macro_regime_timeline(tmp_path)
        # Only one usable point (regime present) -> still insufficient (< 2).
        assert result["transitions"] == []
        assert result["reason"]

    def test_defaults_to_live_settings_output_dir(self, tmp_path, monkeypatch):
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        result = macro_regime_timeline()  # no explicit output_dir -> reads settings live
        assert result["n_rotated_snapshots"] == 0
        assert result["reason"]


# ---------------------------------------------------------------------------
# validation_trend_snapshot (composite)
# ---------------------------------------------------------------------------
class TestValidationTrendSnapshotComposite:
    def test_bundles_all_three_sections_independently(self, tmp_path):
        (tmp_path / "timeseries_momentum_validation_summary.json").write_text(
            Path(FIXTURES_DIR, "timeseries_momentum_validation_summary.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = validation_trend_snapshot(reports_dir=str(tmp_path), output_dir=tmp_path)
        assert result["strategies"] and result["strategies_reason"] is None
        # No history/regime data seeded -> both other sections degrade honestly
        # without affecting the strategies section (CONSTRAINT #6).
        assert result["trend"] == {}
        assert result["trend_reason"]
        assert result["regime_timeline"] == []
        assert result["regime_reason"]

    def test_all_three_sections_populated_together(self, tmp_path):
        # Strategy snapshot + its own 2-run history + a real regime
        # transition, all seeded at once -- proves the three independently-
        # sourced sections compose correctly into one payload.
        (tmp_path / "duo_validation_summary.json").write_text(
            json.dumps({"strategy_id": "duo", "deployable": True, "pbo": 0.15,
                        "dsr": 0.98, "sharpe": 1.2, "max_drawdown": 0.15}),
            encoding="utf-8",
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [
            {"report_date": "2026-06-01", "dsr": 0.90},
            {"report_date": "2026-06-15", "dsr": 0.98},
        ]
        (history_dir / "duo_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        base = datetime(2026, 7, 1, tzinfo=timezone.utc)
        rotate_snapshot(
            {"timestamp": base.isoformat(), "market_regime": "RISK ON"}, tmp_path, max_age_days=0
        )
        rotate_snapshot(
            {"timestamp": (base + timedelta(hours=24)).isoformat(), "market_regime": "RISK OFF"},
            tmp_path, max_age_days=0,
        )

        result = validation_trend_snapshot(
            reports_dir=str(tmp_path), history_dir=str(history_dir), output_dir=tmp_path
        )
        assert [r["strategy_id"] for r in result["strategies"]] == ["duo"]
        assert result["strategies_reason"] is None
        assert [p["report_date"] for p in result["trend"]["duo"]] == ["2026-06-01", "2026-06-15"]
        assert result["trend_reason"] is None
        assert [t["market_regime"] for t in result["regime_timeline"]] == ["RISK ON", "RISK OFF"]
        assert result["n_rotated_snapshots"] == 2
        assert result["regime_reason"] is None
