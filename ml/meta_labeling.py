"""
InvestYo Quant Platform - Meta-Labeling (Lopez de Prado AFML Ch. 3)
====================================================================
Meta-labeling adds a second-pass binary classifier on top of each primary
signal to estimate P(primary_signal_correct). The meta-label probability is
then used to scale position sizes: if the meta-model is not confident that the
primary signal is generating a true positive, the position is reduced or zeroed.

Key design decisions (from the book):
- The primary signal's own score is always a feature for the meta-model — the
  model conditions on "how confident is the primary signal?"
- Triple-barrier labels (ml/triple_barrier.py) define what "correct" means:
  +1 label → profit-take hit; the primary signal in the same direction was right.
- The meta-label binary target is: 1 if (primary_direction == barrier_label), else 0.
  Vertical timeout (label=0) is treated as a primary-signal failure (binary 0).
- Monthly retraining is the caller's responsibility (main_orchestrator.py or a
  scheduler). MetaLabeler.needs_retrain() returns True after retrain_freq_days.
- Model is persisted to ml/models/meta_<signal_id>_<YYYYMMDD>.pkl.

Integration with SignalAggregator
----------------------------------
``global_meta_registry`` is the singleton MetaLabelerRegistry. The aggregator
(signals/aggregator.py) calls:
    proba = global_meta_registry.get_proba(signal_id, feature_row)
If proba < settings.META_LABEL_MIN_CONFIDENCE (default 0.4), the aggregator sets
meta_label_composite = 0.0, which when multiplied by Kelly Target zeroes the
position for that cycle.
"""

from __future__ import annotations

import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ml.models.base import Model

logger = logging.getLogger("ML.MetaLabeling")

_MODELS_DIR = Path(__file__).parent / "models"
_MODELS_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_META_PARAMS: dict = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 15,
    "learning_rate": 0.05,
    "n_estimators": 500,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
    "class_weight": "balanced",  # P(wrong) and P(right) are rarely 50/50
}


def build_meta_label_target(
    y_primary: pd.Series,
    y_barrier: pd.Series,
) -> pd.Series:
    """Construct binary meta-label target from primary signal and barrier labels.

    Meta-label = 1 when the primary signal direction matches the barrier outcome:
      primary=+1 and barrier=+1  → meta-label = 1  (long signal, profit-take hit)
      primary=-1 and barrier=-1  → meta-label = 1  (short signal, stop-out of a long = win)
      vertical barrier (0)       → meta-label = 0  (primary signal inconclusive/wrong)
      primary and barrier differ → meta-label = 0  (primary signal wrong direction)

    Rows where primary signal = 0 (no signal) are excluded by the caller before
    fitting; this function merely computes the target for all provided rows.

    Parameters
    ----------
    y_primary : pd.Series
        Primary signal direction per event (+1, -1, or 0).
    y_barrier : pd.Series
        Triple-barrier label per event (+1, -1, or 0).

    Returns
    -------
    pd.Series
        Binary meta-label (1 = primary correct, 0 = primary incorrect).
    """
    aligned = y_primary.index.intersection(y_barrier.index)
    yp = y_primary.loc[aligned]
    yb = y_barrier.loc[aligned]
    # Correct iff directions match AND neither is 0 (vertical timeout counts as wrong)
    correct = ((yp == 1) & (yb == 1)) | ((yp == -1) & (yb == -1))
    return correct.astype(int)


class MetaLabeler(Model):
    """Binary LightGBM classifier predicting P(primary_signal_correct).

    Conforms to ``ml.models.base.Model`` ABC (fit/predict/save/load).

    Usage
    -----
    >>> labeler = MetaLabeler(signal_id="timeseries_momentum")
    >>> labeler.fit(X_train, y_primary, y_barrier)
    >>> proba = labeler.predict_proba_scalar(X_today)   # single float in [0,1]
    """

    def __init__(
        self,
        signal_id: str,
        lgbm_params: Optional[dict] = None,
        retrain_freq_days: int = 30,
    ):
        """
        Parameters
        ----------
        signal_id :
            String key matching the ``SignalModule.name`` (e.g., "timeseries_momentum").
        lgbm_params :
            Override any of the default LightGBM parameters.
        retrain_freq_days :
            Number of days after which ``needs_retrain()`` returns True.
        """
        self.signal_id = signal_id
        self.lgbm_params = {**_DEFAULT_META_PARAMS, **(lgbm_params or {})}
        self.retrain_freq_days = retrain_freq_days
        self._model = None
        self._feature_names: list[str] = []
        self._last_trained: Optional[datetime] = None
        self._n_train_samples: int = 0

    # ── Model ABC implementation ──────────────────────────────────────────────

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        t1: Optional[pd.Series] = None,
    ) -> "MetaLabeler":
        """Train from a pre-built meta-label target.

        If ``y`` is already the binary meta-label (output of
        ``build_meta_label_target()``), call ``fit(X, meta_y)`` directly.
        If ``y`` is the primary signal series and you also have ``y_barrier``,
        use ``fit_from_primary(X, y_primary, y_barrier)`` instead.

        Parameters
        ----------
        X :
            Feature matrix (n_samples × n_features). Rows with all-NaN are dropped.
        y :
            Binary meta-label (0 or 1).  Must be aligned with ``X``.
        t1 :
            Unused; present for Model ABC conformance.

        Returns
        -------
        self
        """
        try:
            import lightgbm as lgb
        except ImportError as exc:
            raise ImportError("lightgbm is required: pip install lightgbm") from exc

        common = X.index.intersection(y.index)
        X = X.loc[common].copy()
        y = y.loc[common].copy()

        # Drop rows with NaN target or all-NaN features
        valid = y.notna() & X.notna().any(axis=1)
        X = X.loc[valid]
        y = y.loc[valid]

        if len(X) < 30:
            logger.warning(
                "MetaLabeler[%s].fit: only %d samples (need ≥ 30). Skipping.",
                self.signal_id, len(X),
            )
            return self

        self._feature_names = list(X.columns)
        clf = lgb.LGBMClassifier(**self.lgbm_params)
        clf.fit(X.fillna(0.0), y.values.astype(int))
        self._model = clf
        self._last_trained = datetime.now()
        self._n_train_samples = len(X)
        logger.info(
            "MetaLabeler[%s] trained on %d samples. Features: %s",
            self.signal_id, len(X), self._feature_names,
        )
        return self

    def fit_from_primary(
        self,
        X: pd.DataFrame,
        y_primary: pd.Series,
        y_barrier: pd.Series,
    ) -> "MetaLabeler":
        """Convenience wrapper: builds meta-label target then calls fit().

        Filters out events where the primary signal was 0 (no opinion) before
        training, since the meta-model cannot learn from non-events.
        """
        meta_y = build_meta_label_target(y_primary, y_barrier)
        # Only train on events where the primary signal had a directional opinion
        active = y_primary.loc[y_primary != 0].index
        active = active.intersection(meta_y.index).intersection(X.index)
        return self.fit(X.loc[active], meta_y.loc[active])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Return predicted class labels (0 or 1) for each row in X.

        Part of the Model ABC; prefer ``predict_proba_scalar`` for aggregator
        use (returns a single float rather than a full array).

        Returns
        -------
        np.ndarray of ints (0 or 1), shape (n_samples,).
        Ones if model has never been trained (neutral: assume correct).
        """
        if self._model is None:
            return np.ones(len(X), dtype=int)
        return self._model.predict(self._prepare_X(X))

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Return P(primary_signal_correct) for each row in X.

        Returns
        -------
        np.ndarray of floats in [0, 1], shape (n_samples,).
        All-ones array if model has not been trained.
        """
        if self._model is None:
            return np.ones(len(X), dtype=float)
        return self._model.predict_proba(self._prepare_X(X))[:, 1]

    def predict_proba_scalar(self, X: pd.DataFrame) -> float:
        """Mean P(primary_signal_correct) across rows of X (typically one row).

        This is the method called by MetaLabelerRegistry.get_proba().

        Returns 1.0 (neutral, no-op) if the model has not been trained, so
        position sizing is unchanged until a real model is available.
        """
        if self._model is None:
            logger.debug(
                "MetaLabeler[%s].predict_proba_scalar: no model, returning 1.0.",
                self.signal_id,
            )
            return 1.0
        probas = self.predict_proba(X)
        return float(np.mean(probas)) if len(probas) > 0 else 1.0

    def _prepare_X(self, X: pd.DataFrame) -> pd.DataFrame:
        """Align columns to training feature set and fill NaN with 0."""
        if not self._feature_names:
            return X.fillna(0.0)
        missing = [c for c in self._feature_names if c not in X.columns]
        if missing:
            X = X.copy()
            for c in missing:
                X[c] = 0.0
        return X[self._feature_names].fillna(0.0)

    # ── Lifecycle helpers ─────────────────────────────────────────────────────

    def needs_retrain(self) -> bool:
        """True if the model has never been trained or is past its retrain window."""
        if self._last_trained is None:
            return True
        return (datetime.now() - self._last_trained).days >= self.retrain_freq_days

    # ── Persistence (overrides Model.save/load with signal_id stamping) ───────

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist to ml/models/meta_<signal_id>_<YYYYMMDD>.pkl."""
        if path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            path = _MODELS_DIR / f"meta_{self.signal_id}_{stamp}.pkl"
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info("MetaLabeler[%s] saved to %s", self.signal_id, path)
        return path

    @classmethod
    def load(cls, path: Path) -> "MetaLabeler":
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"Loaded object is not MetaLabeler: {type(obj)}")
        return obj

    @classmethod
    def load_latest(cls, signal_id: str) -> Optional["MetaLabeler"]:
        """Load the most recently saved model for ``signal_id``, or None."""
        pickles = sorted(_MODELS_DIR.glob(f"meta_{signal_id}_*.pkl"))
        if not pickles:
            return None
        return cls.load(pickles[-1])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class MetaLabelerRegistry:
    """Maps ``signal_id -> MetaLabeler``, used by ``signals/aggregator.py``.

    The global singleton ``global_meta_registry`` is imported by the aggregator.
    Callers register trained MetaLabelers via ``register()``; the aggregator
    queries probabilities via ``get_proba()``.

    By default the registry is empty and ``get_proba()`` returns 1.0 (no-op),
    so the platform behaves identically to the pre-meta-label state until a real
    MetaLabeler is registered.
    """

    def __init__(self) -> None:
        self._labelers: dict[str, MetaLabeler] = {}

    def register(self, labeler: MetaLabeler) -> None:
        """Add or replace the MetaLabeler for a given signal_id."""
        self._labelers[labeler.signal_id] = labeler
        logger.info("MetaLabelerRegistry: registered '%s'.", labeler.signal_id)

    def unregister(self, signal_id: str) -> None:
        self._labelers.pop(signal_id, None)

    def has(self, signal_id: str) -> bool:
        return signal_id in self._labelers

    def get_proba(self, signal_id: str, features: pd.DataFrame) -> float:
        """Return P(primary_signal_correct) from the registered MetaLabeler.

        Parameters
        ----------
        signal_id :
            The ``SignalModule.name`` (e.g., "timeseries_momentum").
        features :
            A single-row DataFrame with at least the features the MetaLabeler
            was trained on; any extra columns are silently ignored; any missing
            columns are filled with 0.

        Returns
        -------
        float in [0, 1].  Returns 1.0 if no labeler registered for signal_id.
        """
        labeler = self._labelers.get(signal_id)
        if labeler is None:
            return 1.0
        try:
            return labeler.predict_proba_scalar(features)
        except Exception as exc:
            logger.warning(
                "MetaLabelerRegistry.get_proba[%s] raised %s — returning 1.0.",
                signal_id, exc,
            )
            return 1.0

    def __repr__(self) -> str:
        return f"MetaLabelerRegistry({list(self._labelers.keys())})"


# Module-level singleton imported by signals/aggregator.py
global_meta_registry = MetaLabelerRegistry()
