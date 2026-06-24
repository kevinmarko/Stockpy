"""
InvestYo Quant Platform - Abstract Model Interface (qlib-style, no qlib dep)
=============================================================================
Mirrors Microsoft qlib's three-layer pattern (Data → Model → Strategy) WITHOUT
taking qlib as a dependency. All ML models in this platform implement Model so
the strategy layer can consume them through a uniform interface.

Reference: https://qlib.readthedocs.io/en/latest/component/model.html

Concrete implementations:
  - ml.lgbm_ranker.LGBMCrossSectionalRanker  (cross-sectional LambdaRank)
  - ml.meta_labeling.MetaLabeler              (binary meta-label classifier)
"""

from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


class Model(ABC):
    """Uniform interface for all ML models in the InvestYo platform.

    Three obligatory methods mirror qlib's model contract:
      fit(X, y, t1)   — train (t1 = event end times for CPCV purging)
      predict(X)      — return point predictions as an ndarray
      save(path)      — persist to disk
      load(path)      — class-method that reconstructs from disk

    Concrete models may add domain-specific methods (e.g.,
    ``predict_score``, ``predict_proba_scalar``) alongside these.
    """

    @abstractmethod
    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        t1: Optional[pd.Series] = None,
    ) -> "Model":
        """Train the model.

        Parameters
        ----------
        X :
            Feature matrix. Index = date or (date, ticker) MultiIndex.
        y :
            Target vector, aligned with X.
        t1 :
            Event end times (for Purged-CV embargo logic). Optional.

        Returns
        -------
        self
        """

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Generate predictions on new data.

        Parameters
        ----------
        X :
            Feature matrix (same columns as training X).

        Returns
        -------
        np.ndarray, shape (n_samples,).
        """

    def save(self, path: Path) -> None:
        """Pickle self to ``path``."""
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: Path) -> "Model":
        """Reconstruct a Model from a pickle file.

        Raises TypeError if the loaded object is not the expected class.
        """
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"Expected {cls.__name__}, got {type(obj).__name__}")
        return obj
