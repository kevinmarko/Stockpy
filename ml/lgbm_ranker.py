"""
InvestYo Quant Platform - LightGBM Cross-Sectional Return Ranker
=================================================================
Trains a LightGBM LambdaRank model inside a Purged k-fold CV loop
(validation/purged_cv.py) to predict next-21-day cross-sectional
return rank percentiles.

Design principles (Lopez de Prado AFML Ch. 13):
- Training uses purged k-fold with embargo to prevent serial-correlation leakage.
- Model is an ENSEMBLE INPUT (weight 0.10) — it does not override the rules-based
  signal stack, it adds a weak cross-sectional alpha signal.
- Monthly retraining on an expanding window; model is pickled to
  ml/models/lgbm_<YYYYMMDD>.pkl.
- Scores are forward-filtered: predict_score() runs on today's cross-section
  using only features available as of today (no future data).
"""

from __future__ import annotations

import logging
import os
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ml.models.base import Model

logger = logging.getLogger("ML.LGBMRanker")

_MODELS_DIR = Path(__file__).parent / "models"
_MODELS_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Hyper-parameters (Prompt 4.1 spec)
# ──────────────────────────────────────────────────────────────────────────────
_DEFAULT_PARAMS: dict = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "num_leaves": 31,
    "learning_rate": 0.03,
    "n_estimators": 1000,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
}


class LGBMCrossSectionalRanker(Model):
    """LightGBM LambdaRank model trained inside purged k-fold CV.

    Usage
    -----
    >>> ranker = LGBMCrossSectionalRanker()
    >>> ranker.train(X_panel, y_ranks, t1_series)
    >>> scores = ranker.predict_score(X_today)   # pd.Series[ticker -> rank_pct]
    """

    def __init__(self, params: Optional[dict] = None, purged_kfold_splits: int = 5,
                 embargo_pct: float = 0.01):
        self.params = {**_DEFAULT_PARAMS, **(params or {})}
        self.purged_kfold_splits = purged_kfold_splits
        self.embargo_pct = embargo_pct
        self._model = None
        self._feature_names: list[str] = []
        self._last_trained: Optional[datetime] = None

    # ── training ──────────────────────────────────────────────────────────────

    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        t1: Optional[pd.Series] = None,
    ) -> "LGBMCrossSectionalRanker":
        """Train on a panel of (date × ticker) observations.

        Parameters
        ----------
        X:
            Feature matrix.  Index = (date, ticker) MultiIndex or flat index
            matching y's index.
        y:
            Target: cross-sectional forward-21d return rank percentile ∈ [0,1].
            Must be integer-convertible for LambdaRank (we scale to [0, 99]).
        t1:
            Event end times aligned to X's index.  Passed to
            CombinatorialPurgedCV.split() for purging.  If None, defaults to
            index-position + 1.
        """
        try:
            import lightgbm as lgb
        except ImportError as e:
            raise ImportError("lightgbm is required: pip install lightgbm") from e

        from validation.purged_cv import CombinatorialPurgedCV

        if X.empty or y.empty:
            logger.warning("LGBMCrossSectionalRanker.train: empty X or y — skipping.")
            return self

        common_idx = X.index.intersection(y.index)
        X = X.loc[common_idx].copy()
        y = y.loc[common_idx].copy()

        # Drop rows with all-NaN features or NaN target
        valid_mask = X.notna().any(axis=1) & y.notna()
        X = X.loc[valid_mask]
        y = y.loc[valid_mask]

        if len(X) < max(10, self.purged_kfold_splits * 2):
            logger.warning("LGBMCrossSectionalRanker.train: too few samples (%d). Skipping.", len(X))
            return self

        # LambdaRank needs a group array: # tickers per date (query).
        # If MultiIndex, group by first level (date); else treat all as one group.
        if isinstance(X.index, pd.MultiIndex):
            groups = X.index.get_level_values(0).value_counts().sort_index().values
        else:
            groups = np.array([len(X)])

        # Scale target to 5 fixed relevance grades (0–4), the standard approach
        # for LambdaRank. Fixed grade count avoids the LightGBM constraint that
        # all label values must be < number of unique labels in training data.
        N_GRADES = 5
        y_int = (y.clip(0.0, 1.0) * (N_GRADES - 1)).round().astype(int).clip(0, N_GRADES - 1)

        self._feature_names = list(X.columns)

        # For purged CV: flatten MultiIndex to a DatetimeIndex (CV splitter
        # doesn't support MultiIndex natively).
        if isinstance(X.index, pd.MultiIndex):
            cv_index = X.index.get_level_values(0)
            X_for_cv = X.set_axis(cv_index)
            y_for_cv = y.set_axis(cv_index)
        else:
            X_for_cv = X
            y_for_cv = y

        # Purged k-fold CV to evaluate generalisation (single final model on all data)
        cv = CombinatorialPurgedCV(
            n_splits=self.purged_kfold_splits,
            n_test_splits=2,
            embargo_pct=self.embargo_pct,
        )

        oof_scores: list[float] = []
        X_arr = X.fillna(0.0).values
        y_arr = y_int.values

        for train_idx, test_idx, _ in cv.split(X_for_cv, y_for_cv, t1):
            if len(train_idx) < 5 or len(test_idx) < 1:
                continue
            X_tr, X_te = X_arr[train_idx], X_arr[test_idx]
            y_tr, y_te = y_arr[train_idx], y_arr[test_idx]

            # Each fold is treated as one query group (purged CV slices arbitrarily)
            fold_model = lgb.LGBMRanker(**{k: v for k, v in self.params.items()
                                           if k not in ("n_estimators", "early_stopping_rounds")})
            try:
                fold_model.fit(
                    X_tr, y_tr,
                    group=[len(y_tr)],
                    eval_set=[(X_te, y_te)],
                    eval_group=[[len(y_te)]],
                    callbacks=[lgb.early_stopping(
                        stopping_rounds=self.params.get("early_stopping_rounds", 50),
                        verbose=False,
                    )],
                )
                oof_scores.append(fold_model.best_score_["valid_0"]["ndcg@1"])
            except Exception as exc:
                logger.debug("LGBMRanker fold failed: %s", exc)

        if oof_scores:
            logger.info("LGBMRanker CV NDCG@1 mean=%.4f std=%.4f over %d folds",
                        np.mean(oof_scores), np.std(oof_scores), len(oof_scores))

        # Final model on full data (single-group mode)
        final_model = lgb.LGBMRanker(**{k: v for k, v in self.params.items()
                                         if k not in ("early_stopping_rounds",)})
        final_model.fit(X_arr, y_arr, group=[len(y_arr)])

        self._model = final_model
        self._last_trained = datetime.now(tz=None)
        logger.info("LGBMCrossSectionalRanker trained on %d samples. Features: %s",
                    len(X), self._feature_names)
        return self

    # ── inference ─────────────────────────────────────────────────────────────

    def predict_score(self, X_today: pd.DataFrame) -> pd.Series:
        """Score today's cross-section.  Returns rank ∈ [0, 1] per ticker.

        If the model has never been trained, returns a neutral 0.5 Series
        (logged as a warning) rather than raising.
        """
        if self._model is None:
            logger.warning("LGBMCrossSectionalRanker.predict_score called before train(). "
                           "Returning neutral 0.5 scores.")
            return pd.Series(0.5, index=X_today.index)

        missing = [c for c in self._feature_names if c not in X_today.columns]
        if missing:
            logger.warning("Missing features: %s — filling with NaN.", missing)
            for c in missing:
                X_today = X_today.copy()
                X_today[c] = np.nan

        X_arr = X_today[self._feature_names].fillna(0.0).values
        raw_scores = self._model.predict(X_arr)
        # Normalise to [0, 1] percentile rank within this cross-section
        ranks = pd.Series(raw_scores, index=X_today.index).rank(pct=True)
        return ranks

    # ── Model ABC conformance wrappers ────────────────────────────────────────
    # ``train()`` is the primary method; fit/predict satisfy the abstract base.

    def fit(
        self,
        X: "pd.DataFrame",
        y: "pd.Series",
        t1: "Optional[pd.Series]" = None,
    ) -> "LGBMCrossSectionalRanker":
        """Model ABC: delegates to ``train(X, y, t1)``."""
        return self.train(X, y, t1)

    def predict(self, X: "pd.DataFrame") -> "np.ndarray":
        """Model ABC: returns raw ranker scores (not normalised rank percentiles)."""
        if self._model is None:
            return np.full(len(X), 0.5)
        X_arr = X[self._feature_names].fillna(0.0).values if self._feature_names else X.fillna(0.0).values
        return self._model.predict(X_arr)

    # ── persistence ───────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        """Pickle model to ml/models/lgbm_<YYYYMMDD>.pkl."""
        if path is None:
            stamp = datetime.utcnow().strftime("%Y%m%d")
            path = _MODELS_DIR / f"lgbm_{stamp}.pkl"
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info("LGBMCrossSectionalRanker saved to %s", path)
        return path

    @classmethod
    def load(cls, path: Path) -> "LGBMCrossSectionalRanker":
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"Loaded object is not LGBMCrossSectionalRanker: {type(obj)}")
        return obj

    @classmethod
    def load_latest(cls) -> Optional["LGBMCrossSectionalRanker"]:
        """Load the most recent persisted model, or None if no model exists."""
        pickles = sorted(_MODELS_DIR.glob("lgbm_*.pkl"))
        if not pickles:
            return None
        return cls.load(pickles[-1])
