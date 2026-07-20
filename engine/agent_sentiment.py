import os
import logging
import pydantic
from typing import Dict, Any

# We handle the import safely so if the SDK is missing, it fails gracefully.
try:
    from google.antigravity import Agent, LocalAgentConfig
    HAS_ANTIGRAVITY = True
except ImportError:
    HAS_ANTIGRAVITY = False

from signals.news_catalyst import build_finnhub_client, fetch_company_news

logger = logging.getLogger("AgentSentiment")

class SentimentOutput(pydantic.BaseModel):
    sentiment_score: float
    sentiment_intensity: float
    credibility_score: float

def get_recent_news(ticker: str) -> str:
    """Fetches recent company news headlines for a given ticker."""
    client = build_finnhub_client()
    if not client:
        return f"No Finnhub API key available to fetch news for {ticker}."
    news_items = fetch_company_news(client, ticker, lookback_days=7)
    if not news_items:
        return f"No recent news found for {ticker}."
    
    # Just grab the headlines to avoid token bloat
    headlines = [item.get("headline", "") for item in news_items if item.get("headline")]
    return "\n".join(headlines[:20]) # Limit to top 20 headlines

async def analyze_sentiment(ticker: str) -> Dict[str, Any]:
    """
    Uses the Google Antigravity SDK to analyze recent news and determine
    sentiment score, intensity, and credibility.
    """
    if not HAS_ANTIGRAVITY:
        logger.warning("google.antigravity SDK not installed. Cannot run agent.")
        return {}

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set. Cannot run Antigravity agent.")
        return {}

    config = LocalAgentConfig(
        api_key=api_key,
        model="gemini-3.5-flash",
        tools=[get_recent_news],
        response_schema=SentimentOutput,
        system_instructions=(
            "You are a quantitative finance sentiment analyst. "
            "Use the `get_recent_news` tool to fetch headlines for the requested ticker. "
            "Analyze the news for emotional extremes, herding behavior, and source credibility. "
            "Return a structured JSON output with: "
            "1. sentiment_score (-1.0 to 1.0, where -1 is extremely negative and 1 is extremely positive) "
            "2. sentiment_intensity (0.1 to 1.0, representing the volume and emotional magnitude of the news) "
            "3. credibility_score (0.1 to 1.0, lower if news seems like rumors or 'fake news' spikes, higher for official earnings/FDA approvals etc.)"
        )
    )

    try:
        async with Agent(config) as agent:
            prompt = f"Analyze the recent news and sentiment dynamics for ticker: {ticker}"
            response = await agent.chat(prompt)
            data = await response.structured_output()
            if data:
                return data
            return {}
    except Exception as e:
        logger.error(f"Error running Antigravity Agent for {ticker}: {e}")
        return {}
