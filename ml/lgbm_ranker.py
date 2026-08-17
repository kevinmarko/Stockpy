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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ml.models.base import Model
from settings import settings

logger = logging.getLogger("ML.LGBMRanker")

_MODELS_DIR = settings.LOCAL_DATA_ROOT / "ml_models"
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
        use_native_multiindex_cv: Optional[bool] = None,
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
            CombinatorialPurgedCV.split() for purging.  If None, the flatten
            path (see ``use_native_multiindex_cv``) synthesizes a default
            "next row" t1 -- exactly as before this parameter existed. The
            native path REQUIRES an explicit t1 and raises ValueError
            otherwise (see ``use_native_multiindex_cv``).
        use_native_multiindex_cv:
            When True and X has a (date, ticker) MultiIndex, calls
            ``CombinatorialPurgedCV.split()`` directly on the MultiIndex panel
            (PR #648's native support) instead of flattening to a date-only
            index first. When None (default), resolved from
            ``settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED`` (itself
            default False) -- so every existing caller that never passes this
            kwarg keeps today's exact flatten-path behavior unless the
            settings flag is explicitly enabled. Ignored (no effect) when X
            is not a MultiIndex.
        """
        try:
            import lightgbm as lgb
        except ImportError as e:
            raise ImportError("lightgbm is required: pip install lightgbm") from e

        from validation.purged_cv import CombinatorialPurgedCV

        if X.empty or y.empty:
            logger.warning("LGBMCrossSectionalRanker.train: empty X or y — skipping.")
            return self

        if use_native_multiindex_cv is None:
            try:
                from settings import settings as _settings
                use_native_multiindex_cv = bool(
                    getattr(_settings, "LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", False)
                )
            except Exception:
                use_native_multiindex_cv = False

        common_idx = X.index.intersection(y.index)
        X = X.loc[common_idx].copy()
        y = y.loc[common_idx].copy()

        # Drop rows with all-NaN features or NaN target
        valid_mask = X.notna().any(axis=1) & y.notna()
        X = X.loc[valid_mask]
        y = y.loc[valid_mask]
        if t1 is not None:
            # Realign to the filtered X.index (also covers the common_idx
            # intersection above in one step) -- NaN for any row t1 doesn't
            # cover, surfaced honestly by the native path's own t1-required
            # check below rather than silently dropped.
            t1 = t1.reindex(X.index)

        if len(X) < max(10, self.purged_kfold_splits * 2):
            logger.warning("LGBMCrossSectionalRanker.train: too few samples (%d). Skipping.", len(X))
            return self

        is_multi = isinstance(X.index, pd.MultiIndex)

        if is_multi:
            # CPCV's contiguous block partitioning assumes positional order
            # matches chronological order (validation/purged_cv.py raises on
            # this too, but sorting defensively here means the row-filtering
            # above can never desync X/y/t1 from a caller-supplied panel that
            # wasn't already sorted, e.g. one built via per-ticker pd.concat).
            X = X.sort_index(level=0)
            y = y.reindex(X.index)
            if t1 is not None:
                t1 = t1.reindex(X.index)

        # LambdaRank needs a group array: # tickers per date (query).
        # If MultiIndex, group by first level (date); else treat all as one group.
        if is_multi:
            groups = X.index.get_level_values(0).value_counts().sort_index().values
        else:
            groups = np.array([len(X)])

        # Scale target to 5 fixed relevance grades (0–4), the standard approach
        # for LambdaRank. Fixed grade count avoids the LightGBM constraint that
        # all label values must be < number of unique labels in training data.
        N_GRADES = 5
        y_int = (y.clip(0.0, 1.0) * (N_GRADES - 1)).round().astype(int).clip(0, N_GRADES - 1)

        self._feature_names = list(X.columns)

        if use_native_multiindex_cv and is_multi:
            if t1 is None:
                raise ValueError(
                    "LGBMCrossSectionalRanker.train(): t1 is required when "
                    "use_native_multiindex_cv=True and X has a (date, ticker) "
                    "MultiIndex -- CombinatorialPurgedCV.split() cannot safely "
                    "synthesize a default t1 across a MultiIndex (see "
                    "validation/purged_cv.py, PR #648). Pass t1 explicitly, "
                    "aligned to X.index, with plain Date-comparable scalar "
                    "values (not MultiIndex tuples)."
                )
            # Native path: hand CombinatorialPurgedCV.split() the MultiIndex
            # panel directly -- no flatten.
            X_for_cv = X
            y_for_cv = y
            t1_for_cv = t1
        else:
            # Flatten path (default / legacy): CV splitter historically didn't
            # support MultiIndex natively, so a (date, ticker) panel is
            # relabeled to a date-only index first. Purely a relabeling for
            # cv.split()'s own index reads -- row order/positions are
            # untouched, so X_arr/y_arr below (built from the ORIGINAL X/y)
            # stay correctly aligned with cv.split()'s positional train/test
            # indices either way.
            if is_multi:
                cv_index = X.index.get_level_values(0)
                X_for_cv = X.set_axis(cv_index)
                y_for_cv = y.set_axis(cv_index)
                t1_for_cv = t1.set_axis(cv_index) if t1 is not None else None
            else:
                X_for_cv = X
                y_for_cv = y
                t1_for_cv = t1

        # Purged k-fold CV to evaluate generalisation (single final model on all data)
        cv = CombinatorialPurgedCV(
            n_splits=self.purged_kfold_splits,
            n_test_splits=2,
            embargo_pct=self.embargo_pct,
        )

        oof_scores: list[float] = []
        X_arr = X.fillna(0.0).values
        y_arr = y_int.values

        for train_idx, test_idx, _ in cv.split(X_for_cv, y_for_cv, t1_for_cv):
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

        X_df = X_today[self._feature_names].fillna(0.0)
        raw_scores = self._model.predict(X_df)
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
        X_df = X[self._feature_names].fillna(0.0) if self._feature_names else X.fillna(0.0)
        return self._model.predict(X_df)

    # ── persistence ───────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        """Pickle model to ml/models/lgbm_<YYYYMMDD>.pkl."""
        if path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            path = _MODELS_DIR / f"lgbm_{stamp}.pkl"
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info("LGBMCrossSectionalRanker saved to %s", path)
        return path

    @classmethod
    def load(cls, path: Path) -> "LGBMCrossSectionalRanker":
        with open(path, "rb") as f:
            # Bandit B301: local model artifact this pipeline itself trained
            # and wrote, not externally-supplied data -- see ml/models/base.py.
            obj = pickle.load(f)  # nosec B301
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
