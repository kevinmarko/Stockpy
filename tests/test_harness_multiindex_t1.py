"""Unit tests for StrategyValidationHarness.run()'s new ``t1`` parameter --
the plumbing that lets a native (Date, Ticker) pd.MultiIndex adapter (e.g.
scripts.refresh_validations._build_sector_quality_rank_adapter) reach
CombinatorialPurgedCV's native MultiIndex support (PR #648) through the same
harness every flat-DatetimeIndex adapter already uses.

Fully offline: synthetic X/y, stubbed universe/SPY -- same pattern as
tests/test_harness_oos_gate.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
from validation.purged_cv import CombinatorialPurgedCV
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


def _flat_xy(n=300, seed=7):
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"feat": np.arange(n, dtype=float)}, index=idx)
    y = pd.Series(rng.normal(0.0003, 0.007, size=n), index=idx)
    return X, y


def _flat_strategy_fn(idx, seed=99):
    rng = np.random.default_rng(seed)
    rets = pd.Series(rng.normal(0.0005, 0.01, size=len(idx)), index=idx)

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [{
            "params": "flat",
            "train_returns": rets.reindex(y_train.index).dropna(),
            "test_returns": rets.reindex(y_test.index).dropna(),
            "turnover": 0.02,
        }]
    return strategy_fn


def _multiindex_xy(n_dates=120, tickers=("AAA", "BBB", "CCC", "DDD", "EEE"), seed=11):
    dates = pd.date_range("2015-01-01", periods=n_dates, freq="B")
    midx = pd.MultiIndex.from_product([dates, tickers], names=["Date", "Ticker"])
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"feat": rng.normal(size=len(midx))}, index=midx)
    y = pd.Series(rng.normal(0.0003, 0.01, size=len(midx)), index=midx)
    return X, y


def _multiindex_strategy_fn(book_returns):
    """Mirrors _build_sector_quality_rank_adapter's real strategy_fn shape:
    given a MultiIndex train/test subset, slice a precomputed flat
    Date-indexed book-return series to exactly those dates."""

    def strategy_fn(X_train, y_train, X_test, y_test):
        train_dates = X_train.index.get_level_values("Date").unique().sort_values()
        test_dates = X_test.index.get_level_values("Date").unique().sort_values()
        return [{
            "params": "sneqr_like",
            "train_returns": book_returns.reindex(train_dates).fillna(0.0),
            "test_returns": book_returns.reindex(test_dates).fillna(0.0),
            "turnover": 0.01,
        }]
    return strategy_fn


class TestT1DefaultPreservesFlatIndexBehavior:
    def test_flat_index_run_unaffected_by_new_parameter(self, tmp_path):
        """Omitting t1 (the default) on a flat-DatetimeIndex X/y must
        reproduce the pre-existing behavior exactly -- CombinatorialPurgedCV
        synthesizes its own default t1 internally, same as before this
        parameter existed."""
        X, y = _flat_xy()
        harness = StrategyValidationHarness(
            strategy_fn=_flat_strategy_fn(X.index),
            universe_fn=lambda _d: ["SYN"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        report = harness.run(
            start_date="2015-01-01", end_date="2016-06-01",
            X=X, y=y, strategy_name="flat_synthetic",
        )
        assert np.isfinite(report.sharpe)
        assert 0.0 <= report.pbo <= 1.0


class TestT1MultiIndexPlumbing:
    def test_multiindex_x_without_t1_raises(self, tmp_path):
        """A MultiIndex X with no explicit t1 must surface
        CombinatorialPurgedCV.split()'s own ValueError -- the harness must
        NOT silently swallow or paper over this constraint."""
        X, y = _multiindex_xy()
        book_returns = pd.Series(
            0.0, index=X.index.get_level_values("Date").unique()
        )
        harness = StrategyValidationHarness(
            strategy_fn=_multiindex_strategy_fn(book_returns),
            universe_fn=lambda _d: ["AAA", "BBB", "CCC", "DDD", "EEE"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="t1"):
            harness.run(
                start_date="2015-01-01", end_date="2015-07-01",
                X=X, y=y, strategy_name="multiindex_no_t1",
                # t1 omitted (None) on purpose.
            )

    def test_multiindex_x_with_explicit_t1_runs_end_to_end(self, tmp_path):
        """The actual new capability: a real (Date, Ticker) MultiIndex X/y
        plus an explicit t1 must run the full harness (walk-forward splits,
        CPCV, full-sample fit, deployability gate) to completion and produce
        a well-formed report -- never asserting deployable is True/False,
        only that every number is finite/sane (CONSTRAINT #4 honesty)."""
        X, y = _multiindex_xy(n_dates=150)
        rng = np.random.default_rng(3)
        dates = X.index.get_level_values("Date").unique()
        book_returns = pd.Series(rng.normal(0.0004, 0.008, size=len(dates)), index=dates)

        t1 = pd.Series(
            X.index.get_level_values("Date") + pd.Timedelta(days=21),
            index=X.index,
        )

        harness = StrategyValidationHarness(
            strategy_fn=_multiindex_strategy_fn(book_returns),
            universe_fn=lambda _d: ["AAA", "BBB", "CCC", "DDD", "EEE"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        report = harness.run(
            start_date=str(dates.min().date()), end_date=str(dates.max().date()),
            X=X, y=y, strategy_name="multiindex_synthetic", t1=t1,
        )

        assert isinstance(report.deployable, bool)
        assert np.isfinite(report.sharpe)
        assert np.isfinite(report.max_dd)
        assert 0.0 <= report.pbo <= 1.0
        assert np.isfinite(report.dsr)

    def test_multiindex_y_benchmark_reindex_never_crashes(self, tmp_path):
        """y being a MultiIndex Series (required so y.iloc[idx] stays
        row-aligned with a MultiIndex X inside run_cpcv_evaluation) must
        degrade the benchmark overlay to empty rather than crash
        run() -- reindexing a MultiIndex Series onto full_returns' flat
        DatetimeIndex raises ValueError, verified directly against real
        pandas behavior; this guards the try/except added around that call."""
        X, y = _multiindex_xy(n_dates=120)
        dates = X.index.get_level_values("Date").unique()
        book_returns = pd.Series(0.0002, index=dates)
        t1 = pd.Series(
            X.index.get_level_values("Date") + pd.Timedelta(days=21),
            index=X.index,
        )
        harness = StrategyValidationHarness(
            strategy_fn=_multiindex_strategy_fn(book_returns),
            universe_fn=lambda _d: ["AAA", "BBB", "CCC", "DDD", "EEE"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=5,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        # Must not raise.
        report = harness.run(
            start_date=str(dates.min().date()), end_date=str(dates.max().date()),
            X=X, y=y, strategy_name="multiindex_benchmark_guard", t1=t1,
        )
        assert report.benchmark_curve == []
