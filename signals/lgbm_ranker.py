"""
InvestYo Quant Platform - LightGBM Cross-Sectional Ranker SignalModule
=======================================================================
Thin wrapper around ml/lgbm_ranker.LGBMCrossSectionalRanker that plugs into
the two-phase hook pattern used by all cross-sectional signals.

Two-phase:
  pre_compute(universe_df, context)  — scores today's cross-section and stores
                                       per-ticker rank in context.lgbm_scores.
  compute(row, context)              — maps stored rank to [-1, +1] signal score.

Weight is defined solely by settings.SIGNAL_WEIGHTS["lgbm_ranker"] (SignalAggregator
never reads a per-module default) — kept at 0.0 while the persisted model remains
non-deployable per ml/registry.yaml (cpcv_dsr far below the 0.95 gate). This module
NEVER overrides the rules-based signal stack; it is one input among many to
SignalAggregator.aggregate().

Monthly retraining is the *caller's* responsibility (main_orchestrator.py or a
scheduled job); this module just loads the latest persisted model.  If no model
has been trained yet, it returns 0.0 for every ticker and logs a warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict

import numpy as np
import pandas as pd

from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry

logger = logging.getLogger("Signals.LGBMRanker")

_NAME = "lgbm_ranker"


class LGBMRankerSignal(SignalModule):
    """Cross-sectional LightGBM ranker signal module (weight from settings.SIGNAL_WEIGHTS)."""

    name = _NAME

    def pre_compute(self, universe_df: pd.DataFrame, context: SignalContext) -> None:
        """Score the full cross-section using the latest persisted LGBMRanker.

        Stores results in context.lgbm_scores: {ticker -> rank_pct ∈ [0,1]}.
        Silently returns neutral (0.5) for all tickers if no model is available.
        """
        from ml.lgbm_ranker import LGBMCrossSectionalRanker
        from ml.feature_engineering import build_pit_feature_matrix

        context.lgbm_scores = {}

        if universe_df.empty:
            return

        try:
            ranker = LGBMCrossSectionalRanker.load_latest()
        except Exception as exc:
            # Shipping without a trained LGBM model is the documented default
            # state (ml/registry.yaml: deployable=false) — INFO, not WARNING,
            # so this never spams the per-cycle logs on a vanilla deployment.
            logger.info(
                "LGBMRankerSignal.pre_compute: no persisted model available "
                "(%s); contributing neutral scores this cycle.", exc,
            )
            ranker = None

        # No model → neutral for the whole cross-section; skip the (wasted)
        # feature build entirely.
        if ranker is None:
            context.lgbm_scores = {t: 0.5 for t in universe_df.index}
            return

        vix = getattr(getattr(context, "macro", None), "vix", None)

        try:
            feat_df = build_pit_feature_matrix(
                universe_df,
                as_of_date=getattr(context, "as_of_date", None),
                macro_vix=vix,
            )
        except Exception as exc:
            logger.warning("LGBMRankerSignal.pre_compute: feature build failed: %s. Neutral.", exc)
            context.lgbm_scores = {t: 0.5 for t in universe_df.index}
            return

        try:
            scores = ranker.predict_score(feat_df)
            context.lgbm_scores = scores.to_dict()
        except Exception as exc:
            logger.warning("LGBMRankerSignal.pre_compute: predict_score failed: %s. Neutral.", exc)
            context.lgbm_scores = {t: 0.5 for t in feat_df.index}

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Map pre-computed rank percentile to [-1, +1] signal score."""
        ticker = str(row.get("Symbol", row.name if hasattr(row, "name") else ""))
        lgbm_scores: dict = getattr(context, "lgbm_scores", {})
        
        if ticker not in lgbm_scores or lgbm_scores[ticker] != lgbm_scores[ticker]:
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation="WARNING: LGBM scores unavailable. Score set to 0 (neutral)."
            )
            
        rank = lgbm_scores[ticker]

        # Linear map: rank 1.0 -> +1.0, rank 0.0 -> -1.0
        score = 2.0 * (float(rank) - 0.5)
        score = float(np.clip(score, -1.0, 1.0))

        return SignalOutput(
            score=score,
            confidence=1.0,
            explanation=f"LGBM cross-sectional rank={rank:.3f}",
        )

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        import numpy as np

        if "Symbol" in df:
            tickers = df["Symbol"].astype(str)
        else:
            tickers = pd.Series(df.index.astype(str), index=df.index)
            
        lgbm_scores = getattr(context, "lgbm_scores", {})
        
        ranks = tickers.map(lgbm_scores)
        valid = ranks.notna()
        
        scores = pd.Series(0.0, index=df.index)
        scores[valid] = np.clip(2.0 * (ranks[valid] - 0.5), -1.0, 1.0)
        
        confidences = pd.Series(0.0, index=df.index)
        confidences[valid] = 1.0
        
        explanations = pd.Series("WARNING: LGBM scores unavailable. Score set to 0 (neutral).", index=df.index)
        explanations[valid] = ranks[valid].map(lambda r: f"LGBM cross-sectional rank={r:.3f}")
        
        return pd.DataFrame({
            "score": scores,
            "confidence": confidences,
            "explanation": explanations,
            "meta_label_proba": np.nan
        }, index=df.index)


# Auto-register module (one input among many to SignalAggregator.aggregate();
# contributes nothing to final_score while settings.SIGNAL_WEIGHTS["lgbm_ranker"]
# is 0.0 — see the module docstring for the deployability gate that governs it).
global_registry.register(LGBMRankerSignal())
