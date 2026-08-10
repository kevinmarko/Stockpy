"""
InvestYo Quant Platform - LGBM Ranker Native MultiIndex CPCV Tests
====================================================================
Covers the ``use_native_multiindex_cv`` kwarg added to
``LGBMCrossSectionalRanker.train()`` (prerequisite for surfacing
``lgbm_ranker`` as a Pilot in ``scripts/refresh_validations.py`` --
see ``docs/VALIDATION_STRATEGY_FIX_LOG.md``):

* the native path REQUIRES an explicit ``t1`` for a MultiIndex X and raises
  ``ValueError`` when it's missing;
* the native path hands ``CombinatorialPurgedCV.split()`` the MultiIndex
  panel directly (no flatten);
* the flatten (default/legacy) path is UNCHANGED -- a MultiIndex X with no
  t1 still trains without raising when the kwarg is left unset (regression
  guard for ``tests/test_lgbm_no_leakage.py``'s existing usage pattern);
* the settings-flag fallback (``LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED``)
  resolves the kwarg when the caller leaves it as ``None``.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from ml.feature_engineering import FEATURE_COLUMNS
from ml.lgbm_ranker import LGBMCrossSectionalRanker


def _make_multiindex_panel(
    n_dates: int = 40, n_tickers: int = 10, seed: int = 0
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Synthetic (date, ticker) panel with a real forward-window t1."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-01", periods=n_dates, freq="B")
    tickers = [f"T{i}" for i in range(n_tickers)]

    rows, y_rows, t1_rows = [], [], []
    for i, dt in enumerate(dates):
        idx = pd.MultiIndex.from_tuples([(dt, t) for t in tickers], names=["date", "ticker"])
        feat = pd.DataFrame(
            rng.normal(0, 1, size=(n_tickers, len(FEATURE_COLUMNS))),
            index=idx, columns=FEATURE_COLUMNS,
        )
        rows.append(feat)
        y_rows.append(pd.Series(rng.uniform(0, 1, n_tickers), index=idx))
        # Real ~21-trading-day forward-window end time, not the next row.
        end_dt = dates[min(i + 21, n_dates - 1)]
        t1_rows.append(pd.Series([end_dt] * n_tickers, index=idx))

    X = pd.concat(rows)
    y = pd.concat(y_rows)
    t1 = pd.concat(t1_rows)
    return X, y, t1


def _make_flat_panel(n: int = 60, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-01", periods=n, freq="B")
    X = pd.DataFrame(rng.normal(0, 1, (n, len(FEATURE_COLUMNS))), index=dates, columns=FEATURE_COLUMNS)
    y = pd.Series(rng.uniform(0, 1, n), index=dates)
    return X, y


class TestNativePathRequiresT1:
    def test_native_multiindex_without_t1_raises(self):
        X, y, _t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        with pytest.raises(ValueError, match="t1 is required"):
            ranker.train(X, y, use_native_multiindex_cv=True)

    def test_native_multiindex_with_t1_does_not_raise(self):
        X, y, t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker.train(X, y, t1=t1, use_native_multiindex_cv=True)
        assert ranker._model is not None


class TestFlattenPathUnchanged:
    def test_default_multiindex_without_t1_still_trains(self):
        """Regression guard: tests/test_lgbm_no_leakage.py calls
        model.train(X, y) with a MultiIndex X and no t1, with the kwarg
        never set anywhere -- this must keep working exactly as before."""
        X, y, _t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker.train(X, y)  # no t1, no use_native_multiindex_cv -> flatten path
        assert ranker._model is not None

    def test_explicit_flatten_false_with_multiindex_and_no_t1(self):
        X, y, _t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker.train(X, y, use_native_multiindex_cv=False)
        assert ranker._model is not None

    def test_flat_index_unaffected_by_native_kwarg(self):
        """use_native_multiindex_cv is a no-op when X isn't a MultiIndex."""
        X, y = _make_flat_panel(60)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker.train(X, y, use_native_multiindex_cv=True)  # no t1, flat X
        assert ranker._model is not None


class TestSplitReceivesExpectedIndexShape:
    def test_native_path_passes_multiindex_to_split(self):
        X, y, t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        seen = {}

        def mock_split_fn(X_arg, y_arg=None, t1_arg=None):
            seen["is_multi"] = isinstance(X_arg.index, pd.MultiIndex)
            n = len(X_arg)
            half = n // 2
            yield np.arange(half), np.arange(half, n), (0,)

        with patch("validation.purged_cv.CombinatorialPurgedCV.split", side_effect=mock_split_fn):
            ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
            ranker.train(X, y, t1=t1, use_native_multiindex_cv=True)

        assert seen["is_multi"] is True

    def test_flatten_path_passes_flat_index_to_split(self):
        X, y, t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        seen = {}

        def mock_split_fn(X_arg, y_arg=None, t1_arg=None):
            seen["is_multi"] = isinstance(X_arg.index, pd.MultiIndex)
            n = len(X_arg)
            half = n // 2
            yield np.arange(half), np.arange(half, n), (0,)

        with patch("validation.purged_cv.CombinatorialPurgedCV.split", side_effect=mock_split_fn):
            ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
            ranker.train(X, y, t1=t1, use_native_multiindex_cv=False)

        assert seen["is_multi"] is False


class TestSettingsFallback:
    def test_settings_flag_enables_native_path_when_kwarg_unset(self):
        X, y, _t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        with (
            patch("settings.settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", True),
            pytest.raises(ValueError, match="t1 is required"),
        ):
            ranker.train(X, y)  # kwarg unset -> resolved from settings -> native -> no t1 -> raises

    def test_settings_flag_off_by_default(self):
        """Default settings value must be False -- flag-off byte-identical
        behavior for every existing caller."""
        from settings import settings as real_settings
        assert real_settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED is False

    def test_explicit_kwarg_overrides_settings_flag(self):
        X, y, _t1 = _make_multiindex_panel(n_dates=30, n_tickers=8)
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        with patch("settings.settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", True):
            # Explicit False always wins over the settings flag.
            ranker.train(X, y, use_native_multiindex_cv=False)
        assert ranker._model is not None
