"""
InvestYo Quant Platform - Forecast Alignment Signal Module
=========================================================
Phase 4: Computes projected gain/loss against calibrated forecast horizons.

Forecasting rebuild F2 (2026-09): a MISSING or FALLBACK forecast scores 0
(neutral), not -10 (bearish). Missing = ``forecast_price`` NaN / None /
<= 0 (the vectorized path fills a missing forecast with 0.0). Fallback =
the optional ``forecast_is_fallback`` feature is True, meaning every
forecasting model failed (or was dropped by the F2 guards) and the "forecast"
is just today's price, or the whole engine failed and the row carries a
coarse Monte Carlo recovery. Neither is evidence of price erosion. The
feature is optional: a caller that does not supply it gets the missing-value
rule only.
"""

import math

import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry


def _is_true_flag(value) -> bool:
    """True only for a real True flag (bool/np.bool_ or a numeric 1.0).
    NaN / None / missing mean "unknown", which is not a fallback."""
    if value is None:
        return False
    try:
        if isinstance(value, float) and math.isnan(value):
            return False
        return bool(value)
    except (TypeError, ValueError):
        return False


class ForecastAlignmentSignal(SignalModule):
    name = "forecast_alignment"
    required_features = ["current_price", "forecast_price"]

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        current_price = df.get("current_price", pd.Series(0.0, index=df.index))
        forecast_price = df.get("forecast_price", pd.Series(0.0, index=df.index))
        if "forecast_is_fallback" in df:
            is_fallback = df["forecast_is_fallback"].map(_is_true_flag).astype(bool)
        else:
            is_fallback = pd.Series(False, index=df.index)

        score = pd.Series(0.0, index=df.index)
        exps = pd.Series("", index=df.index)

        # F2: a missing (NaN / <= 0) or fallback forecast is neutral (0).
        valid = (
            current_price.notna() & forecast_price.notna() & (current_price > 0)
            & (forecast_price > 0) & ~is_fallback
        )

        up = valid & (forecast_price > current_price)
        expected_gain = ((forecast_price[up] - current_price[up]) / current_price[up]) * 100

        strong = expected_gain >= 1.5
        # Index through expected_gain (already sliced to the up-subset), not
        # up.index (the full-length index) — up.index[strong] mismatches
        # lengths and raises IndexError whenever the universe has any
        # down-tickers at all (bug only manifests with a genuine up/down mix).
        score[expected_gain.index[strong]] = 10.0
        exps[expected_gain.index[strong]] = "+10pts: Strong forecast projection (+" + expected_gain[strong].round(1).astype(str) + "%)"

        mod = (expected_gain > 0) & ~strong
        score[expected_gain.index[mod]] = 5.0
        exps[expected_gain.index[mod]] = "+5pts: Moderate positive forecast (+" + expected_gain[mod].round(1).astype(str) + "%)"

        down = valid & (forecast_price <= current_price)
        score[down] = -10.0
        exps[down] = "-10pts: Forecast suggests structural price erosion"

        neutral = ~valid & current_price.notna() & (current_price > 0)
        exps[neutral] = "0pts: No usable forecast (missing or fallback); neutral"

        score /= 10.0

        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exps,
            "meta_label_proba": 1.0
        }, index=df.index)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        current_price = row["current_price"]
        forecast_price = row["forecast_price"]
        is_fallback = _is_true_flag(row.get("forecast_is_fallback"))
        points = 0.0
        exps = []

        if pd.isna(current_price) or current_price == 0:
            pass
        elif pd.isna(forecast_price) or forecast_price <= 0 or is_fallback:
            # F2: missing or fallback forecast -> neutral, not bearish.
            exps.append("0pts: No usable forecast (missing or fallback); neutral")
        elif forecast_price > current_price:
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
