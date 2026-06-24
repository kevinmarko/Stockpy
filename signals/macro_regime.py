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


# Auto-register module
global_registry.register(MacroRegimeSignal())
