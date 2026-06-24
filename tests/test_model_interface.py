"""
tests/test_model_interface.py
================================
Verifies that all concrete ML models conform to the ml.models.base.Model ABC
(Prompt 4.3 — qlib-style architecture audit).

Tests
-----
- LGBMCrossSectionalRanker conforms to Model ABC
- MetaLabeler conforms to Model ABC
- fit/predict/save/load round-trips for both models
- StrategySpec wraps a model correctly
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.models.base import Model
from ml.lgbm_ranker import LGBMCrossSectionalRanker
from ml.meta_labeling import MetaLabeler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_X(n: int = 60, n_features: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(
        rng.normal(0, 1, (n, n_features)),
        columns=[f"f{i}" for i in range(n_features)],
        index=idx,
    )


def _minimal_y(n: int = 60, binary: bool = False) -> pd.Series:
    rng = np.random.default_rng(1)
    if binary:
        return pd.Series(rng.integers(0, 2, n), index=pd.date_range("2020-01-01", periods=n, freq="B"))
    return pd.Series(rng.uniform(0, 1, n), index=pd.date_range("2020-01-01", periods=n, freq="B"))


# ---------------------------------------------------------------------------
# ABC conformance
# ---------------------------------------------------------------------------

def test_lgbm_ranker_is_model():
    """LGBMCrossSectionalRanker must be an instance of Model."""
    ranker = LGBMCrossSectionalRanker()
    assert isinstance(ranker, Model), "LGBMCrossSectionalRanker is not a subclass of Model"


def test_meta_labeler_is_model():
    """MetaLabeler must be an instance of Model."""
    labeler = MetaLabeler(signal_id="test")
    assert isinstance(labeler, Model), "MetaLabeler is not a subclass of Model"


# ---------------------------------------------------------------------------
# LGBMCrossSectionalRanker — fit/predict/save/load
# ---------------------------------------------------------------------------

def test_lgbm_ranker_fit_predict_saves():
    """fit() → predict() → save() → load() round-trip for LGBMCrossSectionalRanker."""
    ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
    X = _minimal_X(60, 3)
    y = _minimal_y(60)

    # fit (Model ABC)
    returned = ranker.fit(X, y)
    assert returned is ranker, "fit() must return self"

    # predict (Model ABC)
    preds = ranker.predict(X)
    assert isinstance(preds, np.ndarray), "predict() must return ndarray"
    assert len(preds) == len(X), "predict() length mismatch"

    # save / load
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "lgbm_test.pkl"
        ranker.save(path)
        loaded = LGBMCrossSectionalRanker.load(path)
        assert isinstance(loaded, LGBMCrossSectionalRanker)
        preds_loaded = loaded.predict(X)
        np.testing.assert_allclose(preds, preds_loaded, rtol=1e-6)


# ---------------------------------------------------------------------------
# MetaLabeler — fit/predict/save/load
# ---------------------------------------------------------------------------

def test_meta_labeler_fit_predict_saves():
    """fit() → predict() → save() → load() round-trip for MetaLabeler."""
    labeler = MetaLabeler(signal_id="round_trip_test")
    X = _minimal_X(80, 3)
    y = _minimal_y(80, binary=True)

    returned = labeler.fit(X, y)
    assert returned is labeler, "fit() must return self"

    preds = labeler.predict(X)
    assert isinstance(preds, np.ndarray), "predict() must return ndarray"
    assert set(preds).issubset({0, 1}), "predict() must return binary labels"

    probas = labeler.predict_proba(X)
    assert ((probas >= 0.0) & (probas <= 1.0)).all()

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "meta_test.pkl"
        labeler.save(path)
        loaded = MetaLabeler.load(path)
        assert isinstance(loaded, MetaLabeler)
        np.testing.assert_allclose(probas, loaded.predict_proba(X), rtol=1e-6)


# ---------------------------------------------------------------------------
# StrategySpec
# ---------------------------------------------------------------------------

def test_strategy_spec_wraps_model():
    """StrategySpec.score() delegates to Model.predict() and returns a pd.Series."""
    from ml.strategies import StrategySpec

    ranker = LGBMCrossSectionalRanker(purged_kfold_splits=3)
    X = _minimal_X(60, 3)
    y = _minimal_y(60)
    ranker.fit(X, y)

    spec = StrategySpec(
        model=ranker,
        signal_id="lgbm_ranker",
        description="Test spec",
    )
    scores = spec.score(X)
    assert isinstance(scores, pd.Series)
    assert len(scores) == len(X)
    assert not spec.is_meta_labeler


def test_strategy_spec_meta_labeler_flag():
    """StrategySpec.is_meta_labeler is True when meta_labeler_signal_ids is set."""
    from ml.strategies import StrategySpec

    labeler = MetaLabeler(signal_id="ts_momentum")
    spec = StrategySpec(
        model=labeler,
        signal_id="meta_ts_momentum",
        meta_labeler_signal_ids=["timeseries_momentum"],
    )
    assert spec.is_meta_labeler


# ---------------------------------------------------------------------------
# Model ABC — cannot instantiate directly
# ---------------------------------------------------------------------------

def test_model_abc_cannot_instantiate():
    """Model ABC must not be directly instantiatable."""
    with pytest.raises(TypeError):
        Model()  # type: ignore[abstract]
