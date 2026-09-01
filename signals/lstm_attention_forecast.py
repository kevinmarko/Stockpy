"""
InvestYo Quant Platform - LSTM Attention Forecast Signal
========================================================
Phase 5: Signal module for the LSTM-Attention Google Trends forecaster.
"""

import pandas as pd
import numpy as np
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry

class LstmAttentionForecastSignal(SignalModule):
    name = "lstm_attention_forecast"
    required_features = ["Google_Trends_LSTM_Forecast"]

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        forecast = df.get("Google_Trends_LSTM_Forecast", pd.Series(float('nan'), index=df.index))
        
        score = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)
        
        valid = forecast.notna()
        expected_gain = forecast[valid] * 100.0  # Convert decimal return to percentage
        
        strong = expected_gain >= 1.0
        score[expected_gain.index[strong]] = 10.0
        exps[expected_gain.index[strong]] = "+10pts: Strong ASVI LSTM projection (+" + expected_gain[strong].round(2).astype(str) + "%)"
        
        mod = (expected_gain > 0) & ~strong
        score[expected_gain.index[mod]] = 5.0
        exps[expected_gain.index[mod]] = "+5pts: Positive ASVI LSTM projection (+" + expected_gain[mod].round(2).astype(str) + "%)"
        
        down = expected_gain <= 0
        score[expected_gain.index[down]] = -10.0
        exps[expected_gain.index[down]] = "-10pts: ASVI LSTM indicates structural price erosion"
        
        # Missing data explanation
        missing = ~valid
        exps[missing] = "WARNING: Insufficient ASVI/LSTM history"
        score[missing] = 0.0
        
        score /= 10.0
        
        return pd.DataFrame({
            "score": score,
            "confidence": np.where(valid, 1.0, 0.0),
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        forecast = row.get("Google_Trends_LSTM_Forecast", float('nan'))
        points = 0.0
        exps = []
        conf = 1.0

        if pd.isna(forecast):
            exps.append("WARNING: Insufficient ASVI/LSTM history")
            conf = 0.0
        else:
            expected_gain = forecast * 100.0
            if expected_gain >= 1.0:
                exps.append(f"+10pts: Strong ASVI LSTM projection (+{expected_gain:.2f}%)")
                points += 10.0
            elif expected_gain > 0:
                exps.append(f"+5pts: Positive ASVI LSTM projection (+{expected_gain:.2f}%)")
                points += 5.0
            else:
                exps.append("-10pts: ASVI LSTM indicates structural price erosion")
                points -= 10.0

        weight = 10.0
        score = points / weight
        explanation = "\n".join(exps)
        
        return SignalOutput(score=score, confidence=conf, explanation=explanation)

global_registry.register(LstmAttentionForecastSignal())
