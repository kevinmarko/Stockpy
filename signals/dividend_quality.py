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
