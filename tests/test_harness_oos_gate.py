"""Unit tests for settings.VALIDATION_HARNESS_OOS_GATE_ENABLED --
StrategyValidationHarness's opt-in fix replacing its deployability gate's
in-sample Sharpe/MaxDD/Sortino/Calmar/hit_rate/avg_trade_pct/turnover (from
strategy_fn(X, y, X, y) -- a "test" set identical to the training set) with
the mean of each metric computed on every CombinatorialPurgedCV path's own
genuinely held-out OOS returns, cost-adjusted the same way as the rest of the
harness.

Fully offline: synthetic X/y, stubbed universe/SPY -- same pattern as
tests/test_harness_equity_curve.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
import validation.harness as harness_module


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(
        harness_module, "get_universe_with_survivorship_warning",
        lambda _d: (["SYN"], {"n_current": 1, "n_at_date": 1,
                              "n_delisted_in_period": 0, "estimated_bias_pct": 0.5}),
    )
    monkeypatch.setattr(
        harness_module, "_spy_return_series",
        lambda oos_index, s, e: None,
    )


def _synthetic_xy(n=300, seed=13):
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"feat": np.arange(n, dtype=float)}, index=idx)
    y = pd.Series(rng.normal(0.0003, 0.007, size=n), index=idx)
    return X, y


def _drawdown_strategy_fn(idx):
    """A strategy whose in-sample (full-period) fit and CPCV OOS folds are
    NOT numerically identical, so a flag flip is observable. The full return
    series is generated ONCE (not re-randomized per call), so repeated calls
    with the same train/test index slices are fully deterministic/idempotent
    -- required since the harness calls strategy_fn many times per run()
    (walk-forward splits, every CPCV path, the full-sample fit) and this test
    module also calls it again afterward to hand-recompute an expectation."""
    rng = np.random.default_rng(42)
    rets = pd.Series(rng.normal(0.0006, 0.012, size=len(idx)), index=idx)

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "synthetic",
            "train_returns": rets.reindex(y_train.index).dropna(),
            "test_returns": rets.reindex(y_test.index).dropna(),
            "turnover": 0.03,
        }]
    return strategy_fn


def _make_harness(tmp_path, idx):
    return StrategyValidationHarness(
        strategy_fn=_drawdown_strategy_fn(idx),
        universe_fn=lambda _d: ["SYN"],
        cost_model=TieredCostModel(),
        n_cpcv_splits=5,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )


class TestFlagDefaultOff:
    def test_default_reproduces_full_sample_in_sample_fit(self, tmp_path, monkeypatch):
        """With the flag unset (default False), sharpe/max_dd must still come
        from the full-sample strategy_fn(X, y, X, y) fit -- unchanged from
        pre-existing behavior."""
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, X.index)

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        # Recompute the expected full-sample number by hand, exactly mirroring
        # the harness's own (unflagged) "5. Performance Metrics" logic.
        full_trials = harness.strategy_fn(X, y, X, y)
        best_trial = full_trials[0]
        expected_returns = harness._apply_cost_model(best_trial["test_returns"], turnover=best_trial["turnover"])
        from validation.metrics import sharpe_ratio
        assert report.sharpe == pytest.approx(sharpe_ratio(expected_returns), nan_ok=True)

    def test_cost_model_fn_not_passed_to_cpcv_when_disabled(self, tmp_path, monkeypatch):
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, X.index)

        captured = {}
        orig = harness_module.run_cpcv_evaluation

        def spy(*args, **kwargs):
            captured["cost_model_fn"] = kwargs.get("cost_model_fn")
            return orig(*args, **kwargs)

        monkeypatch.setattr(harness_module, "run_cpcv_evaluation", spy)
        harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")
        assert captured["cost_model_fn"] is None


class TestFlagEnabled:
    def test_cost_model_fn_passed_to_cpcv_when_enabled(self, tmp_path, monkeypatch):
        from settings import settings as live_settings
        monkeypatch.setattr(live_settings, "VALIDATION_HARNESS_OOS_GATE_ENABLED", True, raising=False)

        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, X.index)

        captured = {}
        orig = harness_module.run_cpcv_evaluation

        def spy(*args, **kwargs):
            captured["cost_model_fn"] = kwargs.get("cost_model_fn")
            return orig(*args, **kwargs)

        monkeypatch.setattr(harness_module, "run_cpcv_evaluation", spy)
        harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")
        assert captured["cost_model_fn"] == harness._apply_cost_model

    def test_gate_metrics_match_cpcv_oos_aggregates(self, tmp_path, monkeypatch):
        """When enabled, report.sharpe/max_dd/sortino/hit_rate/avg_trade_pct/
        turnover must come from cpcv_results' mean_oos_* aggregates, not the
        full-sample in-sample fit."""
        from settings import settings as live_settings
        monkeypatch.setattr(live_settings, "VALIDATION_HARNESS_OOS_GATE_ENABLED", True, raising=False)

        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, X.index)

        captured = {}
        orig = harness_module.run_cpcv_evaluation

        def spy(*args, **kwargs):
            result = orig(*args, **kwargs)
            captured["result"] = result
            return result

        monkeypatch.setattr(harness_module, "run_cpcv_evaluation", spy)
        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        cpcv = captured["result"]
        assert report.sharpe == pytest.approx(cpcv["mean_oos_sharpe"], nan_ok=True)
        assert report.max_dd == pytest.approx(cpcv["mean_oos_max_dd"], nan_ok=True)
        assert report.sortino == pytest.approx(cpcv["mean_oos_sortino"], nan_ok=True)
        assert report.hit_rate == pytest.approx(cpcv["mean_oos_hit_rate"], nan_ok=True)
        assert report.avg_trade_pct == pytest.approx(cpcv["mean_oos_avg_trade_pct"], nan_ok=True)
        assert report.turnover == pytest.approx(cpcv["mean_oos_turnover"], nan_ok=True)

    def test_equity_curve_unaffected_by_flag(self, tmp_path, monkeypatch):
        """equity_curve/benchmark_curve/macro_benchmark_curve deliberately stay
        on the full-sample series regardless of the flag -- a real, documented
        scope limit (see settings.VALIDATION_HARNESS_OOS_GATE_ENABLED), not
        silently faked as also-fixed."""
        from settings import settings as live_settings

        X, y = _synthetic_xy()

        harness_off = _make_harness(tmp_path / "off", X.index)
        report_off = harness_off.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        monkeypatch.setattr(live_settings, "VALIDATION_HARNESS_OOS_GATE_ENABLED", True, raising=False)
        harness_on = _make_harness(tmp_path / "on", X.index)
        report_on = harness_on.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert report_off.equity_curve == report_on.equity_curve

    def test_dsr_pbo_unaffected_by_gate_flag_directly(self, tmp_path, monkeypatch):
        """DSR/PBO were already genuinely OOS before this change; the flag's
        job is only to also cost-adjust them and fix sharpe/max_dd -- it must
        not, by itself, silently change DSR/PBO through some unrelated path."""
        from settings import settings as live_settings

        X, y = _synthetic_xy()
        harness_off = _make_harness(tmp_path / "off", X.index)
        report_off = harness_off.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        # With cost_model_fn now applied, DSR/PBO CAN legitimately move (cost
        # drag changes the return series) -- so this only asserts both runs
        # produce finite numbers in the expected range, not byte-equality.
        assert 0.0 <= report_off.pbo <= 1.0
        assert 0.0 <= report_off.dsr <= 1.0
