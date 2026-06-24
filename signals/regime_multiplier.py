"""
InvestYo Quant Platform - HMM Regime Position-Sizing Multiplier
===================================================================
This module deliberately does NOT add directional alpha to the 0-100
scoring kernel -- its compute() always returns score=0.0, so it contributes
nothing to SignalAggregator's weighted-sum final_score regardless of its
configured weight. Its only job is to carry context.macro.hmm_risk_on_probability
(regime/hmm_regime.py's second opinion) through the existing
SignalModule/SignalAggregator plumbing so StrategyEngine can read it back out
of aggregator.aggregate()'s `outputs` dict (already returned for
introspection -- see signals/aggregator.py) and use it to MULTIPLY the final
Kelly Target, not add to score.

WHY NOT JUST A NEW STRATEGYENGINE PARAMETER?
-----------------------------------------------
Routing this through the signal registry (rather than threading
hmm_risk_on_probability as a separate ad-hoc argument) keeps a single,
auditable path by which macro/regime information reaches StrategyEngine,
consistent with how RSI(2) mean reversion's regime gate
(signals/rsi2_mean_reversion.py) and every other macro-aware behavior in this
codebase is wired through SignalContext.macro rather than bespoke parameters.

CONTRACT
---------
compute() returns SignalOutput(score=0.0, confidence=multiplier, ...) where
multiplier = hmm_risk_on_probability if available, else 1.0 (neutral -- never
penalizes sizing just because the HMM didn't run this cycle). StrategyEngine
reads outputs['regime_multiplier'].confidence and multiplies the final Kelly
Target by it -- see strategy_engine.py's _calculate_kelly_sizing wiring.
"""

from __future__ import annotations

import logging

import pandas as pd

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry

logger = logging.getLogger(__name__)


class RegimeMultiplierSignal(SignalModule):
    """Carries the HMM risk_on_probability second opinion through the signal
    pipeline as a position-sizing multiplier, not a score contribution."""

    name = "regime_multiplier"
    required_features: list[str] = []

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        hmm_p = getattr(context.macro, "hmm_risk_on_probability", None)

        if hmm_p is None:
            return SignalOutput(
                score=0.0,
                confidence=1.0,
                explanation="DETAIL: regime_multiplier: HMM unavailable this cycle; sizing multiplier=1.0 (neutral).",
            )

        multiplier = float(hmm_p)
        return SignalOutput(
            score=0.0,
            confidence=multiplier,
            explanation=f"DETAIL: regime_multiplier: HMM risk_on_probability={multiplier:.3f} -> sizing multiplier={multiplier:.3f}.",
        )


# Auto-register
global_registry.register(RegimeMultiplierSignal())
