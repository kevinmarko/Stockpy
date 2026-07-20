"""
Sentiment Risk Engine
Computes social media sentiment dynamics and their impact on market volatility,
specifically measuring the asymmetric leverage effect, sentiment intensity,
credibility filtering, and volatility persistence.

Standalone, on-demand, per-symbol feature — NOT part of the shared calculation
pipeline (``data_engine`` -> ``processing_engine`` -> ``strategy_engine``).  It
never touches ``signals/`` or ``StrategyEngine``, so its result shape lives
here as a local dataclass (:class:`SentimentResult`) rather than in
``dto_models.py`` (which is reserved for data crossing into the shared
calculation pipeline).

Honesty contract (CONSTRAINT #4): every field that cannot be genuinely
computed or fetched is ``None`` — never a fabricated plausible-looking
number.  ``compute_asymmetric_volatility`` returns ``(None, None, None)`` on
insufficient data or a fit failure instead of the old hardcoded
``(0.0, 0.95, 0.15)`` defaults.  ``get_live_sentiment`` no longer silently
substitutes ``generate_mock_sentiment``'s synthetic numbers when the
Antigravity agent is unavailable; it returns an explicit
``source="unavailable"`` result instead so every consumer (API, webapp,
Streamlit) can render the distinction rather than guess.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Tuple

import numpy as np
import pandas as pd
import logging

from arch import arch_model

logger = logging.getLogger("SentimentRiskEngine")


@dataclass
class SentimentResult:
    """Result of a sentiment-dynamics lookup for one ticker on one date.

    ``source`` tells callers whether the sentiment fields (``sentiment_score``,
    ``sentiment_intensity``, ``credibility_score``) are real Antigravity-agent
    output (``"antigravity_agent"``), an honest unavailable degradation
    (``"unavailable"`` — all three ``None``), or synthetic demo data produced
    by :meth:`SentimentRiskEngine.generate_mock_sentiment` (``"mock"`` — an
    explicit test/demo utility, never wired into the live API/GUI path, so
    this value never reaches a real consumer).

    ``volatility_persistence`` is computed independently from ``returns`` via
    :meth:`SentimentRiskEngine.compute_asymmetric_volatility` (a real
    GJR-GARCH fit) and is populated or ``None`` purely based on data
    sufficiency / fit success — it does not depend on the agent's own
    availability, since it is not derived from the agent at all.
    """

    ticker: str
    date: datetime
    sentiment_score: Optional[float]
    sentiment_intensity: Optional[float]
    credibility_score: Optional[float]
    volatility_persistence: Optional[float]
    source: Literal["antigravity_agent", "unavailable", "mock"]


class SentimentRiskEngine:
    def __init__(self):
        pass

    def compute_asymmetric_volatility(
        self, returns: pd.Series
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Fits a GJR-GARCH(1,1,1) model to compute the asymmetric leverage effect
        and volatility persistence.
        Returns:
            gamma (float or None): Asymmetry coefficient (positive means negative
                shocks increase vol more).
            persistence (float or None): alpha + beta + (gamma / 2), measure of
                shock persistence.
            current_vol (float or None): Annualized conditional volatility of the
                last observation.

            All three are ``None`` (never a fabricated default) when there is
            insufficient data (< 100 observations) or the GARCH fit raises
            (CONSTRAINT #4).
        """
        if len(returns) < 100:
            logger.warning(
                "Insufficient data for GJR-GARCH model (%d obs, need >= 100). "
                "Returning honest unavailable (None, None, None).",
                len(returns),
            )
            return None, None, None

        try:
            # Drop NaN and scale for optimizer
            scaled_ret = returns.dropna() * 100.0

            # GJR-GARCH uses vol='GARCH', p=1, o=1, q=1
            am = arch_model(scaled_ret, vol='GARCH', p=1, o=1, q=1, dist='Normal')
            res = am.fit(update_freq=0, disp='off')

            # Extract parameters
            params = res.params
            alpha = params.get('alpha[1]', 0.0)
            beta = params.get('beta[1]', 0.9)
            gamma = params.get('gamma[1]', 0.0)

            # For GJR-GARCH, persistence is alpha + beta + (gamma / 2)
            persistence = alpha + beta + (gamma / 2.0)

            # Annualize the latest conditional volatility
            last_vol = res.conditional_volatility.iloc[-1] / 100.0  # unscale
            annualized_vol = last_vol * np.sqrt(252)

            return float(gamma), float(persistence), float(annualized_vol)
        except Exception as e:
            logger.warning(
                "GJR-GARCH model failed: %s. Returning honest unavailable (None, None, None).",
                e,
            )
            return None, None, None

    def generate_mock_sentiment(
        self, ticker: str, date: datetime, returns: pd.Series
    ) -> SentimentResult:
        """
        Generates synthetic sentiment data for UI demonstration / test purposes,
        inversely correlated with recent market returns to simulate the leverage
        effect.

        Explicit test/demo utility only — NOT wired into the live API/GUI path
        (see :meth:`get_live_sentiment`).  ``source="mock"`` marks the numeric
        fields as synthetic so this can never be confused with a real
        Antigravity-agent result or an honest "unavailable" state.
        """
        # Default mock values
        score = 0.0
        intensity = 0.5
        credibility = 0.8

        if len(returns) >= 5:
            # Inverse relationship: bad recent returns -> negative sentiment
            recent_return = returns.iloc[-5:].sum()
            score = float(np.clip(recent_return * 10.0, -1.0, 1.0))

            # High absolute returns (shocks) -> high intensity
            intensity = float(np.clip(abs(recent_return) * 20.0, 0.1, 1.0))

            # Random credibility for demonstration, but lower during extreme panics
            if recent_return < -0.05:
                credibility = 0.4  # Rumor mill runs wild during crashes
            else:
                credibility = 0.9

        # Compute real asymmetry and persistence if enough data (honest None otherwise)
        _gamma, persistence, _vol = self.compute_asymmetric_volatility(returns)

        return SentimentResult(
            ticker=ticker,
            date=date,
            sentiment_score=score,
            sentiment_intensity=intensity,
            credibility_score=credibility,
            volatility_persistence=persistence,
            source="mock",
        )

    async def get_live_sentiment(
        self, ticker: str, date: datetime, returns: pd.Series
    ) -> SentimentResult:
        """
        Uses the Antigravity AI Agent to determine live sentiment dynamics from
        news.

        Honesty contract (CONSTRAINT #4): when the agent is unavailable — the
        ``google.antigravity`` SDK is not installed, ``GEMINI_API_KEY`` is
        unset, or the live call fails/returns nothing — this returns an
        explicit ``source="unavailable"`` result with ``sentiment_score``,
        ``sentiment_intensity``, and ``credibility_score`` all ``None``.  It
        NEVER silently substitutes :meth:`generate_mock_sentiment`'s synthetic
        numbers into the live path; callers (API, Streamlit) must check
        ``source`` and render the honest unavailable state rather than assume
        a populated result.

        ``volatility_persistence`` is always the real, independently-computed
        GJR-GARCH persistence for ``returns`` (or ``None`` on insufficient
        data / fit failure) — it does not depend on the agent's own
        availability, since it is not derived from the agent at all.
        """
        from engine.agent_sentiment import analyze_sentiment
        agent_data = await analyze_sentiment(ticker)

        # Independent causal calculation — always attempted regardless of
        # whether the agent itself succeeded.
        _gamma, persistence, _vol = self.compute_asymmetric_volatility(returns)

        if not agent_data:
            return SentimentResult(
                ticker=ticker,
                date=date,
                sentiment_score=None,
                sentiment_intensity=None,
                credibility_score=None,
                volatility_persistence=persistence,
                source="unavailable",
            )

        # Extract agent values (already honest: SentimentOutput is a required
        # pydantic model, so any structured response carries real floats).
        score = agent_data.get("sentiment_score")
        intensity = agent_data.get("sentiment_intensity")
        credibility = agent_data.get("credibility_score")

        return SentimentResult(
            ticker=ticker,
            date=date,
            sentiment_score=score,
            sentiment_intensity=intensity,
            credibility_score=credibility,
            volatility_persistence=persistence,
            source="antigravity_agent",
        )
