"""Regression tests for StrategyValidationHarness.run()'s Calmar guard.

Bug this locks in: both Calmar calcs in validation/harness.py::run() (the
in-sample branch and the settings.VALIDATION_HARNESS_OOS_GATE_ENABLED
branch) guarded their `max_dd` denominator with an exact `max_dd > 0` check
-- the same fragile-equality shape as the Sharpe/Sortino bug fixed in PR #501
(validation/metrics.py::sharpe_ratio, and the Sortino calc four lines above
each Calmar calc in this same file), which that PR did not also apply here.

Note on reproduction: unlike returns.std()'s two-pass sum-of-squared-
deviations algorithm (which measurably accumulates floating-point rounding
noise into a near-zero-but-nonzero result for a degenerate constant series --
see tests/test_metrics_sharpe_ratio.py), compute_max_drawdown's cumprod/
cummax/subtraction pipeline does not appear to accumulate noise the same
way: a genuinely flat/degenerate returns series produces an exactly
bit-identical 0.0 max_dd (already correctly handled pre-fix), not a noisy
near-zero value. This suite therefore exercises the guard directly at its
1e-12 threshold (monkeypatching compute_max_drawdown to a controlled value)
rather than claiming a specific real-world return series reproduces it --
this is a defensive consistency fix, not a confirmed live incident like the
Sharpe/Sortino one.
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


def _make_harness(tmp_path, strategy_fn):
    return StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda _d: ["SYN"],
        cost_model=TieredCostModel(),
        n_cpcv_splits=5,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )


def _fixed_returns_strategy_fn(idx):
    rng = np.random.default_rng(42)
    rets = pd.Series(rng.normal(0.0004, 0.006, size=len(idx)), index=idx)

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "synthetic",
            "train_returns": rets.reindex(y_train.index).dropna(),
            "test_returns": rets.reindex(y_test.index).dropna(),
            "turnover": 0.03,
        }]
    return strategy_fn


class TestInSampleCalmarGuard:
    def test_near_zero_max_dd_is_nan_not_absurd(self, tmp_path, monkeypatch):
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, _fixed_returns_strategy_fn(X.index))
        # Force the exact floating-point-noise magnitude observed for the
        # sibling std() bug (~1e-16) onto max_dd, below the 1e-12 floor.
        monkeypatch.setattr(harness_module, "compute_max_drawdown", lambda _r: 9.9e-17)

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert np.isnan(report.calmar), f"expected NaN, got an absurd value: {report.calmar}"

    def test_real_drawdown_computes_a_finite_calmar(self, tmp_path, monkeypatch):
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, _fixed_returns_strategy_fn(X.index))
        monkeypatch.setattr(harness_module, "compute_max_drawdown", lambda _r: 0.12)

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert np.isfinite(report.calmar)

    def test_exactly_zero_max_dd_is_nan(self, tmp_path, monkeypatch):
        """Baseline: bit-identical zero was already handled pre-fix."""
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, _fixed_returns_strategy_fn(X.index))
        monkeypatch.setattr(harness_module, "compute_max_drawdown", lambda _r: 0.0)

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert np.isnan(report.calmar)
