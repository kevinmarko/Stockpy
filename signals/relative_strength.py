"""
InvestYo Quant Platform - Relative Strength Signal Module
=========================================================
Phase 4B: Scores relative strength/underperformance compared to the S&P 500.
"""

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


class RelativeStrengthSignal(SignalModule):
    name = "relative_strength"
    required_features = []

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        rs = df.get("relative_strength", pd.Series(0.0, index=df.index))
        
        score = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)
        
        valid = rs.notna()
        
        up = valid & (rs > 0)
        score[up] = 10.0 / 10.0
        exps[up] = "+10pts: Outperforming S&P 500 (RS: " + rs[up].round(2).astype(str) + ")"
        
        down = valid & (rs <= 0)
        score[down] = -10.0 / 10.0
        exps[down] = "-10pts: Underperforming S&P 500 (RS: " + rs[down].round(2).astype(str) + ")"
        
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        relative_strength = row.get("relative_strength")
        points = 0.0
        exps = []

        if relative_strength is not None and not pd.isna(relative_strength):
            if relative_strength > 0:
                exps.append(f"+10pts: Outperforming S&P 500 (RS: {relative_strength:.2f})")
                points += 10.0
            else:
                exps.append(f"-10pts: Underperforming S&P 500 (RS: {relative_strength:.2f})")
                points -= 10.0

        # Normalization (Max absolute adjustment is 10.0)
        weight = 10.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=1.0, explanation=explanation)


# Auto-register module
global_registry.register(RelativeStrengthSignal())
