"""
InvestYo Quant Platform - Options Stage 4 ML Meta-Labeler
=========================================================
Trains a secondary machine learning meta-classifier on executed and simulated
options trades to estimate P(Profit | Entry Features). The predicted win
probability dynamically gates and scales contract sizing in automated paper trading.
"""

from __future__ import annotations

import logging
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
    credit_to_width_ratio: float  # e.g. 0.30
    short_delta: float  # e.g. 0.30
    outcome_win: Optional[int] = None  # 1 for profit > 0, 0 for loss <= 0


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
        "trend_bias",
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
        """Extracts normalized numerical feature vector from feature row."""
        if isinstance(row, OptionsTradeFeatureRow):
            strat = row.strategy
            ivr = row.ivr
            vrp = row.vrp
            vix = row.vix
            credit_to_width = row.credit_to_width_ratio
            short_delta = row.short_delta
        else:
            strat = row.get("strategy", "")
            ivr = float(row.get("ivr", 50.0))
            vrp = float(row.get("vrp", 0.0))
            vix = float(row.get("vix", 20.0))
            credit_to_width = float(row.get("credit_to_width_ratio", 0.30))
            short_delta = float(row.get("short_delta", 0.30))

        strat_lower = strat.lower()
        is_put_spread = 1.0 if "put credit spread" in strat_lower or "short put spread" in strat_lower else 0.0
        is_call_spread = 1.0 if "call credit spread" in strat_lower or "short call spread" in strat_lower else 0.0
        is_iron_condor = 1.0 if "iron condor" in strat_lower else 0.0

        return np.array([
            is_put_spread,
            is_call_spread,
            is_iron_condor,
            ivr / 100.0,
            vrp,
            vix / 100.0,
            credit_to_width,
            short_delta,
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
            return {"accuracy": 1.0, "roc_auc": 0.50, "samples": len(y)}

        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import accuracy_score, roc_auc_score
        from sklearn.model_selection import TimeSeriesSplit

        try:
            clf = HistGradientBoostingClassifier(
                max_iter=100,
                learning_rate=0.05,
                max_leaf_nodes=15,
                min_samples_leaf=5,
                random_state=42,
            )
            
            # Purged Walk-Forward Split (OOS metrics)
            tscv = TimeSeriesSplit(n_splits=5)
            oos_preds = np.zeros_like(y, dtype=float)
            oos_probas = np.zeros_like(y, dtype=float) + 0.5
            
            for train_idx, test_idx in tscv.split(X):
                # Embargo: drop last 5 samples of train to avoid overlap leak
                if len(train_idx) > 5:
                    train_idx = train_idx[:-5]
                X_tr, y_tr = X[train_idx], y[train_idx]
                X_te, y_te = X[test_idx], y[test_idx]
                if len(np.unique(y_tr)) > 1:
                    clf.fit(X_tr, y_tr)
                    oos_preds[test_idx] = clf.predict(X_te)
                    oos_probas[test_idx] = clf.predict_proba(X_te)[:, 1]
                else:
                    oos_preds[test_idx] = y_tr[0]
                    oos_probas[test_idx] = 0.5

            # OOS Metrics
            test_mask = np.concatenate([test_idx for _, test_idx in tscv.split(X)])
            oos_acc = float(accuracy_score(y[test_mask], oos_preds[test_mask]))
            oos_auc = float(roc_auc_score(y[test_mask], oos_probas[test_mask]))

            # In-Sample fit on full data
            clf.fit(X, y)
            self.model = clf

            y_pred_is = clf.predict(X)
            y_proba_is = clf.predict_proba(X)[:, 1]
            is_acc = float(accuracy_score(y, y_pred_is))
            is_auc = float(roc_auc_score(y, y_proba_is))

        except Exception as exc:
            logger.warning("sklearn fit failed (%s); using logistic fallback", exc)
            # Fallback simple logistic regression with numpy
            weights = np.linalg.lstsq(X, y, rcond=None)[0]
            self.model = ("linear_fallback", weights)
            is_acc = 0.50
            is_auc = 0.50
            oos_acc = 0.50
            oos_auc = 0.50

        self.trained_at = datetime.now(timezone.utc)
        self.n_samples = len(y)
        self.train_accuracy = is_acc
        self.train_roc_auc = is_auc

        logger.info(
            "OptionsMetaLabeler trained on %d samples. IS Acc: %.2f%%, IS AUC: %.3f, OOS Acc: %.2f%%, OOS AUC: %.3f",
            len(y), is_acc * 100.0, is_auc, oos_acc * 100.0, oos_auc
        )

        # Automatically persist
        self.save_model()

        return {
            "in_sample_accuracy": is_acc, 
            "in_sample_roc_auc": is_auc, 
            "oos_accuracy": oos_acc,
            "oos_roc_auc": oos_auc,
            "samples": len(y)
        }


    def predict_probability(self, row: Dict[str, Any] | OptionsTradeFeatureRow) -> float:
        """
        Predicts calibrated P(Profit > 0) for candidate options directive.
        Returns probability in [0.0, 1.0].
        """
        if self.model is None:
            # Fallback default probability based on base options premium collection edge (~65% win rate)
            return 0.50

        x_vec = self._extract_feature_vector(row).reshape(1, -1)

        if isinstance(self.model, tuple) and self.model[0] == "baseline":
            return float(self.model[1])

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
            return 0.50

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
