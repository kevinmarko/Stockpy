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
