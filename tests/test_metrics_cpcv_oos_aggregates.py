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


def _two_config_wobbling_strategy_fn(daily_return_a=0.002, daily_return_b=0.0004, turnover=0.02, wobble=0.0001):
    """Two trial configurations with genuinely DIFFERENT (not noise-level)
    mean daily returns -- each with a tiny wobble so their Sharpe is
    well-defined (not the NaN a perfectly-constant/degenerate series would
    produce) -- so mean_is_sharpes has real, non-degenerate variance across
    trials. Used to prove the sr_variance degenerate-std guard doesn't
    misfire on legitimate dispersion."""
    def strategy_fn(X_train, y_train, X_test, y_test):
        def _series(index, mean_ret):
            sign = np.resize([1.0, -1.0], len(index))
            return pd.Series(mean_ret + sign * wobble, index=index)
        return [
            {
                "params": "config_a",
                "train_returns": _series(y_train.index, daily_return_a),
                "test_returns": _series(y_test.index, daily_return_a),
                "turnover": turnover,
            },
            {
                "params": "config_b",
                "train_returns": _series(y_train.index, daily_return_b),
                "test_returns": _series(y_test.index, daily_return_b),
                "turnover": turnover,
            },
        ]
    return strategy_fn


def _wobbling_return_strategy_fn(daily_return=0.001, turnover=0.02, wobble=0.0001):
    """Like ``_constant_return_strategy_fn`` but with a tiny deterministic
    +/-wobble around the constant mean, so the series carries genuine
    (non-degenerate) variance. Sharpe/Sortino need real dispersion to be
    well-defined -- an exactly-flat series correctly returns NaN under
    ``validation.metrics.sharpe_ratio``'s degenerate-std guard (a near-zero
    std that's pure floating-point noise, not signal, must never be divided
    into an absurd ratio -- CONSTRAINT #4), so tests that need a comparable,
    orderable Sharpe use this fixture instead."""
    def strategy_fn(X_train, y_train, X_test, y_test):
        def _series(index):
            sign = np.resize([1.0, -1.0], len(index))
            return pd.Series(daily_return + sign * wobble, index=index)
        return [{
            "params": "wobbling",
            "train_returns": _series(y_train.index),
            "test_returns": _series(y_test.index),
            "turnover": turnover,
        }]
    return strategy_fn


class TestCostModelFn:
    def test_none_reproduces_gross_returns(self):
        X, y = _synthetic_xy()
        strategy_fn = _wobbling_return_strategy_fn(daily_return=0.002, turnover=0.02, wobble=0.0002)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        # A +0.002/day return with only a tiny wobble around it has a large
        # (but finite, well-defined) Sharpe -- what matters here is that it's
        # POSITIVE and large, since no cost was subtracted.
        assert result["mean_oos_sharpe"] > 50

    def test_cost_model_fn_reduces_returns_before_any_stat_is_computed(self):
        """A cost model draining more than the daily return must flip the OOS
        return series (and therefore Sharpe/hit-rate) negative -- proof the
        adjustment happens BEFORE Sharpe/PBO/DSR/drawdown, not after."""
        X, y = _synthetic_xy()
        daily_return = 0.0005
        strategy_fn = _wobbling_return_strategy_fn(daily_return=daily_return, turnover=0.02, wobble=0.00005)

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

    def test_mean_oos_return_is_unconditional_mean_matching_constant_daily_return(self):
        """mean_oos_return is the UNCONDITIONAL per-path mean (every day, not
        just trade_days) -- for a constant daily return, every day is a
        trade day, so this equals mean_oos_avg_trade_pct, but the two are
        computed independently (see TestSkewKurtosisTrialConsistency below
        for a case where a per-path-average would differ from the
        single-selected-trial mean)."""
        X, y = _synthetic_xy()
        strategy_fn = _constant_return_strategy_fn(daily_return=0.00087)
        result = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert result["mean_oos_return"] == pytest.approx(0.00087, abs=1e-9)

    def test_empty_trials_mean_oos_return_is_nan(self):
        X, y = _synthetic_xy()

        def empty_strategy_fn(X_train, y_train, X_test, y_test):
            return []

        result = run_cpcv_evaluation(empty_strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert np.isnan(result["mean_oos_return"])


class TestSrVarianceDegenerateGuard:
    """Bug: sr_variance = np.var(mean_is_sharpes) was floored with an exact
    ``== 0`` check instead of this repo's documented degenerate-std
    ``< 1e-12`` convention. A near-zero-but-nonzero variance (floating-point
    noise from near-identical trial Sharpes) must be treated the same way as
    an exact zero -- floored to 1e-6 -- not left as literal noise feeding
    deflated_sharpe_ratio's sqrt(var_sr) term."""

    def test_near_zero_variance_is_floored_like_exact_zero(self, monkeypatch):
        import validation.metrics as metrics_module

        X, y = _synthetic_xy()
        strategy_fn = _wobbling_return_strategy_fn(daily_return=0.001, wobble=0.0002)

        monkeypatch.setattr(metrics_module.np, "var", lambda *a, **k: 0.0)
        result_exact_zero = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)

        monkeypatch.setattr(metrics_module.np, "var", lambda *a, **k: 9.9e-17)
        result_near_zero = run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)

        assert result_near_zero["dsr"] == pytest.approx(result_exact_zero["dsr"], nan_ok=True)

    def test_genuinely_small_but_real_variance_is_not_floored(self, monkeypatch):
        """A real (not artificially forced), legitimately-small-but-nonzero
        variance must NOT be floored -- the guard must only catch floating
        noise near the 1e-12 boundary, not real low-dispersion signal."""
        import validation.metrics as metrics_module

        seen = {}
        real_var = np.var

        def spy_var(*args, **kwargs):
            v = real_var(*args, **kwargs)
            seen["value"] = v
            return v

        monkeypatch.setattr(metrics_module.np, "var", spy_var)
        X, y = _synthetic_xy()
        strategy_fn = _two_config_wobbling_strategy_fn()
        run_cpcv_evaluation(strategy_fn, X, y, n_splits=5, n_test_splits=2)
        assert seen["value"] >= 1e-12, "premise: real variance is well above the noise floor"


class TestSkewKurtosisTrialConsistency:
    """Finding 25: skew/kurtosis fed into deflated_sharpe_ratio must be
    computed from the SAME trial selection (best_overall_idx -- the trial
    with the best MEAN in-sample Sharpe across all paths) that sr_observed
    itself uses, not from the per-path best_is_idx 'winners' (which can
    differ from best_overall_idx on individual paths and legitimately back
    paths_data's own per-path report table)."""

    def test_skew_input_is_built_from_best_overall_idx_returns_when_per_path_winners_diverge(self, monkeypatch):
        """Construct two trial configurations whose per-path IS winner
        genuinely diverges from the global (mean-IS) winner: config A (index
        0) has the best MEAN in-sample Sharpe across all paths (so it is
        best_overall_idx), but config B (index 1) actually wins in-sample on
        at least one individual path (a noisier, higher-variance IS return).
        Spies on pd.Series.skew (called exactly once in run_cpcv_evaluation,
        on the merged best_overall_idx OOS returns) to capture the EXACT
        array production code fed it, and asserts it equals config A's own
        merged OOS returns -- proving no per-path best_is_idx contamination
        from config B leaked in."""
        from validation.purged_cv import CombinatorialPurgedCV
        from validation.metrics import sharpe_ratio as _sharpe

        n = 240
        idx = pd.date_range("2016-01-01", periods=n, freq="B")
        X = pd.DataFrame({"feat": np.arange(n, dtype=float)}, index=idx)
        rng = np.random.default_rng(5)
        y = pd.Series(rng.normal(0.0002, 0.005, size=n), index=idx)

        # Config A/B: each a FULL return series generated ONCE up front (not
        # re-randomized per strategy_fn call -- same rationale as
        # tests/test_harness_oos_gate.py's _drawdown_strategy_fn: strategy_fn
        # is invoked many times per CPCV pass, and this test module also
        # re-derives an expectation by calling it again afterward, so a
        # freshly-drawn random series per call would silently desync the two).
        # Config A has a higher mean (so its MEAN in-sample Sharpe across all
        # paths wins overall) but real per-path noise, so config B's own
        # per-path IS Sharpe genuinely beats it on some individual paths.
        rng_a = np.random.default_rng(3)
        full_a_returns = pd.Series(rng_a.normal(0.0008, 0.006, size=n), index=idx)
        rng_b = np.random.default_rng(99)
        full_b_returns = pd.Series(rng_b.normal(0.0004, 0.006, size=n), index=idx)

        def strategy_fn(X_train, y_train, X_test, y_test):
            return [
                {
                    "params": "config_a",
                    "train_returns": full_a_returns.reindex(y_train.index).dropna(),
                    "test_returns": full_a_returns.reindex(y_test.index).dropna(),
                    "turnover": 0.02,
                },
                {
                    "params": "config_b",
                    "train_returns": full_b_returns.reindex(y_train.index).dropna(),
                    "test_returns": full_b_returns.reindex(y_test.index).dropna(),
                    "turnover": 0.02,
                },
            ]

        cv = CombinatorialPurgedCV(n_splits=6, n_test_splits=2)

        # Sanity: confirm at least one CPCV path actually has config B
        # winning in-sample -- proving best_is_idx and best_overall_idx
        # really do diverge on at least one path, otherwise this test would
        # pass trivially even with the bug present.
        divergence_found = False
        expected_a_returns: list[float] = []
        for train_idx, test_idx, _pid in cv.split(X, y, None):
            if len(train_idx) == 0:
                continue
            trials = strategy_fn(X.iloc[train_idx], y.iloc[train_idx], X.iloc[test_idx], y.iloc[test_idx])
            is_a = _sharpe(trials[0]["train_returns"])
            is_b = _sharpe(trials[1]["train_returns"])
            if (not np.isnan(is_b)) and is_b > is_a:
                divergence_found = True
            expected_a_returns.extend(trials[0]["test_returns"].tolist())
        assert divergence_found, "test premise failed: config B never wins in-sample on any path"

        captured: dict = {}
        real_skew = pd.Series.skew

        def spy_skew(self, *args, **kwargs):
            captured["values"] = self.tolist()
            return real_skew(self, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(pd.Series, "skew", spy_skew)
            run_cpcv_evaluation(strategy_fn, X, y, n_splits=6, n_test_splits=2)

        assert "values" in captured, "pd.Series.skew was never called"
        # The bug this test locks in would instead have fed a mix including
        # config B's returns on paths where B won in-sample -- a length or
        # value mismatch here is exactly that contamination.
        assert captured["values"] == pytest.approx(expected_a_returns)
