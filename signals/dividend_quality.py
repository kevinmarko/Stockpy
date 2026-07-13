"""
InvestYo Quant Platform - Dividend Quality Signal Module
======================================================
Phase 3: Handles dividend yield checks and sustainability warning gates.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class DividendQualitySignal(SignalModule):
    name = "dividend_quality"
    required_features = []

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        div_yield = df.get("dividend_yield", pd.Series(0.0, index=df.index))
        is_sustainable = df.get("is_dividend_sustainable", pd.Series(False, index=df.index))
        
        score = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)
        
        has_div = div_yield > 0
        
        # Sustainable
        sustainable = has_div & is_sustainable
        score[sustainable] = 10.0 / 25.0
        exps[sustainable] = "+10pts: Sustainable Dividend"
        
        # Yield Trap
        trap = has_div & ~is_sustainable
        score[trap] = -25.0 / 25.0
        exps[trap] = "-25pts: Yield Trap Warning (Payout > 100%)\nWARNING: Dividend Sustainability Failure"
        
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        fundamentals = context.fundamentals
        points = 0.0
        exps = []
        
        if fundamentals.dividend_yield > 0:
            if fundamentals.is_dividend_sustainable:
                exps.append("+10pts: Sustainable Dividend")
                points += 10.0
            else:
                exps.append("-25pts: Yield Trap Warning (Payout > 100%)")
                exps.append("WARNING: Dividend Sustainability Failure")
                points -= 25.0

        # Normalization (Max absolute adjustment is 25.0)
        weight = 25.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(DividendQualitySignal())
