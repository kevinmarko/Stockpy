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

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        sortino = df.get("sortino_ratio", pd.Series(0.0, index=df.index))
        drawdown = df.get("max_drawdown", pd.Series(0.0, index=df.index))
        
        score = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)
        
        valid_s = sortino.notna()
        high_s = valid_s & (sortino > 2.0)
        score[high_s] += 10.0
        exps[high_s] = "+10pts: High Sortino (" + sortino[high_s].round(2).astype(str) + ")"
        
        valid_d = drawdown.notna()
        steep_d = valid_d & (drawdown < -0.25)
        score[steep_d] -= 10.0
        
        dd_msg = "-10pts: Steep Drawdown (" + (drawdown[steep_d] * 100).round(1).astype(str) + "%)"
        has_exp = exps != ""
        exps[steep_d & has_exp] += "\n" + dd_msg
        exps[steep_d & ~has_exp] = dd_msg
        
        score /= 10.0
        
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

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
