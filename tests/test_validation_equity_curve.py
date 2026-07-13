"""Tests for the per-Pilot equity curve persisted by
``validation.harness.StrategyValidationHarness`` (D2 decision) — the
60/40 walk-forward split's held-out, out-of-sample test-period returns,
converted to a cumulative equity series and written to
``reports/<strategy>_equity_curve.json`` for ``pilots/performance.py`` to
read.

Fully offline: synthetic X/y are passed directly to ``run()`` so no live
Yahoo Finance download occurs.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness


def _synthetic_xy(n_days: int = 60, daily_return: float = 0.001, seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    returns = pd.Series(
        daily_return + rng.normal(0, 0.0005, size=n_days), index=dates
    )
    X = pd.DataFrame({"close_lag1": (1 + returns).cumprod()}, index=dates)
    return X, returns


def _constant_return_strategy(X_train, y_train, X_test, y_test):
    """A trivial strategy: always long, test_returns == y_test verbatim."""
    return [
        {"params": "always_long", "train_returns": y_train, "test_returns": y_test, "turnover": 0.05}
    ]


def _no_trials_strategy(X_train, y_train, X_test, y_test):
    return []


def _make_harness(strategy_fn, reports_dir):
    return StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda as_of: ["SPY"],
        cost_model=TieredCostModel(),
        reports_dir=str(reports_dir),
    )


class TestEquityCurveWritten:
    def test_run_writes_equity_curve_file_with_expected_shape(self, tmp_path):
        X, y = _synthetic_xy()
        harness = _make_harness(_constant_return_strategy, tmp_path)
        harness.run(start_date="2024-01-02", end_date="2024-03-31", X=X, y=y, strategy_name="CurveTest")

        curve_path = Path(tmp_path) / "CurveTest_equity_curve.json"
        assert curve_path.exists()
        data = json.loads(curve_path.read_text(encoding="utf-8"))
        assert data["strategy"] == "CurveTest"
        assert data["source"] == "walk_forward_60_40_test_period"
        assert "out-of-sample" in data["note"].lower()
        assert "full-sample" in data["note"].lower()
        assert len(data["points"]) > 0
        for p in data["points"]:
            assert set(p.keys()) == {"date", "value"}
            # Date is a real ISO string, not a fabricated placeholder.
            assert len(p["date"]) == 10 and p["date"][4] == "-"

    def test_curve_points_are_a_genuine_cumulative_product(self, tmp_path):
        # Deterministic, no-noise strategy: every day the SAME positive
        # return net of a fixed cost -> the curve must be a real cumprod,
        # not a fabricated straight line or a copy of the raw returns.
        n_days = 60
        dates = pd.bdate_range("2024-01-02", periods=n_days)
        flat_return = 0.002
        y = pd.Series(flat_return, index=dates)
        X = pd.DataFrame({"close_lag1": (1 + y).cumprod()}, index=dates)

        def flat_strategy(X_train, y_train, X_test, y_test):
            return [{"params": "flat", "train_returns": y_train, "test_returns": y_test, "turnover": 0.0}]

        harness = _make_harness(flat_strategy, tmp_path)
        harness.run(start_date=str(dates[0].date()), end_date=str(dates[-1].date()), X=X, y=y, strategy_name="FlatTest")

        data = json.loads((Path(tmp_path) / "FlatTest_equity_curve.json").read_text(encoding="utf-8"))
        values = [p["value"] for p in data["points"]]
        # Monotonically increasing (every day the same positive net return).
        assert all(b > a for a, b in zip(values, values[1:]))
        # First point should be close to (1 + flat_return), not exactly 1.0
        # (this is a real cumprod starting from the first held-out day's
        # return, not a fabricated "start at par" placeholder).
        assert values[0] == pytest.approx(1 + flat_return, rel=1e-6)

    def test_no_trials_at_all_skips_curve_write_gracefully(self, tmp_path):
        X, y = _synthetic_xy()
        harness = _make_harness(_no_trials_strategy, tmp_path)
        report = harness.run(start_date="2024-01-02", end_date="2024-03-31", X=X, y=y, strategy_name="EmptyTest")

        assert not (Path(tmp_path) / "EmptyTest_equity_curve.json").exists()
        # The rest of the run must still succeed (dead-letter, don't crash).
        assert report is not None
        assert (Path(tmp_path) / "EmptyTest_validation_summary.json").exists()

    def test_summary_dict_and_history_never_carry_curve_data(self, tmp_path):
        """The equity curve must live in its own sibling file, never inside
        to_summary_dict() / the run-history JSONL, so the append-only
        history file doesn't grow unbounded with a full return series
        every run."""
        X, y = _synthetic_xy()
        harness = _make_harness(_constant_return_strategy, tmp_path)
        report = harness.run(start_date="2024-01-02", end_date="2024-03-31", X=X, y=y, strategy_name="NoBloatTest")

        summary = report.to_summary_dict()
        assert "points" not in summary
        assert "curve" not in summary
        assert "equity_curve" not in summary

        history_path = Path(tmp_path) / "history" / "NoBloatTest_validation_history.jsonl"
        assert history_path.exists()
        for line in history_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert "points" not in row
            assert "curve" not in row


class TestWriteEquityCurveUnit:
    """Direct unit tests of _write_equity_curve, bypassing the full run()."""

    def test_none_returns_writes_nothing(self, tmp_path):
        harness = _make_harness(_constant_return_strategy, tmp_path)

        class _FakeReport:
            name = "UnitTest"

        harness._write_equity_curve(_FakeReport(), None)
        assert not list(Path(tmp_path).glob("*_equity_curve.json"))

    def test_all_nan_returns_writes_nothing(self, tmp_path):
        harness = _make_harness(_constant_return_strategy, tmp_path)

        class _FakeReport:
            name = "UnitTest"

        nan_returns = pd.Series([np.nan, np.nan, np.nan], index=pd.bdate_range("2024-01-02", periods=3))
        harness._write_equity_curve(_FakeReport(), nan_returns)
        assert not list(Path(tmp_path).glob("*_equity_curve.json"))

    def test_empty_series_writes_nothing(self, tmp_path):
        harness = _make_harness(_constant_return_strategy, tmp_path)

        class _FakeReport:
            name = "UnitTest"

        harness._write_equity_curve(_FakeReport(), pd.Series(dtype=float))
        assert not list(Path(tmp_path).glob("*_equity_curve.json"))

    def test_name_with_spaces_and_slashes_is_sanitized(self, tmp_path):
        harness = _make_harness(_constant_return_strategy, tmp_path)

        class _FakeReport:
            name = "Weird Strategy/Name"

        returns = pd.Series([0.01, 0.02], index=pd.bdate_range("2024-01-02", periods=2))
        harness._write_equity_curve(_FakeReport(), returns)
        assert (Path(tmp_path) / "Weird_Strategy_Name_equity_curve.json").exists()
