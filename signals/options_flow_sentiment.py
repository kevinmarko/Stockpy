"""signals/options_flow_sentiment.py — Options Order Flow Sentiment Signal
=============================================================================

Phase 11 / Workstream 4: Institutional Options Order Flow Sentiment & Alpha Overlay.

Quantitative signal module scoring directional options flow sentiment based on
institutional sweeps and blocks (Unusual Options Activity).

Scoring Logic:
--------------
- Bullish Notional: Aggressive Call Ask Sweeps + Put Bid Sweeps
- Bearish Notional: Aggressive Put Ask Sweeps + Call Bid Sweeps
- Net Sentiment: (Bullish Notional - Bearish Notional) / Total Notional in [-1.0, 1.0]

Signal Output:
--------------
- score: Net Sentiment in [-1.0, 1.0]
- confidence: 0.85 when active institutional flow is detected; 0.5 when neutral; 0.0 when missing.
- explanation: Detailed institutional flow breakdown.

Honest Degradation (CONSTRAINT #4 / #6):
----------------------------------------
- When no UOA data exists for a symbol, returns neutral score=0.0, confidence=0.0,
  with an honest "Options flow sentiment: neutral/no flow data this cycle" message.
- Vectorized and scalar paths are guaranteed identical in output.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from dto_models import MacroEconomicDTO
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry

logger = logging.getLogger(__name__)

__all__ = ["OptionsFlowSentimentSignal"]


class OptionsFlowSentimentSignal(SignalModule):
    """Signal module scoring institutional unusual options order flow sentiment."""

    name: str = "options_flow_sentiment"
    required_features: List[str] = []
    meta_label_features: List[str] = []

    def __init__(self) -> None:
        self._sentiment_scores: Dict[str, float] = {}

    def pre_compute(self, universe_df: pd.DataFrame, context: SignalContext) -> None:
        """Load or synchronize options flow sentiment for the current universe."""
        self._sentiment_scores.clear()

        # 1. Inherit any sentiment scores already populated in context
        if context is not None and hasattr(context, "options_flow_sentiment") and context.options_flow_sentiment:
            self._sentiment_scores.update(
                {str(k).upper(): float(v) for k, v in context.options_flow_sentiment.items() if v is not None}
            )

        # 2. If empty, attempt to load persisted UOA flow records
        if not self._sentiment_scores:
            try:
                from pilots.unusual_options_flow import calculate_net_flow_sentiment, load_uoa_records

                records = load_uoa_records()
                if records:
                    symbols = []
                    if universe_df is not None and "Symbol" in universe_df.columns:
                        symbols = [str(s).upper() for s in universe_df["Symbol"].dropna().unique()]

                    # Calculate per symbol
                    for sym in symbols:
                        res = calculate_net_flow_sentiment(sym, records)
                        if res and res.get("total_records", 0) > 0:
                            self._sentiment_scores[sym] = float(res.get("net_sentiment", 0.0))

            except Exception as exc:
                logger.debug("OptionsFlowSentimentSignal.pre_compute error: %s", exc)

        # 3. Synchronize back into context
        if context is not None and hasattr(context, "options_flow_sentiment"):
            context.options_flow_sentiment = dict(self._sentiment_scores)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        """Vectorized execution over universe DataFrame."""
        n = len(df)
        if n == 0:
            return pd.DataFrame(
                columns=["score", "confidence", "explanation", "meta_label_proba"],
                index=df.index,
            )

        # Identify symbol column
        symbols = None
        for col in ("Symbol", "Ticker", "symbol", "ticker"):
            if col in df.columns:
                symbols = df[col].astype(str).str.upper().str.strip()
                break

        # Check for direct column input
        raw_sentiment = None
        for col in ("Options_Flow_Sentiment", "options_flow_sentiment", "Flow_Sentiment", "options_sentiment"):
            if col in df.columns:
                raw_sentiment = pd.to_numeric(df[col], errors="coerce")
                break

        # Merge from context or internal cache if column not present or contains NaNs
        scores = pd.Series(float("nan"), index=df.index)
        if raw_sentiment is not None:
            scores = raw_sentiment.copy()

        if symbols is not None:
            context_scores = {}
            if context is not None and hasattr(context, "options_flow_sentiment") and context.options_flow_sentiment:
                context_scores = context.options_flow_sentiment
            elif self._sentiment_scores:
                context_scores = self._sentiment_scores

            if context_scores:
                missing_mask = scores.isna()
                if missing_mask.any():
                    mapped = symbols[missing_mask].map(context_scores)
                    scores[missing_mask] = pd.to_numeric(mapped, errors="coerce")

        has_data = scores.notna()
        clamped_score = scores.clip(lower=-1.0, upper=1.0).fillna(0.0)

        confidence = pd.Series(0.0, index=df.index)
        confidence[has_data] = clamped_score[has_data].abs().apply(lambda x: 0.85 if x > 0.15 else (0.75 if x > 0.0 else 0.5))

        explanations = pd.Series("", index=df.index)
        explanations[~has_data] = "Options flow sentiment: neutral/no flow data this cycle"

        # Categorize explanations
        bullish_mask = has_data & (clamped_score > 0.15)
        bearish_mask = has_data & (clamped_score < -0.15)
        neutral_mask = has_data & ~bullish_mask & ~bearish_mask

        explanations[bullish_mask] = clamped_score[bullish_mask].apply(
            lambda s: f"Options flow sentiment: bullish (+{s:.2f}) [institutional call sweep/bid-put flow]"
        )
        explanations[bearish_mask] = clamped_score[bearish_mask].apply(
            lambda s: f"Options flow sentiment: bearish ({s:.2f}) [institutional put sweep/bid-call flow]"
        )
        explanations[neutral_mask] = clamped_score[neutral_mask].apply(
            lambda s: f"Options flow sentiment: neutral ({s:.2f}) [balanced order flow]"
        )

        return pd.DataFrame(
            {
                "score": clamped_score,
                "confidence": confidence,
                "explanation": explanations,
                "meta_label_proba": pd.Series(1.0, index=df.index),
            },
            index=df.index,
        )

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Scalar execution for a single asset row."""
        symbol = ""
        for col in ("Symbol", "Ticker", "symbol", "ticker"):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                symbol = str(val).upper().strip()
                break

        # Check row values
        raw_val = None
        for col in ("Options_Flow_Sentiment", "options_flow_sentiment", "Flow_Sentiment", "options_sentiment"):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                try:
                    raw_val = float(val)
                    break
                except (ValueError, TypeError):
                    continue

        # Check context
        if raw_val is None and symbol:
            if context is not None and hasattr(context, "options_flow_sentiment") and context.options_flow_sentiment:
                raw_val = context.options_flow_sentiment.get(symbol)
            elif symbol in self._sentiment_scores:
                raw_val = self._sentiment_scores.get(symbol)

        if raw_val is None or (isinstance(raw_val, float) and math.isnan(raw_val)):
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation="Options flow sentiment: neutral/no flow data this cycle",
                meta_label_proba=1.0,
            )

        score = max(-1.0, min(1.0, float(raw_val)))
        if score > 0.15:
            confidence = 0.85
            explanation = f"Options flow sentiment: bullish (+{score:.2f}) [institutional call sweep/bid-put flow]"
        elif score < -0.15:
            confidence = 0.85
            explanation = f"Options flow sentiment: bearish ({score:.2f}) [institutional put sweep/bid-call flow]"
        else:
            confidence = 0.75 if abs(score) > 0.0 else 0.5
            explanation = f"Options flow sentiment: neutral ({score:.2f}) [balanced order flow]"

        return SignalOutput(
            score=score,
            confidence=confidence,
            explanation=explanation,
            meta_label_proba=1.0,
        )


# Auto-register with global signal registry
global_registry.register(OptionsFlowSentimentSignal())
