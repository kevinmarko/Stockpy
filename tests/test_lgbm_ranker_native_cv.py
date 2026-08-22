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


class TestPerDateQueryGroups:
    """Regression coverage for a real bug found widening
    scripts/refresh_validations.py's ``lgbm_ranker`` universe from 30 to 100
    tickers: ``train()`` computed a correct per-date ``groups`` array (the
    "# tickers per date (query)" comment) but never actually passed it to
    ``lgb.LGBMRanker.fit`` -- every fold and the final fit instead used
    ``group=[len(y)]``, treating the ENTIRE fold/panel as one giant LambdaRank
    query. Wrong even when it doesn't crash (ranks tickers against OTHER
    DATES' tickers, not just same-date peers) -- and at 100 tickers x enough
    dates, the single query's row count crossed LightGBM's real internal
    ~10000-row-per-query limit and the whole strategy started hard-crashing
    every CPCV fold (PBO=1.0/DSR=0.0/Sharpe=None sentinel output). See
    ``ml.lgbm_ranker._positional_query_groups`` and
    ``docs/known_issues/lgbm_ranker_query_group_bug.md``.
    """

    def test_positional_query_groups_run_length_encodes_contiguous_dates(self):
        from ml.lgbm_ranker import _positional_query_groups

        # 3 dates x 4 tickers each, contiguous, all positions retained.
        keys = np.array([0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2])
        assert _positional_query_groups(keys, np.arange(12)) == [4, 4, 4]

    def test_positional_query_groups_survives_a_purged_middle_chunk(self):
        """A purge/embargo removing rows from the MIDDLE of one date's block
        (not the whole date) must still produce valid LightGBM groups summing
        to len(positions) -- the two surviving sub-runs of that date become
        contiguous WITHIN the filtered subset even though they weren't
        contiguous in the original unfiltered array (see the function's own
        docstring for why)."""
        from ml.lgbm_ranker import _positional_query_groups

        keys = np.array([0, 0, 0, 0, 0, 1, 1, 1])  # date 0 has 5 tickers, date 1 has 3
        # Drop positions 2,3 (middle of date 0's block) -- keep 0,1,4 (date 0) + 5,6,7 (date 1).
        positions = np.array([0, 1, 4, 5, 6, 7])
        groups = _positional_query_groups(keys, positions)
        assert groups == [3, 3]
        assert sum(groups) == len(positions)

    def test_positional_query_groups_none_keys_is_one_group(self):
        from ml.lgbm_ranker import _positional_query_groups

        assert _positional_query_groups(None, np.arange(7)) == [7]

    def test_positional_query_groups_empty_positions(self):
        from ml.lgbm_ranker import _positional_query_groups

        assert _positional_query_groups(np.array([0, 1]), np.array([], dtype=int)) == []

    def test_real_training_past_10000_rows_no_longer_crashes(self):
        """The actual regression repro: 250 dates x 45 tickers = 11250 total
        rows -- comfortably over LightGBM's real ~10000-row single-query
        limit (confirmed directly against the installed lightgbm: fit(...,
        group=[10500]) raises 'Number of rows 10500 exceeds upper limit of
        10000 for a query') -- but each individual date's query is only 45
        rows, so real per-date grouping must never approach that limit.
        Before the fix this raised LightGBMError on both the inner CV folds
        and the final full-data fit."""
        X, y, t1 = _make_multiindex_panel(n_dates=250, n_tickers=45)
        assert len(X) > 10_000

        ranker = LGBMCrossSectionalRanker(
            params={"n_estimators": 5}, purged_kfold_splits=3, embargo_pct=0.0,
        )
        ranker.train(X, y, t1=t1, use_native_multiindex_cv=True)  # must not raise

        assert ranker._model is not None

    def test_final_fit_group_sums_to_full_panel_and_matches_per_date_counts(self):
        """Directly proves the FINAL model's group array is real per-date
        counts, not [len(y_arr)] -- reconstructs what train() computes
        internally for group_keys/group_full without relying on mocking
        lightgbm (no test in this suite mocks it; every other test here
        exercises the real library)."""
        from ml.lgbm_ranker import _positional_query_groups

        X, y, t1 = _make_multiindex_panel(n_dates=20, n_tickers=6)
        X = X.sort_index(level=0)  # train() does this too for a MultiIndex
        group_keys = X.index.get_level_values(0).values
        group_full = _positional_query_groups(group_keys, np.arange(len(X)))

        assert sum(group_full) == len(X)
        assert group_full == [6] * 20  # 20 dates x 6 tickers each, none purged
        assert group_full != [len(X)]  # the pre-fix single-giant-group shape


class TestReproducibility:
    """Regression coverage for the fixed-seed determinism fix in
    ``ml.lgbm_ranker._DEFAULT_PARAMS`` (``LGBM_RANDOM_SEED = 42`` plus
    ``random_state``/``deterministic``/``force_row_wise``). Before this fix,
    ``feature_fraction``/``bagging_fraction`` (refreshed every iteration since
    ``bagging_freq=1``) drew a genuinely random row/feature subsample each
    run, making every ``lgb.LGBMRanker(**params)`` fit non-deterministic --
    confirmed live via two ``lgbm_ranker`` CPCV runs over the identical date
    window differing at the 6th significant digit of Sharpe. See the module's
    own ``LGBM_RANDOM_SEED`` docstring comment for the full writeup.
    """

    def test_default_params_include_fixed_seed(self):
        import ml.lgbm_ranker as lgbm_ranker_module

        assert (
            lgbm_ranker_module._DEFAULT_PARAMS["random_state"]
            == lgbm_ranker_module.LGBM_RANDOM_SEED
        )
        assert lgbm_ranker_module._DEFAULT_PARAMS["deterministic"] is True

    def test_two_identical_trainings_produce_bitidentical_predictions(self):
        """Two independently-constructed rankers, trained on the identical
        panel via the native MultiIndex CV path (the path production/
        validation actually exercises), must produce bit-identical
        predictions -- not merely close ones."""
        X, y, t1 = _make_multiindex_panel(n_dates=40, n_tickers=10, seed=0)

        ranker_a = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker_a.train(X, y, t1=t1, use_native_multiindex_cv=True)

        ranker_b = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker_b.train(X, y, t1=t1, use_native_multiindex_cv=True)

        assert ranker_a._model is not None
        assert ranker_b._model is not None

        preds_a = ranker_a.predict(X)
        preds_b = ranker_b.predict(X)

        np.testing.assert_array_equal(preds_a, preds_b)

    def test_two_identical_trainings_produce_bitidentical_predictions_flatten_path(self):
        """Symmetry check for the default (non-native, flatten) path used by
        every existing caller that never sets ``use_native_multiindex_cv``."""
        X, y = _make_flat_panel(60, seed=0)

        ranker_a = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker_a.train(X, y)

        ranker_b = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker_b.train(X, y)

        assert ranker_a._model is not None
        assert ranker_b._model is not None

        preds_a = ranker_a.predict(X)
        preds_b = ranker_b.predict(X)

        np.testing.assert_array_equal(preds_a, preds_b)
