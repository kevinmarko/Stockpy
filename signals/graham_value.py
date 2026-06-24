"""
InvestYo Quant Platform - Graham Value Signal Module
===================================================
Phase 3: Graham Intrinsic Value relative valuation scoring.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class GrahamValueSignal(SignalModule):
    name = "graham_value"
    required_features = ["current_price"]

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        current_price = row["current_price"]
        graham_val = context.fundamentals.graham_number
        points = 0.0
        exps = []
        
        if graham_val > 0:
            if graham_val > current_price:
                exps.append(f"+15pts: Undervalued vs Graham (${graham_val:.2f})")
                exps.append("DETAIL: Value Anchor Met")
                points += 15.0
            else:
                exps.append(f"-10pts: Overvalued vs Graham (${graham_val:.2f})")
                points -= 10.0
        else:
            exps.append("-5pts: No Intrinsic Graham Value possible")
            points -= 5.0

        # Normalization (Max absolute adjustment is 15.0)
        weight = 15.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(GrahamValueSignal())
