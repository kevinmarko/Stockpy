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

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        edge_ratio = df.get("edge_ratio", pd.Series(0.0, index=df.index))
        garch_vol = df.get("garch_vol", pd.Series(0.0, index=df.index))
        
        score = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)
        
        valid_edge = edge_ratio.notna() & (edge_ratio > 0.0)
        strong = valid_edge & (edge_ratio >= 1.2)
        score[strong] += 15.0
        exps[strong] = "+15pts: Strong Mathematical Edge (" + edge_ratio[strong].round(2).astype(str) + ")"
        
        weak = valid_edge & (edge_ratio < 0.8)
        score[weak] -= 15.0
        exps[weak] = "-15pts: Negative Mathematical Edge (" + edge_ratio[weak].round(2).astype(str) + ")"
        
        valid_garch = garch_vol.notna()
        high_vol = valid_garch & (garch_vol > 0.40)
        score[high_vol] -= 20.0
        
        garch_msg = "-20pts: Extreme GARCH Volatility (" + (garch_vol[high_vol] * 100).round(1).astype(str) + "%) - High Tail Risk"
        has_exp = exps != ""
        exps[high_vol & has_exp] += "\n" + garch_msg
        exps[high_vol & ~has_exp] = garch_msg
        
        score /= 35.0
        
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

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
