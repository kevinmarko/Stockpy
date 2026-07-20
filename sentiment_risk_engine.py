"""
Sentiment Risk Engine
Computes social media sentiment dynamics and their impact on market volatility,
specifically measuring the asymmetric leverage effect, sentiment intensity,
credibility filtering, and volatility persistence.
"""
from datetime import datetime
import numpy as np
import pandas as pd
import logging
from typing import Tuple

from arch import arch_model

from dto_models import SentimentDTO

logger = logging.getLogger("SentimentRiskEngine")

class SentimentRiskEngine:
    def __init__(self):
        pass

    def compute_asymmetric_volatility(self, returns: pd.Series) -> Tuple[float, float, float]:
        """
        Fits a GJR-GARCH(1,1,1) model to compute the asymmetric leverage effect
        and volatility persistence.
        Returns:
            gamma (float): Asymmetry coefficient (positive means negative shocks increase vol more).
            persistence (float): alpha + beta + (gamma / 2), measure of shock persistence.
            current_vol (float): Annualized conditional volatility of the last observation.
        """
        if len(returns) < 100:
            logger.warning("Insufficient data for GJR-GARCH model. Returning defaults.")
            return 0.0, 0.95, 0.15 # Defaults
            
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
            last_vol = res.conditional_volatility.iloc[-1] / 100.0 # unscale
            annualized_vol = last_vol * np.sqrt(252)
            
            return float(gamma), float(persistence), float(annualized_vol)
        except Exception as e:
            logger.warning(f"GJR-GARCH model failed: {e}. Returning defaults.")
            return 0.0, 0.95, 0.15

    def generate_mock_sentiment(self, ticker: str, date: datetime, returns: pd.Series) -> SentimentDTO:
        """
        Generates synthetic sentiment data for UI demonstration purposes, inversely
        correlated with recent market returns to simulate the leverage effect.
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
                credibility = 0.4 # Rumor mill runs wild during crashes
            else:
                credibility = 0.9
                
        # Compute real asymmetry and persistence if enough data
        gamma, persistence, vol = self.compute_asymmetric_volatility(returns)
                
        return SentimentDTO(
            ticker=ticker,
            date=date,
            sentiment_score=score,
            sentiment_intensity=intensity,
            credibility_score=credibility,
            volatility_persistence=persistence
        )

    async def get_live_sentiment(self, ticker: str, date: datetime, returns: pd.Series) -> SentimentDTO:
        """
        Uses the Antigravity AI Agent to determine live sentiment dynamics from news.
        Falls back to mock data if the agent fails or lacks API keys.
        """
        from engine.agent_sentiment import analyze_sentiment
        agent_data = await analyze_sentiment(ticker)
        
        if not agent_data:
            # Fallback to mock logic if agent returns empty
            return self.generate_mock_sentiment(ticker, date, returns)
            
        # Extract agent values
        score = agent_data.get("sentiment_score", 0.0)
        intensity = agent_data.get("sentiment_intensity", 0.5)
        credibility = agent_data.get("credibility_score", 0.8)
        
        # Still compute real mathematical persistence from price history
        gamma, persistence, vol = self.compute_asymmetric_volatility(returns)
        
        return SentimentDTO(
            ticker=ticker,
            date=date,
            sentiment_score=score,
            sentiment_intensity=intensity,
            credibility_score=credibility,
            volatility_persistence=persistence
        )

