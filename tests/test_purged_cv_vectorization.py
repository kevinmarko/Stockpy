"""
tests/test_purged_cv_vectorization.py — Parity and performance tests for vectorized CPCV.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.purged_cv import CombinatorialPurgedCV


def _reference_split(
    cv: CombinatorialPurgedCV,
    X: pd.DataFrame,
    y: pd.Series | None = None,
    t1: pd.Series | None = None,
):
    """Reference implementation of CPCV split using the original un-vectorized algorithm."""
    n_samples = len(X)
    is_multi = isinstance(X.index, pd.MultiIndex)

    if t1 is None:
        t1_times = pd.Series(X.index).shift(-1)
        if isinstance(X.index, pd.DatetimeIndex):
            t1_times.iloc[-1] = X.index[-1] + pd.Timedelta(days=1)
        elif pd.api.types.is_integer_dtype(X.index.dtype):
            t1_times.iloc[-1] = X.index[-1] + 1
        else:
            t1_times.iloc[-1] = X.index[-1]
        t1 = pd.Series(t1_times.values, index=X.index)

    indices = np.arange(n_samples)
    block_size = n_samples // cv.n_splits
    blocks = []
    for i in range(cv.n_splits):
        start = i * block_size
        end = (i + 1) * block_size if i < cv.n_splits - 1 else n_samples
        blocks.append(indices[start:end])

    from itertools import combinations

    combos = list(combinations(range(cv.n_splits), cv.n_test_splits))
    embargo_size = int(n_samples * cv.embargo_pct)

    results = []
    for combo in combos:
        test_idx = np.concatenate([blocks[b] for b in combo])
        test_idx = np.sort(test_idx)

        train_idx_list = [blocks[b] for b in range(cv.n_splits) if b not in combo]
        train_idx = np.concatenate(train_idx_list) if train_idx_list else np.array([], dtype=int)
        purged_train_idx = set(train_idx)

        for b in combo:
            block_indices = blocks[b]
            test_start_time = X.index[block_indices[0]]
            test_end_time = X.index[block_indices[-1]]
            if is_multi:
                test_start_time, test_end_time = test_start_time[0], test_end_time[0]

            test_t1 = t1.iloc[block_indices]
            max_test_t1 = test_t1.max()

            for tr_idx in list(purged_train_idx):
                tr_time = X.index[tr_idx]
                if is_multi:
                    tr_time = tr_time[0]
                tr_t1 = t1.iloc[tr_idx]

                starts_within = (tr_time >= test_start_time) and (tr_time <= test_end_time)
                overlaps_start = (tr_t1 >= test_start_time) and (tr_time <= test_start_time)
                overlaps_end = (tr_time >= test_start_time) and (tr_time <= max_test_t1)

                if starts_within or overlaps_start or overlaps_end:
                    purged_train_idx.discard(tr_idx)
                    continue

                test_end_idx = block_indices[-1]
                if tr_idx > test_end_idx and tr_idx <= test_end_idx + embargo_size:
                    purged_train_idx.discard(tr_idx)

        results.append((np.sort(list(purged_train_idx)), test_idx, combo))
    return results


class TestPurgedCVVectorizationParity:
    """Verifies that the vectorized CombinatorialPurgedCV matches the reference output byte-for-byte."""

    def test_datetime_index_parity(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=100)
        X = pd.DataFrame({"feature": np.arange(100, dtype=float)}, index=idx)
        y = pd.Series(np.random.normal(0, 1, 100), index=idx)

        cv = CombinatorialPurgedCV(n_splits=6, n_test_splits=2, embargo_pct=0.02)
        ref_splits = _reference_split(cv, X, y)
        vec_splits = list(cv.split(X, y))

        assert len(vec_splits) == len(ref_splits)
        for (v_train, v_test, v_combo), (r_train, r_test, r_combo) in zip(vec_splits, ref_splits):
            assert v_combo == r_combo
            np.testing.assert_array_equal(v_test, r_test)
            np.testing.assert_array_equal(v_train, r_train)

    def test_multiindex_parity(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=20)
        tickers = ["AAPL", "MSFT", "GOOG"]
        tuples = [(d, t) for d in dates for t in tickers]
        midx = pd.MultiIndex.from_tuples(tuples, names=["Date", "Ticker"])
        X = pd.DataFrame({"feature": np.arange(len(tuples), dtype=float)}, index=midx)
        y = pd.Series(np.random.normal(0, 1, len(tuples)), index=midx)
        t1 = pd.Series(midx.get_level_values("Date") + pd.Timedelta(days=5), index=midx)

        cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2, embargo_pct=0.01)
        ref_splits = _reference_split(cv, X, y, t1=t1)
        vec_splits = list(cv.split(X, y, t1=t1))

        assert len(vec_splits) == len(ref_splits)
        for (v_train, v_test, v_combo), (r_train, r_test, r_combo) in zip(vec_splits, ref_splits):
            assert v_combo == r_combo
            np.testing.assert_array_equal(v_test, r_test)
            np.testing.assert_array_equal(v_train, r_train)

    def test_integer_index_parity(self) -> None:
        idx = pd.Index(np.arange(50, dtype=int))
        X = pd.DataFrame({"feature": np.arange(50, dtype=float)}, index=idx)
        y = pd.Series(np.random.normal(0, 1, 50), index=idx)

        cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2, embargo_pct=0.02)
        ref_splits = _reference_split(cv, X, y)
        vec_splits = list(cv.split(X, y))

        assert len(vec_splits) == len(ref_splits)
        for (v_train, v_test, v_combo), (r_train, r_test, r_combo) in zip(vec_splits, ref_splits):
            assert v_combo == r_combo
            np.testing.assert_array_equal(v_test, r_test)
            np.testing.assert_array_equal(v_train, r_train)
