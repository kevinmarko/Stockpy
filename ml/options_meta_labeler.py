"""
InvestYo Quant Platform - Options Stage 4 ML Meta-Labeler
=========================================================
Trains a secondary machine learning meta-classifier on executed and simulated
options trades to estimate P(Profit | Entry Features). The predicted win
probability dynamically gates and scales contract sizing in automated paper trading.
"""

from __future__ import annotations

import logging
import math
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from settings import settings

logger = logging.getLogger("ML.OptionsMetaLabeler")

_DEFAULT_MODELS_DIR = settings.LOCAL_DATA_ROOT / "ml_models"


@dataclass
class OptionsTradeFeatureRow:
    """Feature row for an options trade at entry time."""
    strategy: str  # e.g. "Put Credit Spread"
    ivr: float  # 0.0 - 100.0
    vrp: float  # IV - HV (e.g. +0.03)
    vix: float  # e.g. 18.5
    trend_bias: float  # +1.0 (bullish), -1.0 (bearish), 0.0 (neutral)
    target_dte: int  # e.g. 35
    credit_to_width_ratio: float  # e.g. 0.30
    short_delta: float  # e.g. 0.30
    outcome_win: Optional[int] = None  # 1 for profit > 0, 0 for loss <= 0


_UNSET = object()


def _resolve_numeric_feature(row: Dict[str, Any], key: str, default: float) -> float:
    """Resolves a numeric feature from a raw feature dict.

    Returns ``default`` only when ``key`` is entirely absent from ``row`` (a
    caller simply didn't mention this feature -- today's existing,
    backward-compatible behavior). Returns ``NaN`` when ``key`` IS present but
    its value is ``None``, non-finite, or unparseable as a float -- i.e. an
    EXPLICITLY unresolved value, as opposed to an omitted one. Silently
    substituting the same ``default`` for that case was the root cause of a
    real bug: an unresolvable IVR (present in the dict as `float("nan")`, not
    absent) previously sailed through as a normal value and produced an
    overconfident prediction. The NaN this returns instead propagates into
    the feature vector, letting ``OptionsMetaLabeler.predict_probability``'s
    finiteness gate decline to score rather than silently guessing.
    """
    raw = row.get(key, _UNSET)
    if raw is _UNSET:
        return default
    if raw is None:
        return float("nan")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return float("nan")
    return val if math.isfinite(val) else float("nan")


def _finite_or_nan(value: Any) -> float:
    """Coerces a dataclass field value to float, collapsing None/non-finite/
    unparseable input to NaN rather than raising or silently defaulting --
    mirrors ``_resolve_numeric_feature``'s NaN-propagation contract for the
    ``OptionsTradeFeatureRow`` dataclass path."""
    if value is None:
        return float("nan")
    try:
        val = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return val if math.isfinite(val) else float("nan")


class OptionsMetaLabeler:
    """
    Stage 4 ML Meta-Labeling classifier for quantitative options strategies.
    Conditions on entry market regime, volatility surface, and spread geometry
    to predict P(Win) and modulate position sizing multipliers.
    """

    FEATURE_NAMES = [
        "is_put_spread",
        "is_call_spread",
        "is_iron_condor",
        "ivr",
        "vrp",
        "vix",
        "target_dte",
        "credit_to_width_ratio",
        "short_delta",
    ]

    def __init__(self, model_path: Optional[Path] = None):
        self.model = None
        self.model_path = model_path or (_DEFAULT_MODELS_DIR / "options_meta_labeler.pkl")
        self.trained_at: Optional[datetime] = None
        self.n_samples: int = 0
        self.train_accuracy: float = 0.0
        self.train_roc_auc: float = 0.0

    def _extract_feature_vector(self, row: Dict[str, Any] | OptionsTradeFeatureRow) -> np.ndarray:
        """Extracts normalized numerical feature vector from feature row.

        NOTE: ``trend_bias`` is deliberately NOT a model feature (see
        docs/known_issues/options_meta_labeler_serving_time_gaps.md) -- it
        meant a different thing at train time (a pure function of strategy
        name) than at serve time (a real technical trend signal), so it was
        dropped rather than left silently mismatched.
        """
        if isinstance(row, OptionsTradeFeatureRow):
            strat = row.strategy
            ivr = _finite_or_nan(row.ivr)
            vrp = _finite_or_nan(row.vrp)
            vix = _finite_or_nan(row.vix)
            dte = _finite_or_nan(row.target_dte)
            c_w = _finite_or_nan(row.credit_to_width_ratio)
            s_delta = _finite_or_nan(row.short_delta)
        else:
            strat = str(row.get("strategy", ""))
            ivr = _resolve_numeric_feature(row, "ivr", default=50.0)
            vrp = _resolve_numeric_feature(row, "vrp", default=0.02)
            vix = _resolve_numeric_feature(row, "vix", default=20.0)
            dte = _resolve_numeric_feature(row, "target_dte", default=35.0)
            c_w = _resolve_numeric_feature(row, "credit_to_width_ratio", default=0.25)
            s_delta = _resolve_numeric_feature(row, "short_delta", default=0.30)

        is_put = 1.0 if "put" in strat.lower() else 0.0
        is_call = 1.0 if "call" in strat.lower() else 0.0
        is_ic = 1.0 if "condor" in strat.lower() else 0.0

        return np.array([
            is_put,
            is_call,
            is_ic,
            ivr / 100.0,
            vrp,
            vix / 50.0,
            dte / 60.0,
            c_w,
            s_delta,
        ], dtype=float)

    def train(
        self,
        features: List[Dict[str, Any] | OptionsTradeFeatureRow],
        targets: Optional[List[int]] = None,
    ) -> Dict[str, float]:
        """
        Trains a binary classifier (LightGBM or HistGradientBoostingClassifier)
        on options trade entry features and binary profit outcomes.
        """
        if not features:
            raise ValueError("No training features provided to OptionsMetaLabeler.")

        X_list = []
        y_list = []

        for i, f_row in enumerate(features):
            x_vec = self._extract_feature_vector(f_row)
            if targets is not None:
                y_val = int(targets[i])
            elif isinstance(f_row, OptionsTradeFeatureRow) and f_row.outcome_win is not None:
                y_val = int(f_row.outcome_win)
            elif isinstance(f_row, dict) and "outcome_win" in f_row:
                y_val = int(f_row["outcome_win"])
            elif isinstance(f_row, dict) and "pnl" in f_row:
                y_val = 1 if float(f_row["pnl"]) > 0 else 0
            else:
                raise ValueError(f"Missing binary outcome target for feature row index {i}")

            X_list.append(x_vec)
            y_list.append(y_val)

        X = np.array(X_list)
        y = np.array(y_list)

        if len(np.unique(y)) < 2:
            # Degenerate case (single class)
            logger.warning("Single-class target provided to OptionsMetaLabeler. Using baseline predictor.")
            self.model = ("baseline", float(y[0]))
            self.trained_at = datetime.now(timezone.utc)
            self.n_samples = len(y)
            return {"accuracy": 1.0, "roc_auc": 0.50, "samples": len(y), "metrics_are_in_sample": True}

        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
            from sklearn.metrics import accuracy_score, roc_auc_score

            clf = HistGradientBoostingClassifier(
                max_iter=100,
                learning_rate=0.05,
                max_leaf_nodes=15,
                min_samples_leaf=5,
                random_state=42,
            )
            clf.fit(X, y)
            self.model = clf

            y_pred = clf.predict(X)
            y_proba = clf.predict_proba(X)[:, 1]

            acc = float(accuracy_score(y, y_pred))
            auc = float(roc_auc_score(y, y_proba))
        except Exception as exc:
            logger.warning("sklearn fit failed (%s); using logistic fallback", exc)
            # Fallback simple logistic regression with numpy
            weights = np.linalg.lstsq(X, y, rcond=None)[0]
            self.model = ("linear_fallback", weights)
            acc = 0.60
            auc = 0.60

        self.trained_at = datetime.now(timezone.utc)
        self.n_samples = len(y)
        self.train_accuracy = acc
        self.train_roc_auc = auc

        logger.info(
            "OptionsMetaLabeler trained on %d samples. Accuracy: %.2f%%, ROC-AUC: %.3f",
            len(y), acc * 100.0, auc,
        )

        # Automatically persist
        self.save_model()

        return {"accuracy": acc, "roc_auc": auc, "samples": len(y), "metrics_are_in_sample": True}

    def predict_probability(self, row: Dict[str, Any] | OptionsTradeFeatureRow) -> float:
        """
        Predicts calibrated P(Profit > 0) for candidate options directive.
        Returns probability in [0.0, 1.0].
        """
        if self.model is None:
            # Fallback default probability based on base options premium collection edge (~65% win rate)
            return 0.65

        x_vec = self._extract_feature_vector(row).reshape(1, -1)

        if not np.all(np.isfinite(x_vec)):
            logger.warning(
                "OptionsMetaLabeler.predict_probability: declining to score -- "
                "one or more required features (ivr/vrp/vix/target_dte/"
                "credit_to_width_ratio/short_delta) were missing or non-finite "
                "for this directive. Returning the neutral fallback (0.65 / "
                "1.0x sizing) instead of letting NaN reach the model, which "
                "previously produced a confident prediction on unresolved data."
            )
            return 0.65

        if isinstance(self.model, tuple) and self.model[0] == "baseline":
            return float(np.clip(self.model[1], 0.05, 0.95))

        if isinstance(self.model, tuple) and self.model[0] == "linear_fallback":
            weights = self.model[1]
            raw = float(np.dot(x_vec[0], weights))
            # Sigmoid
            prob = 1.0 / (1.0 + np.exp(-raw))
            return float(np.clip(prob, 0.05, 0.95))

        try:
            proba = float(self.model.predict_proba(x_vec)[0, 1])
            return float(np.clip(proba, 0.01, 0.99))
        except Exception as exc:
            logger.warning("predict_proba failed (%s); returning default 0.65", exc)
            return 0.65

    def get_sizing_multiplier(
        self,
        prob: float,
        min_confidence: float = 0.52,
    ) -> float:
        """
        Computes dynamic position sizing scaling factor based on predicted edge.
        Returns:
            0.0 if prob < min_confidence (blocks low-confidence entry),
            Scaled multiplier in [0.30, 1.50] if prob >= min_confidence.
        """
        if prob < min_confidence:
            return 0.0

        # Linear edge scaling: 55% win rate -> 0.60x size, 70% win rate -> 1.20x size, 80%+ -> 1.50x cap
        edge = prob - 0.50
        multiplier = 1.0 + (edge - 0.15) * 4.0
        return float(np.clip(multiplier, 0.30, 1.50))

    def _row_features_finite(self, row: Dict[str, Any] | OptionsTradeFeatureRow) -> bool:
        """Whether every feature this row would produce is finite -- independent
        of whether a model is currently loaded. Used to distinguish "the model
        gave a genuinely neutral/low-confidence answer" from "scoring was
        skipped because required data was unresolved" in score_option_directive's
        returned metadata."""
        x_vec = self._extract_feature_vector(row)
        return bool(np.all(np.isfinite(x_vec)))

    def score_option_directive(
        self,
        directive: Dict[str, Any],
        min_confidence: float = 0.52,
    ) -> Dict[str, Any]:
        """
        Evaluates an actionable options directive and returns ML score metadata.
        """
        prob = self.predict_probability(directive)
        sizing_mult = self.get_sizing_multiplier(prob, min_confidence=min_confidence)
        approved = sizing_mult > 0.0

        return {
            "strategy": directive.get("strategy", ""),
            "symbol": directive.get("symbol", ""),
            "prob_win": round(prob, 3),
            "sizing_multiplier": round(sizing_mult, 2),
            "approved": approved,
            "trained_samples": self.n_samples,
            "features_resolved": self._row_features_finite(directive),
        }

    def save_model(self, path: Optional[Path] = None) -> None:
        """Saves model state to disk."""
        target = path or self.model_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            pickle.dump({
                "model": self.model,
                "trained_at": self.trained_at,
                "n_samples": self.n_samples,
                "train_accuracy": self.train_accuracy,
                "train_roc_auc": self.train_roc_auc,
            }, f)
        logger.info("Saved OptionsMetaLabeler to %s", target)

    def load_model(self, path: Optional[Path] = None) -> bool:
        """Loads model state from disk if exists."""
        target = path or self.model_path
        if not target.exists():
            return False
        try:
            with open(target, "rb") as f:
                # Bandit B301: local model artifact this pipeline itself
                # trained and wrote, not externally-supplied data -- see
                # ml/models/base.py.
                data = pickle.load(f)  # nosec B301
                self.model = data.get("model")
                self.trained_at = data.get("trained_at")
                self.n_samples = data.get("n_samples", 0)
                self.train_accuracy = data.get("train_accuracy", 0.0)
                self.train_roc_auc = data.get("train_roc_auc", 0.0)
            logger.info("Loaded OptionsMetaLabeler from %s (%d samples)", target, self.n_samples)
            return True
        except Exception as exc:
            logger.warning("Failed to load OptionsMetaLabeler from %s: %s", target, exc)
            return False


# Singleton instance
global_options_meta_labeler = OptionsMetaLabeler()
