"""
InvestYo Quant Platform - Moskowitz/Ooi/Pedersen Time-Series Momentum Signal Module
==================================================================================
Reference: Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum,"
Journal of Financial Economics 104:228-250.
"""

import math
import numpy as np
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry
from settings import settings


class TimeSeriesMomentumSignal(SignalModule):
    name = "timeseries_momentum"
    required_features = ["ROC_12M", "GARCH_Vol"]

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        roc_12m = df["ROC_12M"]
        garch_vol = df["GARCH_Vol"]
        
        rf = settings.RISK_FREE_RATE
        target_vol = 0.10
        
        valid = roc_12m.notna() & garch_vol.notna() & (garch_vol > 0)
        
        score = pd.Series(0.0, index=df.index)
        confidence = pd.Series(0.0, index=df.index)
        exps = pd.Series("WARNING: Missing ROC_12M or GARCH_Vol. Time-Series Momentum score set to 0.0.", index=df.index)
        
        if valid.any():
            diff = roc_12m[valid] - rf
            sign_val = np.sign(diff)
            
            vol_scalar = np.minimum(1.0, target_vol / garch_vol[valid])
            strength_factor = np.tanh(roc_12m[valid].abs() * 3)
            
            sub_score = sign_val * vol_scalar * strength_factor
            score[valid] = sub_score
            confidence[valid] = vol_scalar
            
            weight = settings.SIGNAL_WEIGHTS.get(self.name, 15.0)
            contrib = sub_score * weight
            
            contrib_pos = contrib >= 0
            
            exp_pos = "+" + contrib.round(1).astype(str) + "pts: Time-Series Momentum Bullish (ROC_12M=" + (roc_12m[valid] * 100).round(1).astype(str) + "%, GARCH_Vol=" + (garch_vol[valid] * 100).round(1).astype(str) + "%, Vol_Scalar=" + vol_scalar.round(2).astype(str) + ")"
            exp_neg = "-" + contrib.abs().round(1).astype(str) + "pts: Time-Series Momentum Bearish (ROC_12M=" + (roc_12m[valid] * 100).round(1).astype(str) + "%, GARCH_Vol=" + (garch_vol[valid] * 100).round(1).astype(str) + "%, Vol_Scalar=" + vol_scalar.round(2).astype(str) + ")"
            
            exp_sub = pd.Series("", index=valid[valid].index)
            exp_sub[contrib_pos] = exp_pos[contrib_pos]
            exp_sub[~contrib_pos] = exp_neg[~contrib_pos]
            exps[valid] = exp_sub
            
        return pd.DataFrame({
            "score": score,
            "confidence": confidence,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        roc_12m = row["ROC_12M"]
        garch_vol = row["GARCH_Vol"]
        
        # Risk-free rate adjustment
        rf = settings.RISK_FREE_RATE
        target_vol = 0.10  # 10% target annualized exposure
        
        # Check for valid inputs
        if pd.isna(roc_12m) or pd.isna(garch_vol) or garch_vol <= 0:
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation="WARNING: Missing ROC_12M or GARCH_Vol. Time-Series Momentum score set to 0.0."
            )
            
        diff = roc_12m - rf
        sign_val = 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)
        
        # Volatility-scale: scale by inverse of ex-ante vol (cap at 1.0)
        vol_scalar = min(1.0, target_vol / garch_vol)
        
        # Momentum strength factor: tanh of absolute 12m return scaled by 3
        strength_factor = math.tanh(abs(roc_12m) * 3)
        
        # Compute final score in [-1.0, 1.0]
        score = sign_val * vol_scalar * strength_factor
        
        weight = settings.SIGNAL_WEIGHTS.get(self.name, 15.0)
        contrib = score * weight
        
        if contrib >= 0:
            explanation = f"+{contrib:.1f}pts: Time-Series Momentum Bullish (ROC_12M={roc_12m*100:.1f}%, GARCH_Vol={garch_vol*100:.1f}%, Vol_Scalar={vol_scalar:.2f})"
        else:
            explanation = f"-{abs(contrib):.1f}pts: Time-Series Momentum Bearish (ROC_12M={roc_12m*100:.1f}%, GARCH_Vol={garch_vol*100:.1f}%, Vol_Scalar={vol_scalar:.2f})"
            
        return SignalOutput(
            score=score,
            confidence=vol_scalar,  # Use vol scaling as a proxy for confidence/sizing reliability
            explanation=explanation
        )


# Auto-register module
global_registry.register(TimeSeriesMomentumSignal())
