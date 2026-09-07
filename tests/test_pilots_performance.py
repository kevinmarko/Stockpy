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
    _macro_benchmark_staleness_note,
    _slice_curve_by_range,
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
        # news-catalyst is (as of 2026-07) the only Pilot still permanently
        # validation_strategy_id=None.
        pilot = get_pilot("news-catalyst")  # validation_strategy_id is None
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
        # The fixture also carries a persisted benchmark_curve -> a real, sliced
        # benchmark series is served alongside the strategy curve.
        benchmark = perf["benchmark"]
        assert isinstance(benchmark, list) and len(benchmark) >= 2
        assert all(set(p) == {"date", "value"} for p in benchmark)
        assert all(isinstance(p["value"], (int, float)) for p in benchmark)
        # The fixture also carries a persisted macro_benchmark_curve (SPY) -> a
        # real, sliced, SEPARATELY-labeled market overlay is served too.
        macro = perf["macro_benchmark"]
        assert isinstance(macro, list) and len(macro) >= 2
        assert all(set(p) == {"date", "value"} for p in macro)
        assert all(isinstance(p["value"], (int, float)) for p in macro)
        assert perf["reason"] is None
        assert perf["range"] == "2Y"

    def test_none_validation_id_is_honest_null(self):
        # news-catalyst is (as of 2026-07) the only Pilot still permanently
        # validation_strategy_id=None -- macro_regime_pit/forecast_direction_arima_hw/
        # signal_replay_balanced_blend all shipped real backtests this quarter.
        pilot = get_pilot("news-catalyst")
        perf = pilot_performance(pilot, reports_dir=FIXTURES_DIR)
        assert perf["metrics"] is None
        assert perf["curve"] is None
        assert perf["benchmark"] is None
        assert perf["macro_benchmark"] is None
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


# ---------------------------------------------------------------------------
# pilot_performance — benchmark series (persisted buy-&-hold of the underlying)
# ---------------------------------------------------------------------------
class TestPilotPerformanceBenchmark:
    def test_benchmark_present_when_persisted(self):
        """The shared fixture carries benchmark_curve -> a real sliced benchmark."""
        pilot = get_pilot("trend-following")
        perf = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)
        bench = perf["benchmark"]
        assert isinstance(bench, list) and len(bench) >= 2
        assert all(set(p) == {"date", "value"} for p in bench)
        assert all(isinstance(p["value"], (int, float)) for p in bench)

    def test_benchmark_is_tail_sliced_like_curve(self):
        """Shorter ranges zoom the benchmark the same way as the strategy curve:
        fewer-or-equal points and the same latest point (a pure tail slice)."""
        pilot = get_pilot("trend-following")
        full = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)["benchmark"]
        one_y = pilot_performance(pilot, range="1Y", reports_dir=FIXTURES_DIR)["benchmark"]
        one_m = pilot_performance(pilot, range="1M", reports_dir=FIXTURES_DIR)["benchmark"]
        assert full and one_y and one_m
        assert len(one_m) <= len(one_y) <= len(full)
        assert len(one_m) >= 2
        assert one_m[-1] == one_y[-1] == full[-1]

    def test_absent_benchmark_key_is_honest_none(self, tmp_path):
        """A summary that predates benchmark_curve -> benchmark None, never
        fabricated, even when a real equity_curve is present (independent)."""
        (tmp_path / "nobench_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "nobench",
                "sharpe": 1.0,
                "deployable": True,
                "equity_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 101.2},
                    {"date": "2024-03-31", "value": 103.4},
                ],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "nobench"

        perf = pilot_performance(_P(), range="1Y", reports_dir=str(tmp_path))
        assert isinstance(perf["curve"], list) and len(perf["curve"]) >= 2
        assert perf["benchmark"] is None

    def test_empty_benchmark_list_is_honest_none(self, tmp_path):
        """An explicitly-empty benchmark_curve ([] — the honest 'no meaningful
        underlying series' sentinel) surfaces as None, never a synthesized line."""
        (tmp_path / "emptybench_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "emptybench",
                "sharpe": 1.0,
                "deployable": True,
                "equity_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 101.2},
                ],
                "benchmark_curve": [],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "emptybench"

        perf = pilot_performance(_P(), reports_dir=str(tmp_path))
        assert perf["benchmark"] is None

    def test_benchmark_surfaces_even_when_strategy_curve_absent(self, tmp_path):
        """benchmark is independent of curve: a summary with only benchmark_curve
        still surfaces the benchmark (honest), with curve None + honest reason."""
        (tmp_path / "benchonly_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "benchonly",
                "sharpe": 1.0,
                "deployable": True,
                "benchmark_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 100.9},
                    {"date": "2024-03-31", "value": 102.1},
                ],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "benchonly"

        perf = pilot_performance(_P(), reports_dir=str(tmp_path))
        assert perf["curve"] is None
        assert perf["reason"] == "no backtest series persisted"
        assert isinstance(perf["benchmark"], list) and len(perf["benchmark"]) >= 2


# ---------------------------------------------------------------------------
# pilot_performance — macro_benchmark series (SEPARATE labeled SPY overlay)
# ---------------------------------------------------------------------------
class TestPilotPerformanceMacroBenchmark:
    def test_macro_benchmark_present_when_persisted(self):
        """The shared fixture carries macro_benchmark_curve -> a real sliced,
        separately-labeled SPY overlay."""
        pilot = get_pilot("trend-following")
        perf = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)
        macro = perf["macro_benchmark"]
        assert isinstance(macro, list) and len(macro) >= 2
        assert all(set(p) == {"date", "value"} for p in macro)
        assert all(isinstance(p["value"], (int, float)) for p in macro)

    def test_macro_benchmark_is_tail_sliced_like_curve(self):
        """Shorter ranges zoom the macro overlay the same way as the strategy
        curve: fewer-or-equal points and the same latest point (a pure slice)."""
        pilot = get_pilot("trend-following")
        full = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)["macro_benchmark"]
        one_y = pilot_performance(pilot, range="1Y", reports_dir=FIXTURES_DIR)["macro_benchmark"]
        one_m = pilot_performance(pilot, range="1M", reports_dir=FIXTURES_DIR)["macro_benchmark"]
        assert full and one_y and one_m
        assert len(one_m) <= len(one_y) <= len(full)
        assert len(one_m) >= 2
        assert one_m[-1] == one_y[-1] == full[-1]

    def test_macro_benchmark_is_independent_of_benchmark(self):
        """macro_benchmark and benchmark are DISTINCT keys/series — both can be
        present and independently populated (not aliases of each other)."""
        pilot = get_pilot("trend-following")
        perf = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)
        assert perf["benchmark"] is not None
        assert perf["macro_benchmark"] is not None
        assert perf["macro_benchmark"] is not perf["benchmark"]

    def test_absent_macro_benchmark_key_is_honest_none(self, tmp_path):
        """A summary that predates macro_benchmark_curve -> macro_benchmark None,
        never fabricated, even when equity_curve and benchmark_curve are present."""
        (tmp_path / "nomacro_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "nomacro",
                "sharpe": 1.0,
                "deployable": True,
                "equity_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 101.2},
                    {"date": "2024-03-31", "value": 103.4},
                ],
                "benchmark_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 100.7},
                    {"date": "2024-03-31", "value": 101.9},
                ],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "nomacro"

        perf = pilot_performance(_P(), range="1Y", reports_dir=str(tmp_path))
        assert isinstance(perf["curve"], list) and len(perf["curve"]) >= 2
        assert isinstance(perf["benchmark"], list) and len(perf["benchmark"]) >= 2
        assert perf["macro_benchmark"] is None

    def test_empty_macro_benchmark_list_is_honest_none(self, tmp_path):
        """An explicitly-empty macro_benchmark_curve ([] — the honest 'SPY
        unavailable / underlying already IS SPY' sentinel) surfaces as None,
        never a synthesized line (CONSTRAINT #4)."""
        (tmp_path / "emptymacro_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "emptymacro",
                "sharpe": 1.0,
                "deployable": True,
                "equity_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 101.2},
                ],
                "macro_benchmark_curve": [],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "emptymacro"

        perf = pilot_performance(_P(), reports_dir=str(tmp_path))
        assert perf["macro_benchmark"] is None

    def test_macro_benchmark_surfaces_even_when_strategy_curve_absent(self, tmp_path):
        """macro_benchmark is independent of curve: a summary with only
        macro_benchmark_curve still surfaces it, with curve None + honest reason."""
        (tmp_path / "macroonly_validation_summary.json").write_text(
            json.dumps({
                "strategy_id": "macroonly",
                "sharpe": 1.0,
                "deployable": True,
                "macro_benchmark_curve": [
                    {"date": "2024-01-31", "value": 100.0},
                    {"date": "2024-02-29", "value": 100.4},
                    {"date": "2024-03-31", "value": 101.6},
                ],
            }),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = "macroonly"

        perf = pilot_performance(_P(), reports_dir=str(tmp_path))
        assert perf["curve"] is None
        assert perf["reason"] == "no backtest series persisted"
        assert isinstance(perf["macro_benchmark"], list) and len(perf["macro_benchmark"]) >= 2


# ---------------------------------------------------------------------------
# _slice_curve_by_range's anchor_date param
# ---------------------------------------------------------------------------
class TestSliceCurveByRangeAnchorDate:
    def _monthly(self, start_year, start_month, n):
        pts = []
        y, m = start_year, start_month
        for _ in range(n):
            pts.append({"date": f"{y:04d}-{m:02d}-28", "value": 100.0})
            m += 1
            if m > 12:
                m = 1
                y += 1
        return pts

    def test_anchor_date_omitted_reproduces_self_anchored_behavior(self):
        """Default (no anchor_date) is byte-identical to today's behavior."""
        curve = self._monthly(2022, 1, 24)  # 2022-01 .. 2023-12
        sliced_default = _slice_curve_by_range(curve, "1Y")
        sliced_explicit_self = _slice_curve_by_range(
            curve, "1Y", anchor_date=curve[-1]["date"]
        )
        assert sliced_default == sliced_explicit_self

    def test_anchor_date_windows_relative_to_anchor_not_own_last_point(self):
        """A shorter series anchored to a LATER date gets a stricter (later)
        cutoff than self-anchoring would -- fewer, more-recent-relative-to-
        the-anchor points, not just "whatever its own tail happens to be"."""
        curve = self._monthly(2022, 1, 24)  # ends 2023-12
        self_anchored = _slice_curve_by_range(curve, "1Y")  # anchors to 2023-12-28
        later_anchored = _slice_curve_by_range(curve, "1Y", anchor_date="2024-06-28")
        assert self_anchored[-1] == curve[-1]  # last point never changes
        assert later_anchored[-1] == curve[-1]
        # A later anchor pushes the cutoff later too -> a stricter (shorter
        # or equal) window than self-anchoring the same series.
        assert len(later_anchored) <= len(self_anchored)

    def test_anchor_far_past_all_points_falls_back_to_last_two(self):
        """When every point predates the anchor-derived cutoff, the existing
        'never return fewer than 2 points' fallback still applies -- returns
        the series' own last two REAL points rather than an empty list."""
        curve = self._monthly(2020, 1, 3)  # 2020-01, 02, 03
        sliced = _slice_curve_by_range(curve, "1M", anchor_date="2024-06-30")
        assert sliced == curve[-2:]

    def test_unparseable_anchor_date_returns_full_curve(self):
        curve = self._monthly(2022, 1, 24)
        assert _slice_curve_by_range(curve, "1Y", anchor_date="not-a-date") == curve


# ---------------------------------------------------------------------------
# _macro_benchmark_staleness_note
# ---------------------------------------------------------------------------
class TestMacroBenchmarkStalenessNote:
    def test_no_anchor_yields_none(self):
        macro = [{"date": "2024-01-31", "value": 100.0}, {"date": "2024-02-29", "value": 101.0}]
        assert _macro_benchmark_staleness_note(None, macro, macro) is None

    def test_no_macro_benchmark_yields_none(self):
        raw = [{"date": "2024-01-31", "value": 100.0}, {"date": "2024-02-29", "value": 101.0}]
        assert _macro_benchmark_staleness_note("2024-06-30", raw, None) is None

    def test_exact_match_yields_none(self):
        raw = [{"date": "2024-01-31", "value": 100.0}, {"date": "2024-06-30", "value": 101.0}]
        sliced = raw
        assert _macro_benchmark_staleness_note("2024-06-30", raw, sliced) is None

    def test_small_gap_within_tolerance_yields_none(self):
        raw = [{"date": "2024-01-31", "value": 100.0}, {"date": "2024-06-27", "value": 101.0}]
        sliced = raw
        # 2024-06-30 - 2024-06-27 = 3 days <= _MACRO_BENCHMARK_STALE_TOLERANCE_DAYS (5)
        assert _macro_benchmark_staleness_note("2024-06-30", raw, sliced) is None

    def test_large_gap_yields_a_note_naming_the_date_and_gap(self):
        raw = [{"date": "2024-01-31", "value": 100.0}, {"date": "2024-03-31", "value": 102.0}]
        sliced = raw
        note = _macro_benchmark_staleness_note("2024-06-30", raw, sliced)
        assert note is not None
        assert "2024-03-31" in note
        assert "91" in note  # (2024-06-30 - 2024-03-31).days == 91

    def test_macro_ahead_of_curve_yields_none(self):
        """A negative gap (macro's real data extends PAST the curve's own
        last date) has nothing to disclose."""
        raw = [{"date": "2024-01-31", "value": 100.0}, {"date": "2024-09-30", "value": 105.0}]
        sliced = raw
        assert _macro_benchmark_staleness_note("2024-06-30", raw, sliced) is None

    def test_never_raises_on_malformed_raw_macro_benchmark(self):
        assert _macro_benchmark_staleness_note("2024-06-30", "not-a-list", ["x"]) is None
        assert _macro_benchmark_staleness_note("2024-06-30", [{"date": None}], ["x"]) is None


# ---------------------------------------------------------------------------
# pilot_performance() end-to-end: staleness note + anchored slicing
# ---------------------------------------------------------------------------
class TestPilotPerformanceMacroBenchmarkStaleness:
    def _write(self, tmp_path, name, **fields):
        (tmp_path / f"{name}_validation_summary.json").write_text(
            json.dumps({"strategy_id": name, "sharpe": 1.0, "deployable": True, **fields}),
            encoding="utf-8",
        )

        class _P:
            validation_strategy_id = name

        return _P()

    def test_stale_macro_curve_surfaces_a_note(self, tmp_path):
        pilot = self._write(
            tmp_path,
            "stalemacro",
            equity_curve=[
                {"date": "2024-01-31", "value": 100.0},
                {"date": "2024-02-29", "value": 102.0},
                {"date": "2024-03-31", "value": 104.0},
                {"date": "2024-04-30", "value": 106.0},
                {"date": "2024-05-31", "value": 108.0},
                {"date": "2024-06-30", "value": 110.0},
            ],
            macro_benchmark_curve=[
                {"date": "2024-01-31", "value": 100.0},
                {"date": "2024-02-29", "value": 101.0},
                {"date": "2024-03-31", "value": 102.0},
            ],
        )
        perf = pilot_performance(pilot, range="2Y", reports_dir=str(tmp_path))
        assert perf["curve"][-1]["date"] == "2024-06-30"
        assert perf["macro_benchmark"] is not None
        assert perf["macro_benchmark"][-1]["date"] == "2024-03-31"
        assert perf["macro_benchmark_note"] is not None
        assert "2024-03-31" in perf["macro_benchmark_note"]
        assert "91" in perf["macro_benchmark_note"]

    def test_in_sync_macro_curve_has_no_note(self, tmp_path):
        pilot = self._write(
            tmp_path,
            "syncmacro",
            equity_curve=[
                {"date": "2024-01-31", "value": 100.0},
                {"date": "2024-06-30", "value": 110.0},
            ],
            macro_benchmark_curve=[
                {"date": "2024-01-31", "value": 100.0},
                {"date": "2024-06-30", "value": 105.0},
            ],
        )
        perf = pilot_performance(pilot, range="2Y", reports_dir=str(tmp_path))
        assert perf["macro_benchmark_note"] is None

    def test_no_curve_available_has_no_note(self, tmp_path):
        """Mirrors test_macro_benchmark_surfaces_even_when_strategy_curve_absent
        (no equity_curve at all) -- there's no anchor to compare against, so
        the note stays honestly None rather than guessing."""
        pilot = self._write(
            tmp_path,
            "nocurve",
            macro_benchmark_curve=[
                {"date": "2024-01-31", "value": 100.0},
                {"date": "2024-03-31", "value": 101.6},
            ],
        )
        perf = pilot_performance(pilot, reports_dir=str(tmp_path))
        assert perf["curve"] is None
        assert perf["macro_benchmark"] is not None
        assert perf["macro_benchmark_note"] is None

    def test_every_return_branch_carries_the_macro_benchmark_note_key(self, tmp_path):
        # no validation_strategy_id at all
        class _NoId:
            validation_strategy_id = None

        assert "macro_benchmark_note" in pilot_performance(_NoId())

        # validation_strategy_id set but no summary file on disk
        class _Missing:
            validation_strategy_id = "does-not-exist-anywhere"

        assert "macro_benchmark_note" in pilot_performance(_Missing(), reports_dir=str(tmp_path))

    def test_existing_healthy_fixture_regression_guard(self):
        """The one fixture with all three curves sharing an identical last
        date (tests/fixtures/timeseries_momentum_validation_summary.json)
        must still tail-slice byte-identically to before this change, and
        report no staleness note."""
        pilot = get_pilot("trend-following")
        full = pilot_performance(pilot, range="2Y", reports_dir=FIXTURES_DIR)
        one_y = pilot_performance(pilot, range="1Y", reports_dir=FIXTURES_DIR)
        one_m = pilot_performance(pilot, range="1M", reports_dir=FIXTURES_DIR)
        for perf in (full, one_y, one_m):
            assert perf["macro_benchmark_note"] is None
        assert len(one_m["macro_benchmark"]) <= len(one_y["macro_benchmark"]) <= len(full["macro_benchmark"])
        assert (
            one_m["macro_benchmark"][-1]
            == one_y["macro_benchmark"][-1]
            == full["macro_benchmark"][-1]
        )
