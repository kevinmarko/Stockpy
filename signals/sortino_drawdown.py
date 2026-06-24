"""
InvestYo Quant Platform - Sortino Drawdown Signal Module
========================================================
Phase 4C: Handles Sortino ratio reward and drawdown penalty scoring.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class SortinoDrawdownSignal(SignalModule):
    name = "sortino_drawdown"
    required_features = []

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        sortino_ratio = row.get("sortino_ratio")
        max_drawdown = row.get("max_drawdown")
        points = 0.0
        exps = []

        if sortino_ratio is not None and not pd.isna(sortino_ratio):
            if sortino_ratio > 2.0:
                exps.append(f"+10pts: High Sortino ({sortino_ratio:.2f})")
                points += 10.0

        if max_drawdown is not None and not pd.isna(max_drawdown):
            if max_drawdown < -0.25:
                exps.append(f"-10pts: Steep Drawdown ({max_drawdown*100:.1f}%)")
                points -= 10.0

        # Normalization (Max absolute adjustment is 10.0)
        weight = 10.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(SortinoDrawdownSignal())
