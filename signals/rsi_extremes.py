"""
InvestYo Quant Platform - RSI Extremes Signal Module
====================================================
Phase 4B: Scores overbought (>70) and oversold (<30) RSI levels.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class RSIExtremesSignal(SignalModule):
    name = "rsi_extremes"
    required_features = []

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        rsi = row.get("rsi")
        points = 0.0
        exps = []

        if rsi is not None and not pd.isna(rsi):
            if rsi < 30:
                exps.append("+20pts: RSI < 30 (Mean Reversion)")
                points += 20.0
            elif rsi > 70:
                exps.append("-20pts: RSI > 70 (Overbought)")
                points -= 20.0

        # Normalization (Max absolute adjustment is 20.0)
        weight = 20.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(RSIExtremesSignal())
