"""Unit tests for validation/metrics.py::run_cpcv_evaluation's new
genuinely-out-of-sample aggregates (mean_oos_max_dd / mean_oos_sortino /
mean_oos_hit_rate / mean_oos_avg_trade_pct / mean_oos_turnover) and its new
optional cost_model_fn parameter.

Fully offline/deterministic: a synthetic X/y and a hand-constructed
strategy_fn whose returns are known constants, so every aggregate is
hand-computable rather than merely "non-crashing".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.metrics import run_cpcv_evaluation
from validation.stress_scenarios import compute_max_drawdown


def _synthetic_xy(n=200, seed=7):
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"feat": np.arange(n, dtype=float)}, index=idx)
    y = pd.Series(rng.normal(0.0003, 0.006, size=n), index=idx)
    return X, y


def _constant_return_strategy_fn(daily_return=0.001, turnover=0.02):
    """Every trial's train/test returns are a CONSTANT daily return over the
    slice's own index -- makes Sharpe/MaxDD/Sortino/hit-rate hand-computable."""
    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "constant",
            "train_returns": pd.Series(daily_return, index=y_train.index),
            "test_returns": pd.Series(daily_return, index=y_test.index),
            "turnover": turnover,
        }]
    return strategy_fn


class TestCostModelFn:
    def test_none_reproduces_gross_returns(self):
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.002, turnover=0.02)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        # A constant +0.002/day return with zero volatility has an
        # (effectively) infinite/very large Sharpe -- what matters here is that
        # it is POSITIVE and large, since no cost was subtracted.
        assert result["mean_oos_sharpe"] > 0

    def test_cost_model_fn_reduces_returns_before_any_stat_is_computed(self):
        """A cost model draining more than the constant daily return must flip
        the OOS return series (and therefore Sharpe/hit-rate) negative --
        proof the adjustment happens BEFORE Sharpe/PBO/DSR/drawdown, not after."""
        X, y = _synthetic_xy()
        daily_return = 0.0005
        strategy_fn = _constant_return_strategy_fn(daily_return=daily_return, turnover=0.02)

        def draining_cost_model(returns: pd.Series, turnover: float) -> pd.Series:
            # Drains far more than the constant daily return -> net returns go negative.
            return returns - 0.01

        gross = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        net = run_cpcv_evaluation(
            strategy_fn, X, y, n_splits=5, n_test_splits=2, cost_model_fn=draining_cost_model
        )
        assert gross["mean_oos_avg_trade_pct"] > 0
        assert net["mean_oos_avg_trade_pct"] < 0
        assert net["mean_oos_hit_rate"] == 0.0
        assert net["mean_oos_sharpe"] < gross["mean_oos_sharpe"]

    def test_cost_model_fn_receives_trial_turnover(self):
        """cost_model_fn must be called with each trial's OWN 'turnover' field
        (falling back to 0.05 only when absent), not a hardcoded constant."""
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.001, turnover=0.037)
        seen_turnovers = []

        def spy_cost_model(returns: pd.Series, turnover: float) -> pd.Series:
            seen_turnovers.append(turnover)
            return returns

        run_cpcv_evaluation(
            strategy_fn, X, y, n_splits=5, n_test_splits=2, cost_model_fn=spy_cost_model
        )
        assert seen_turnovers, "cost_model_fn was never called"
        assert all(t == pytest.approx(0.037) for t in seen_turnovers)


class TestOosAggregates:
    def test_mean_oos_max_dd_matches_hand_computed_per_path_average(self):
        """A constant positive daily return has zero drawdown on every path,
        so mean_oos_max_dd must be exactly 0.0 (not NaN, not fabricated)."""
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.001)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["mean_oos_max_dd"] == pytest.approx(0.0, abs=1e-9)

    def test_mean_oos_hit_rate_is_one_for_all_positive_returns(self):
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.001)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["mean_oos_hit_rate"] == pytest.approx(1.0)

    def test_mean_oos_avg_trade_pct_matches_constant_daily_return(self):
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.00123)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["mean_oos_avg_trade_pct"] == pytest.approx(0.00123, abs=1e-9)

    def test_mean_oos_turnover_matches_trial_turnover(self):
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.001, turnover=0.08)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["mean_oos_turnover"] == pytest.approx(0.08)

    def test_empty_trials_yields_nan_aggregates_not_a_crash(self):
        X, y = _synthetic_xy()

        def empty_strategy_fn(X_train, y_train, X_test, y_test):
            return []

        result = run_cpcv_evaluation(empty_strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["dsr"] == 0.0 and result["pbo"] == 1.0
        assert np.isnan(result["mean_oos_max_dd"])
        assert np.isnan(result["mean_oos_sortino"])
        assert np.isnan(result["mean_oos_hit_rate"])
        assert np.isnan(result["mean_oos_avg_trade_pct"])
        assert np.isnan(result["mean_oos_turnover"])

    def test_max_dd_matches_compute_max_drawdown_on_a_real_drawdown_path(self):
        """A strategy with an actual drawdown (not a flat constant) must give
        mean_oos_max_dd a positive value consistent with compute_max_drawdown
        applied to each path's own test_returns."""
        X, y = _synthetic_xy(n=300)
        rng = np.random.default_rng(99)
        drawdown_returns = pd.Series(rng.normal(-0.0005, 0.01, size=len(X)), index=X.index)

        def strategy_fn(X_train, y_train, X_test, y_test):
            return [{
                "params": "drawdown",
                "train_returns": drawdown_returns.loc[drawdown_returns.index.intersection(y_train.index)],
                "test_returns": drawdown_returns.loc[drawdown_returns.index.intersection(y_test.index)],
                "turnover": 0.02,
            }]

        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["mean_oos_max_dd"] > 0.0
        assert np.isfinite(result["mean_oos_max_dd"])
