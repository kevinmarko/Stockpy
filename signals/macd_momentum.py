"""
InvestYo Quant Platform - MACD Momentum Signal Module
====================================================
Phase 4: Handles MACD Bullish/Bearish crossover scoring (when Aroon Oscillator is present).
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class MACDMomentumSignal(SignalModule):
    name = "macd_momentum"
    required_features = ["macd_line", "macd_signal"]

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        if "aroon_osc" in df.columns:
            has_aroon = df["aroon_osc"].notna()
        else:
            has_aroon = pd.Series(False, index=df.index)
            
        is_bullish = df["macd_line"] > df["macd_signal"]
        
        points = pd.Series(0.0, index=df.index)
        points[has_aroon & is_bullish] = 10.0
        points[has_aroon & ~is_bullish] = -15.0
        
        exps = pd.Series("", index=df.index)
        exps[has_aroon & is_bullish] = "+10pts: MACD Bullish"
        exps[has_aroon & ~is_bullish] = "-15pts: MACD Bearish Crossover"
        
        score = points / 15.0
        
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        aroon_osc = row.get("aroon_osc")
        points = 0.0
        exps = []
        
        # MACD is only scored in the presence of Aroon Oscillator (modern trend regime)
        if aroon_osc is not None and not pd.isna(aroon_osc):
            macd_line = row["macd_line"]
            macd_signal = row["macd_signal"]
            
            if macd_line > macd_signal:
                exps.append("+10pts: MACD Bullish")
                points += 10.0
            else:
                exps.append("-15pts: MACD Bearish Crossover")
                points -= 15.0

        # Normalization (Max absolute adjustment is 15.0)
        weight = 15.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(MACDMomentumSignal())
