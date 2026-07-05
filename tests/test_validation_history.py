"""
tests/test_validation_history.py — run-over-run validation history persistence.

``StrategyValidationHarness`` writes a per-strategy CURRENT snapshot to
``reports/<strategy>_validation_summary.json`` that is overwritten on every
run (no time series). This suite covers the companion append-only history
file, ``reports/history/<strategy>_validation_history.jsonl`` (one row per
run, written by ``_append_validation_history`` and read back by the
module-level ``read_validation_history``), which lets PBO/DSR/Sharpe/MaxDD
be plotted as a trend across multiple harness runs.

All tests operate in an isolated ``tmp_path`` (via ``monkeypatch.chdir``) so
nothing is written into the real repo ``reports/`` directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from validation.harness import (
    MAX_VALIDATION_HISTORY_ROWS,
    StrategyValidationHarness,
    ValidationReport,
    read_validation_history,
)


def _make_report(name: str = "TestStrategy", *, sharpe: float = 0.8, dsr: float = 0.97, pbo: float = 0.3, max_dd: float = 0.15) -> ValidationReport:
    """Construct a minimal ValidationReport for history-persistence tests."""
    return ValidationReport(
        name=name,
        start_date="2020-01-01",
        end_date="2020-12-31",
        sharpe=sharpe,
        sortino=1.0,
        calmar=1.0,
        max_dd=max_dd,
        turnover=0.05,
        hit_rate=0.55,
        avg_trade_pct=0.01,
        dsr=dsr,
        pbo=pbo,
        bias_report={},
        walk_forward_60_40=0.5,
        walk_forward_70_30=0.5,
        walk_forward_80_20=0.5,
        distribution=np.array([0.1, 0.2, 0.3]),
        paths=[],
        n_trials=10,
    )


def _harness() -> StrategyValidationHarness:
    """A harness instance whose strategy_fn/universe_fn are never invoked —
    only needed to call the private _append_validation_history method."""
    from execution.cost_model import TieredCostModel

    return StrategyValidationHarness(
        strategy_fn=lambda *a, **kw: [],
        universe_fn=lambda d: [],
        cost_model=TieredCostModel(),
    )


class TestToSummaryDictSerializesFamilyDsr:
    """Regression coverage: ``family_multiple_testing["family_dsr"]`` holds
    ``FamilyDSRResult`` dataclass instances (validation/multiple_testing.py),
    which ``json.dumps`` cannot handle on its own. Before the fix, this
    silently broke BOTH the *_validation_summary.json snapshot's second
    write (family_multiple_testing regressed to None, swallowed by
    _write_json_summary's own try/except) and the new history append
    (dropped the row entirely) whenever the family sweep actually ran."""

    def test_family_dsr_dataclasses_are_json_serializable(self):
        from validation.multiple_testing import FamilyDSRResult

        report = _make_report()
        report.family_multiple_testing = {
            "strategy_ids": ["TestStrategy"],
            "bh_rejected": [True],
            "family_dsr": [
                FamilyDSRResult(
                    strategy_id="TestStrategy",
                    sharpe_observed=0.8,
                    n_trials_own=10,
                    n_trials_family=10,
                    dsr_single_strategy=0.97,
                    dsr_family_corrected=0.95,
                )
            ],
            "n_strategies": 1,
            "summary_text": "ok",
        }

        payload = report.to_summary_dict()
        # Must not raise TypeError: Object of type FamilyDSRResult is not
        # JSON serializable.
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded["family_multiple_testing"]["family_dsr"][0]["strategy_id"] == "TestStrategy"
        assert decoded["family_multiple_testing"]["family_dsr"][0]["dsr_family_corrected"] == pytest.approx(0.95)

    def test_history_append_survives_populated_family_multiple_testing(self, tmp_path, monkeypatch):
        from validation.multiple_testing import FamilyDSRResult

        monkeypatch.chdir(tmp_path)
        report = _make_report()
        report.family_multiple_testing = {
            "strategy_ids": ["TestStrategy"],
            "bh_rejected": [True],
            "family_dsr": [
                FamilyDSRResult(
                    strategy_id="TestStrategy",
                    sharpe_observed=0.8,
                    n_trials_own=10,
                    n_trials_family=10,
                    dsr_single_strategy=0.97,
                    dsr_family_corrected=0.95,
                )
            ],
            "n_strategies": 1,
            "summary_text": "ok",
        }

        harness = _harness()
        harness._append_validation_history(report)

        rows = read_validation_history("TestStrategy", history_dir=str(tmp_path / "reports" / "history"))
        assert len(rows) == 1
        assert rows[0]["family_multiple_testing"]["family_dsr"][0]["strategy_id"] == "TestStrategy"


class TestAppendValidationHistory:
    def test_creates_history_dir_and_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        harness = _harness()
        harness._append_validation_history(_make_report())

        dest = tmp_path / "reports" / "history" / "TestStrategy_validation_history.jsonl"
        assert dest.exists()
        rows = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        assert len(rows) == 1
        assert rows[0]["strategy_id"] == "TestStrategy"
        assert rows[0]["dsr"] == pytest.approx(0.97)

    def test_appends_across_multiple_runs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        harness = _harness()
        harness._append_validation_history(_make_report(sharpe=0.5))
        harness._append_validation_history(_make_report(sharpe=0.6))
        harness._append_validation_history(_make_report(sharpe=0.7))

        dest = tmp_path / "reports" / "history" / "TestStrategy_validation_history.jsonl"
        rows = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        assert [r["sharpe"] for r in rows] == [0.5, 0.6, 0.7]

    def test_caps_at_max_rows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        harness = _harness()
        n = MAX_VALIDATION_HISTORY_ROWS + 25
        for i in range(n):
            harness._append_validation_history(_make_report(sharpe=float(i)))

        dest = tmp_path / "reports" / "history" / "TestStrategy_validation_history.jsonl"
        rows = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        assert len(rows) == MAX_VALIDATION_HISTORY_ROWS
        # oldest rows trimmed, most recent retained in order
        assert rows[0]["sharpe"] == float(n - MAX_VALIDATION_HISTORY_ROWS)
        assert rows[-1]["sharpe"] == float(n - 1)

    def test_separate_strategies_do_not_collide(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        harness = _harness()
        harness._append_validation_history(_make_report(name="Strategy_A"))
        harness._append_validation_history(_make_report(name="Strategy_B"))

        hist_dir = tmp_path / "reports" / "history"
        assert (hist_dir / "Strategy_A_validation_history.jsonl").exists()
        assert (hist_dir / "Strategy_B_validation_history.jsonl").exists()

    def test_corrupt_existing_line_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        hist_dir = tmp_path / "reports" / "history"
        hist_dir.mkdir(parents=True)
        dest = hist_dir / "TestStrategy_validation_history.jsonl"
        dest.write_text("not valid json\n{\"strategy_id\": \"TestStrategy\", \"sharpe\": 0.1}\n")

        harness = _harness()
        harness._append_validation_history(_make_report(sharpe=0.9))

        rows = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        # corrupt line dropped, valid prior row kept, new row appended
        assert len(rows) == 2
        assert rows[0]["sharpe"] == pytest.approx(0.1)
        assert rows[1]["sharpe"] == pytest.approx(0.9)

    def test_failure_is_swallowed_not_raised(self, tmp_path, monkeypatch):
        """A write failure (e.g. reports/history path collides with a file)
        must be logged, never propagated (CONSTRAINT #6)."""
        monkeypatch.chdir(tmp_path)
        # Create "reports" as a FILE so mkdir(parents=True) inside the method fails.
        (tmp_path / "reports").write_text("not a directory")

        harness = _harness()
        harness._append_validation_history(_make_report())  # must not raise


class TestReadValidationHistory:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert read_validation_history("NoSuchStrategy", history_dir=str(tmp_path / "reports" / "history")) == []

    def test_round_trip_via_append(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        harness = _harness()
        harness._append_validation_history(_make_report(sharpe=0.4))
        harness._append_validation_history(_make_report(sharpe=0.9))

        rows = read_validation_history("TestStrategy", history_dir=str(tmp_path / "reports" / "history"))
        assert [r["sharpe"] for r in rows] == [0.4, 0.9]

    def test_corrupt_line_skipped(self, tmp_path):
        hist_dir = tmp_path / "reports" / "history"
        hist_dir.mkdir(parents=True)
        dest = hist_dir / "Weird_validation_history.jsonl"
        dest.write_text("{\"strategy_id\": \"Weird\", \"sharpe\": 0.2}\nnot json\n\n")

        rows = read_validation_history("Weird", history_dir=str(hist_dir))
        assert len(rows) == 1
        assert rows[0]["sharpe"] == pytest.approx(0.2)

    def test_name_sanitization_matches_write_path(self, tmp_path, monkeypatch):
        """Strategy names with spaces/slashes must resolve to the same
        sanitized filename on both the write and read paths."""
        monkeypatch.chdir(tmp_path)
        harness = _harness()
        harness._append_validation_history(_make_report(name="My Strategy/V2"))

        rows = read_validation_history("My Strategy/V2", history_dir=str(tmp_path / "reports" / "history"))
        assert len(rows) == 1


class TestRunAppendsHistoryOnce:
    def test_run_calls_append_validation_history_exactly_once(self, tmp_path, monkeypatch):
        """StrategyValidationHarness.run() must persist exactly one history
        row per run, regardless of whether the opportunistic family
        multiple-testing sweep (step 6b) succeeds or fails. Fully offline:
        X/y are supplied directly (no yfinance download) and the
        Wikipedia-backed survivorship-bias lookup is monkeypatched."""
        import validation.harness as harness_mod
        from execution.cost_model import TieredCostModel

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            harness_mod, "get_universe_with_survivorship_warning",
            lambda as_of_date: (["SPY"], {"n_current": 500, "n_at_date": 500}),
        )
        # Force the opportunistic family multiple-testing sweep to fail, to
        # prove the history append still happens exactly once on that path.
        monkeypatch.setattr(
            harness_mod, "compute_family_multiple_testing_report",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        rng = np.random.default_rng(seed=7)
        idx = pd.bdate_range(end="2020-12-31", periods=120)
        y = pd.Series(rng.normal(0.0005, 0.01, size=len(idx)), index=idx)
        X = pd.DataFrame({"lag1": y.shift(1).fillna(0.0)}, index=idx)

        def strategy_fn(X_train, y_train, X_test, y_test):
            return [{"params": "buy_and_hold", "train_returns": y_train, "test_returns": y_test}]

        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda d: ["SPY"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
        )

        calls = []
        monkeypatch.setattr(harness, "_append_validation_history", lambda report: calls.append(report))
        # HTML rendering loads a Jinja template from the real repo's reports/
        # dir, not tmp_path — irrelevant to this test, so stub it out.
        monkeypatch.setattr(harness, "_render_html_report", lambda report: None)

        report = harness.run(
            start_date="2020-01-01", end_date="2020-12-31",
            X=X, y=y, strategy_name="RunOnceTest",
        )

        assert len(calls) == 1
        assert calls[0] is report
