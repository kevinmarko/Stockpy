"""
InvestYo Quant Platform - LightGBM Purged-CV Integration Tests
===============================================================
Verifies that CombinatorialPurgedCV from validation/purged_cv.py is actually
called during LGBMCrossSectionalRanker.train(), and that the mock-split contract
holds (the mock is called, training proceeds, no crash).
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from ml.lgbm_ranker import LGBMCrossSectionalRanker
from ml.feature_engineering import FEATURE_COLUMNS


def _make_flat_panel(n: int = 60, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-01", periods=n, freq="B")
    X = pd.DataFrame(rng.normal(0, 1, (n, len(FEATURE_COLUMNS))), index=dates, columns=FEATURE_COLUMNS)
    y = pd.Series(rng.uniform(0, 1, n), index=dates)
    return X, y


def test_purged_cv_split_is_called_during_training():
    """The CV splitter's split() must be called exactly once per train() call."""
    X, y = _make_flat_panel(60)

    split_call_count = []

    def mock_split_fn(X_arg, y_arg=None, t1=None):
        split_call_count.append(1)
        # Yield 2 simple folds so training can proceed
        n = len(X_arg)
        half = n // 2
        yield np.arange(half), np.arange(half, n), (0,)
        yield np.arange(half, n), np.arange(half), (1,)

    with patch("validation.purged_cv.CombinatorialPurgedCV.split", side_effect=mock_split_fn):
        ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
        ranker.train(X, y)

    assert len(split_call_count) == 1, (
        f"Expected exactly 1 split() call during train(), got {len(split_call_count)}"
    )


def test_train_produces_valid_scores_after_purged_cv():
    """End-to-end: train with real purged-CV and score today's cross-section."""
    X, y = _make_flat_panel(80, seed=13)
    ranker = LGBMCrossSectionalRanker(purged_kfold_splits=4)
    ranker.train(X, y)

    X_today = X.iloc[[-1]]  # last row as today's cross-section
    scores = ranker.predict_score(X_today)

    assert len(scores) == 1
    assert scores.between(0, 1).all()


def test_train_with_empty_input_does_not_crash():
    """Empty X/y must not raise — training is skipped gracefully."""
    ranker = LGBMCrossSectionalRanker()
    ranker.train(pd.DataFrame(columns=FEATURE_COLUMNS), pd.Series(dtype=float))
    # model should remain untrained (neutral scores)
    X_today = pd.DataFrame([[0.0] * len(FEATURE_COLUMNS)],
                            columns=FEATURE_COLUMNS, index=["T0"])
    scores = ranker.predict_score(X_today)
    assert scores.iloc[0] == pytest.approx(0.5)
