"""
InvestYo Quant Platform - Forecast Alignment Signal Module
=========================================================
Phase 4: Computes projected gain/loss against calibrated forecast horizons.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class ForecastAlignmentSignal(SignalModule):
    name = "forecast_alignment"
    required_features = ["current_price", "forecast_price"]

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        current_price = row["current_price"]
        forecast_price = row["forecast_price"]
        points = 0.0
        exps = []

        if forecast_price > current_price:
            expected_gain = ((forecast_price - current_price) / current_price) * 100
            if expected_gain >= 1.5:
                exps.append(f"+10pts: Strong forecast projection (+{expected_gain:.1f}%)")
                points += 10.0
            elif expected_gain > 0:
                exps.append(f"+5pts: Moderate positive forecast (+{expected_gain:.1f}%)")
                points += 5.0
        else:
            exps.append("-10pts: Forecast suggests structural price erosion")
            points -= 10.0

        # Normalization (Max absolute adjustment is 10.0)
        weight = 10.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(ForecastAlignmentSignal())
