"""
InvestYo Quant Platform - Macro Regime Signal Module
===================================================
Phase 1 & 2: Handles top-down systemic risk regimes and defensive/leveraged sector rot.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class MacroRegimeSignal(SignalModule):
    name = "macro_regime"
    required_features = ["sector"]

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        if context.macro is None:
            return SignalOutput(score=0.0, confidence=0.0, explanation="")
            
        regime = context.macro.market_regime
        points = 0.0
        exps = []
        
        # Phase 1: Macro overrides
        if regime == "RECESSION":
            exps.append("-15pts: Recession Regime Active (Inverted Yield Curve)")
            exps.append("WARNING: Systemic recession warning.")
            points -= 15.0
        elif regime == "CREDIT EVENT":
            exps.append("-25pts: Hostile Credit Event (HY OAS Spreads Elevated)")
            exps.append("WARNING: High debt distress window.")
            points -= 25.0
        elif regime == "RISK ON":
            exps.append("+10pts: Favorable Macro Regime")
            points += 10.0

        # Systemic killSwitch check (triggers if Sahm >= 0.5 or VIX > 30)
        if hasattr(context.macro, "killSwitch") and context.macro.killSwitch:
            exps.append("-5pts: Systemic Risk Overlay Active (Sahm/VIX Breach) — localized penalty applied")
            exps.append("WARNING: SYSTEMIC KILLSWITCH ACTIVE: Fresh equity allocations halted.")
            points -= 5.0

        # Phase 2: Sector rotation
        sector = row.get("sector")
        if regime in ["RECESSION", "CREDIT EVENT"] and sector:
            if "Financial" in sector or "Real Estate" in sector:
                exps.append("-15pts: Macro headwind penalty on highly leveraged asset")
                points -= 15.0
            elif "Consumer Staples" in sector or "Healthcare" in sector:
                exps.append("+10pts: Defensive sector premium")
                points += 10.0
                
        # Normalization (Max absolute adjustment is 45.0)
        weight = 45.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        import numpy as np

        if context.macro is None:
            return pd.DataFrame({
                "score": 0.0,
                "confidence": 0.0,
                "explanation": "",
                "meta_label_proba": np.nan
            }, index=df.index)

        regime = context.macro.market_regime
        base_points = 0.0
        base_exps = []

        if regime == "RECESSION":
            base_exps.extend([
                "-15pts: Recession Regime Active (Inverted Yield Curve)",
                "WARNING: Systemic recession warning."
            ])
            base_points -= 15.0
        elif regime == "CREDIT EVENT":
            base_exps.extend([
                "-25pts: Hostile Credit Event (HY OAS Spreads Elevated)",
                "WARNING: High debt distress window."
            ])
            base_points -= 25.0
        elif regime == "RISK ON":
            base_exps.append("+10pts: Favorable Macro Regime")
            base_points += 10.0

        if hasattr(context.macro, "killSwitch") and context.macro.killSwitch:
            base_exps.extend([
                "-5pts: Systemic Risk Overlay Active (Sahm/VIX Breach) — localized penalty applied",
                "WARNING: SYSTEMIC KILLSWITCH ACTIVE: Fresh equity allocations halted."
            ])
            base_points -= 5.0

        base_exp_str = "\n".join(base_exps)

        if "sector" in df:
            sector = df["sector"].fillna("").astype(str)
        else:
            sector = pd.Series("", index=df.index)

        is_fin_re = sector.str.contains("Financial|Real Estate", na=False, regex=True)
        is_defensive = sector.str.contains("Consumer Staples|Healthcare", na=False, regex=True)

        scores = pd.Series(base_points, index=df.index, dtype=float)
        explanations = pd.Series(base_exp_str, index=df.index, dtype=object)

        if regime in ["RECESSION", "CREDIT EVENT"]:
            scores = np.where(is_fin_re, scores - 15.0, scores)
            scores = np.where(~is_fin_re & is_defensive, scores + 10.0, scores)
            
            explanations = np.where(is_fin_re, explanations + ("\n-15pts: Macro headwind penalty on highly leveraged asset" if base_exp_str else "-15pts: Macro headwind penalty on highly leveraged asset"),
                           np.where(is_defensive, explanations + ("\n+10pts: Defensive sector premium" if base_exp_str else "+10pts: Defensive sector premium"), explanations))

        weight = 45.0
        scores = scores / weight

        return pd.DataFrame({
            "score": scores,
            "confidence": 1.0,
            "explanation": explanations,
            "meta_label_proba": np.nan
        }, index=df.index)


# Auto-register module
global_registry.register(MacroRegimeSignal())
