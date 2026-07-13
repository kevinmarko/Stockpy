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

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        points = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)
        
        has_aroon_osc = False
        if "aroon_osc" in df.columns:
            aroon_osc = df["aroon_osc"]
            valid_osc = aroon_osc.notna()
            has_aroon_osc = True
            
            # Aroon Osc logic
            cond_chop = valid_osc & (aroon_osc.abs() < 50)
            cond_up = valid_osc & (aroon_osc >= 50)
            cond_down = valid_osc & (aroon_osc <= -50)
            
            points[cond_chop] = -15.0
            if cond_chop.any():
                exps[cond_chop] = "-15pts: Choppy Market via Aroon Oscillator (" + aroon_osc[cond_chop].round(1).astype(str) + ") - High False Positive Risk"
            
            points[cond_up] = 15.0
            if cond_up.any():
                exps[cond_up] = "+15pts: Strong Aroon Oscillator Uptrend (" + aroon_osc[cond_up].round(1).astype(str) + ")"
            
            points[cond_down] = -15.0
            if cond_down.any():
                exps[cond_down] = "-15pts: Strong Aroon Oscillator Downtrend (" + aroon_osc[cond_down].round(1).astype(str) + ")"

        # Fallback to trend_strength (Aroon Up) where aroon_osc is absent/NaN
        if "trend_strength" in df.columns:
            trend_str = df["trend_strength"]
            if has_aroon_osc:
                fallback_mask = ~valid_osc
            else:
                fallback_mask = pd.Series(True, index=df.index)
                
            cond_up_fb = fallback_mask & (trend_str >= 50.0)
            cond_weak_fb = fallback_mask & (trend_str >= 30.0) & (trend_str < 50.0)
            cond_bear_fb = fallback_mask & (trend_str < 30.0)
            
            points[cond_up_fb] = 10.0
            exps[cond_up_fb] = "+10pts: Bullish technical trend (Aroon >= 50)"
            
            points[cond_weak_fb] = -5.0
            exps[cond_weak_fb] = "-5pts: Weakening trend momentum"
            
            points[cond_bear_fb] = -15.0
            exps[cond_bear_fb] = "-15pts: Bearish pricing structure"
            
        score = points / 15.0
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

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
