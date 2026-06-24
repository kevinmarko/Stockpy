"""
InvestYo Quant Platform - Edge GARCH Signal Module
==================================================
Phase 4D: Scores mathematical edge ratio and GARCH tail-risk volatility.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class EdgeGarchSignal(SignalModule):
    name = "edge_garch"
    required_features = []

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        edge_ratio = row.get("edge_ratio")
        garch_vol = row.get("garch_vol")
        points = 0.0
        exps = []

        if edge_ratio is not None and not pd.isna(edge_ratio) and edge_ratio > 0.0:
            if edge_ratio >= 1.2:
                exps.append(f"+15pts: Strong Mathematical Edge ({edge_ratio:.2f})")
                points += 15.0
            elif edge_ratio < 0.8:
                exps.append(f"-15pts: Negative Mathematical Edge ({edge_ratio:.2f})")
                points -= 15.0

        if garch_vol is not None and not pd.isna(garch_vol):
            if garch_vol > 0.40:
                exps.append(f"-20pts: Extreme GARCH Volatility ({garch_vol*100:.1f}%) - High Tail Risk")
                points -= 20.0

        # Normalization (Max absolute adjustment is 35.0)
        weight = 35.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(EdgeGarchSignal())
