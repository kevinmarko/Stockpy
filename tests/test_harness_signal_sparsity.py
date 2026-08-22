"""Regression tests for StrategyValidationHarness.run()'s
``signal_sparsity_note`` -- the self-diagnosing explanation for a NaN
Sharpe/PBO/DSR that stems from the adapter's underlying strategy essentially
never actually trading over the backtest window, rather than a computation
bug.

Real incident this documents: `python -m scripts.refresh_validations
--strategies put_credit_spread,call_credit_spread` reported deployable=false,
pbo=NaN, dsr=NaN, sharpe=null, max_drawdown=0.24 with no explanation -- the
strategy's five-condition gate (true_ivr>50 AND VRP>threshold AND VIX<30 AND
not CREDIT EVENT AND directional trend_bias) essentially never matched over a
20-year SPY backtest (measured directly against real cached data: only 1 of
127 monthly cycles over 2015-2026 produced ANY of {Put Credit Spread, Call
Credit Spread, Iron Condor}, and the VRP-proxy/true_ivr-proxy correlation is
-0.216 -- a structural anti-correlation, not a data gap). See
docs/VALIDATION_STRATEGY_FIX_LOG.md's entry for this incident for the full
root-cause writeup and validation/metrics.py::describe_signal_sparsity for
the underlying diagnostic. Mirrors tests/test_harness_calmar_degenerate_guard.py's
offline-harness fixture pattern.
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


def _all_zero_strategy_fn(idx):
    """Mirrors the real STRATEGY_REGISTRY precomputed-adapter shape (see
    scripts/refresh_validations.py::_make_strategy_fn): a single trial whose
    return series never actually traded (every day 0.0-fill), exactly like
    put_credit_spread's real raw returns."""
    zeros = pd.Series(0.0, index=idx)

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "AllZero",
            "train_returns": zeros.reindex(y_train.index).fillna(0.0),
            "test_returns": zeros.reindex(y_test.index).fillna(0.0),
            "turnover": 0.05,
        }]
    return strategy_fn


def _dense_real_strategy_fn(idx, seed=42):
    rng = np.random.default_rng(seed)
    rets = pd.Series(rng.normal(0.0004, 0.006, size=len(idx)), index=idx)

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "Real",
            "train_returns": rets.reindex(y_train.index).dropna(),
            "test_returns": rets.reindex(y_test.index).dropna(),
            "turnover": 0.03,
        }]
    return strategy_fn


class TestSignalSparsityNote:
    def test_all_zero_returns_produce_populated_note(self, tmp_path):
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, _all_zero_strategy_fn(X.index))

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert report.signal_sparsity_note is not None
        assert "insufficient trading signal" in report.signal_sparsity_note
        assert "0/" in report.signal_sparsity_note
        # The NaN condition this note explains actually held. (Not asserting
        # on report.dsr/report.pbo here -- deflated_sharpe_ratio's n_trials<=1
        # shortcut is gated by settings.VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED,
        # an ambient operator setting this test must not depend on to stay
        # portable across environments; sharpe_ratio's degenerate-std guard
        # has no such dependency.)
        assert np.isnan(report.sharpe)

    def test_all_zero_returns_note_is_in_summary_dict(self, tmp_path):
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, _all_zero_strategy_fn(X.index))
        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        summary = report.to_summary_dict()
        assert summary["signal_sparsity_note"] is not None
        assert "insufficient trading signal" in summary["signal_sparsity_note"]

    def test_dense_real_returns_produce_no_note(self, tmp_path):
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, _dense_real_strategy_fn(X.index))

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert report.signal_sparsity_note is None
        assert report.to_summary_dict()["signal_sparsity_note"] is None

    def test_no_trials_produces_no_note_not_a_crash(self, tmp_path):
        """strategy_fn returning [] (no trials at all) -- the pre-existing
        `else` branch in run()'s step 5 -- must not crash computing the note
        (raw_test_returns is None in that branch)."""
        X, y = _synthetic_xy()
        harness = _make_harness(tmp_path, lambda *a, **k: [])

        report = harness.run(start_date="2015-01-01", end_date="2016-03-01", X=X, y=y, strategy_name="s")

        assert report.signal_sparsity_note is None
