"""
InvestYo Quant Platform - VRP Premium Selling Signal Module
=============================================================
Scores the Volatility Risk Premium (VRP) options-selling regime gate. This
module does NOT price options or select strikes itself -- that logic already
lives in technical_options_engine.py::OptionsPricingRecommender
(``generate_strategy_pricing_matrix``/``find_strike_for_delta``/
``black_scholes_pricing_and_greeks``). It scores WHETHER the regime favors
selling premium on a given symbol, reusing the same True_IVR/VRP columns
``pipeline/production_steps.py::OptionsAnalysisStep`` already writes onto
every dashboard row every cycle -- zero new computation. Backs the
"vrp-premium-selling" Pilot (``pilots/catalog.py``).
"""

import pandas as pd

from dto_models import MacroEconomicDTO
from settings import settings
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry

# Per-symbol gate thresholds -- mirrors the identical VRP regime rule already
# enforced in
# technical_options_engine.py::OptionsPricingRecommender.generate_strategy_pricing_matrix
# and documented in this repo's own CLAUDE.md "Conventions enforced" section:
# "Options premium selling (e.g. Put Credit Spreads, Iron Condors) is gated
# by VRP regime rules: must have true_ivr > 50, VRP > 0.02, VIX < 30, and no
# CREDIT EVENT. If gated, recommender returns Cash/Wait." VRP_MIN_THRESHOLD is
# sourced from settings.OPTIONS_VRP_THRESHOLD (not re-hardcoded) so this gate
# can never silently drift out of sync with technical_options_engine.py's own
# gate or execution/options_queue_builder.py's.
IVR_SELL_THRESHOLD = 50.0
VRP_MIN_THRESHOLD = settings.OPTIONS_VRP_THRESHOLD

# The macro-level half of the gate (VIX / CREDIT EVENT) is handled centrally
# via is_active_in_regime() below, NOT baked into the per-row score -- per
# this codebase's own convention (CLAUDE.md: "Use [is_active_in_regime] for
# regime-fragile signals ... rather than relying on compute() to self-zero,
# so the suppression is enforced centrally and is impossible to forget
# per-module"). The per-symbol half (True_IVR > 50, VRP > 2%) genuinely
# varies by symbol and is scored per-row here instead.
VIX_MAX_THRESHOLD = 30.0

# VRP magnitude at which the score's VRP component saturates to 1.0 -- a
# scaling constant, not a gate. A VRP of 10%+ is already a very rich premium
# by historical standards; there is no reason to reward an even richer
# reading with a proportionally larger score.
VRP_SATURATION = 0.10


class VRPPremiumSellingSignal(SignalModule):
    name = "vrp_premium_selling"
    required_features = []

    def is_active_in_regime(self, macro: MacroEconomicDTO) -> bool:
        """Suppresses this module's contribution entirely when the
        macro-level half of the VRP gate is closed (VIX >= 30 or the regime
        is CREDIT EVENT). ``compute()``/``compute_vectorized()`` still run
        (their raw output remains available for introspection), they just
        never move ``final_score`` for this cycle -- see
        ``SignalAggregator.aggregate()``.
        """
        if macro is None:
            return True
        vix = getattr(macro, "vix", None)
        if vix is not None and not pd.isna(vix) and vix >= VIX_MAX_THRESHOLD:
            return False
        if getattr(macro, "market_regime", None) == "CREDIT EVENT":
            return False
        return True

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        true_ivr = df.get("True_IVR")
        true_ivr = (
            pd.to_numeric(true_ivr, errors="coerce")
            if true_ivr is not None
            else pd.Series(float("nan"), index=df.index)
        )
        vrp = df.get("VRP")
        vrp = (
            pd.to_numeric(vrp, errors="coerce")
            if vrp is not None
            else pd.Series(float("nan"), index=df.index)
        )

        has_data = true_ivr.notna() & vrp.notna()
        gate = has_data & (true_ivr > IVR_SELL_THRESHOLD) & (vrp > VRP_MIN_THRESHOLD)

        ivr_excess = ((true_ivr - IVR_SELL_THRESHOLD) / IVR_SELL_THRESHOLD).clip(lower=0.0, upper=1.0)
        vrp_excess = (vrp / VRP_SATURATION).clip(lower=0.0, upper=1.0)
        raw_score = (0.5 * ivr_excess + 0.5 * vrp_excess).clip(lower=0.0, upper=1.0)

        score = pd.Series(0.0, index=df.index)
        score[gate] = raw_score[gate]
        confidence = pd.Series(0.0, index=df.index)
        confidence[gate] = 1.0

        exps = pd.Series("", index=df.index)
        exps[~has_data] = "Cash/Wait: True_IVR/VRP not available this cycle"

        closed = has_data & ~gate
        closed_reasons = pd.Series("", index=df.index)
        closed_reasons[has_data & ~gate & (true_ivr <= IVR_SELL_THRESHOLD)] += "True_IVR<=50 "
        closed_reasons[has_data & ~gate & (vrp <= VRP_MIN_THRESHOLD)] += "VRP<=2% "
        exps[closed] = "Cash/Wait: VRP regime gate not met (" + closed_reasons[closed].str.strip() + ")"

        exps[gate] = (
            "+" + (raw_score[gate] * 100).round(1).astype(str)
            + "pts: VRP regime favors selling premium (True_IVR="
            + true_ivr[gate].round(1).astype(str) + ", VRP="
            + (vrp[gate] * 100).round(2).astype(str) + "%)"
        )

        return pd.DataFrame(
            {
                "score": score,
                "confidence": confidence,
                "explanation": exps,
                "meta_label_proba": 1.0,
            },
            index=df.index,
        )

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        true_ivr = row.get("True_IVR")
        vrp = row.get("VRP")

        has_data = (
            true_ivr is not None and not pd.isna(true_ivr)
            and vrp is not None and not pd.isna(vrp)
        )
        if not has_data:
            return SignalOutput(
                score=0.0, confidence=0.0,
                explanation="Cash/Wait: True_IVR/VRP not available this cycle",
            )

        gate = (true_ivr > IVR_SELL_THRESHOLD) and (vrp > VRP_MIN_THRESHOLD)
        if not gate:
            reasons = []
            if true_ivr <= IVR_SELL_THRESHOLD:
                reasons.append("True_IVR<=50")
            if vrp <= VRP_MIN_THRESHOLD:
                reasons.append("VRP<=2%")
            return SignalOutput(
                score=0.0, confidence=0.0,
                explanation=f"Cash/Wait: VRP regime gate not met ({' '.join(reasons)})",
            )

        ivr_excess = max(0.0, min(1.0, (true_ivr - IVR_SELL_THRESHOLD) / IVR_SELL_THRESHOLD))
        vrp_excess = max(0.0, min(1.0, vrp / VRP_SATURATION))
        score = max(0.0, min(1.0, 0.5 * ivr_excess + 0.5 * vrp_excess))
        explanation = (
            f"+{score * 100:.1f}pts: VRP regime favors selling premium "
            f"(True_IVR={true_ivr:.1f}, VRP={vrp * 100:.2f}%)"
        )
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(VRPPremiumSellingSignal())
