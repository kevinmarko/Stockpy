"""
InvestYo Quant Platform - Aroon Trend Signal Module
==================================================
Phase 4: Handles Aroon Oscillator trend strength and chop filtering,
falling back to legacy Trend Strength (Aroon Up) when the oscillator is absent.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class AroonTrendSignal(SignalModule):
    name = "aroon_trend"
    required_features = ["trend_strength"]

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        aroon_osc = row.get("aroon_osc")
        trend_strength = row["trend_strength"]
        points = 0.0
        exps = []

        if aroon_osc is not None and not pd.isna(aroon_osc):
            if abs(aroon_osc) < 50:
                exps.append(
                    f"-15pts: Choppy Market via Aroon Oscillator ({aroon_osc:.1f}) - High False Positive Risk"
                )
                points -= 15.0
            elif aroon_osc >= 50:
                exps.append(f"+15pts: Strong Aroon Oscillator Uptrend ({aroon_osc:.1f})")
                points += 15.0
            else:  # aroon_osc <= -50
                exps.append(f"-15pts: Strong Aroon Oscillator Downtrend ({aroon_osc:.1f})")
                points -= 15.0
        else:
            # Fallback to legacy Trend Strength
            if trend_strength >= 50.0:
                exps.append("+10pts: Bullish technical trend (Aroon >= 50)")
                points += 10.0
            elif 30.0 <= trend_strength < 50.0:
                exps.append("-5pts: Weakening trend momentum")
                points -= 5.0
            else:
                exps.append("-15pts: Bearish pricing structure")
                points -= 15.0

        # Normalization (Max absolute adjustment is 15.0)
        weight = 15.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(AroonTrendSignal())
