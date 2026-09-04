"""
InvestYo Quant Platform - Agent Sentiment Engine
================================================
Processes and aggregates sentiment scores from multiple AI agent sources (e.g.,
LLMs, RAG-based analysis) for a given universe of tickers. Used to augment
the primary technical and fundamental signals in the strategy engine.
"""
import logging
import pydantic
from typing import Dict, Any

# We handle the import safely so if the SDK is missing, it fails gracefully.
try:
    from google.antigravity import Agent, LocalAgentConfig
    HAS_ANTIGRAVITY = True
except ImportError:
    HAS_ANTIGRAVITY = False

from settings import settings
from signals.news_catalyst import fetch_company_headlines

logger = logging.getLogger("AgentSentiment")

class SentimentOutput(pydantic.BaseModel):
    sentiment_score: float
    sentiment_intensity: float

def get_recent_news(ticker: str) -> str:
    """Fetches recent company news headlines for a given ticker.

    Provider-agnostic: FMP-first when configured
    (settings.FMP_NEWS_ENABLED + FMP_API_KEY), Finnhub-fallback otherwise
    (see signals.news_catalyst.fetch_company_headlines).
    """
    news_items = fetch_company_headlines(ticker, lookback_days=7)
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

    # Read via the `settings` singleton, not os.environ — pydantic-settings
    # loads .env into Settings only, never into the real process environment,
    # so an os.environ read here would see nothing for a key that only ever
    # lives in .env (the same bug class fixed across data/robinhood_portfolio.py,
    # prompt_registry/, and data/market_data.py — see CLAUDE.md's "Credential
    # reads MUST go through settings.X" convention).
    api_key = getattr(settings, "GEMINI_API_KEY", None)
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
                # Convert BaseModel to dict
                result = data.model_dump() if hasattr(data, "model_dump") else data.dict()
                
                # Compute credibility via SSOT
                from datetime import datetime, timezone
                from signals.credibility import score_documents, SentimentDocument
                from signals.news_catalyst import fetch_company_headlines

                news_items = fetch_company_headlines(ticker, lookback_days=7)
                if news_items:
                    docs = []
                    for item in news_items:
                        docs.append(SentimentDocument(
                            as_of=datetime.fromtimestamp(float(item.get("datetime", 0.0)), tz=timezone.utc),
                            symbol=ticker,
                            source_name=str(item.get("_provider", "unknown")),
                            text_content=str(item.get("headline", "")),
                            raw_sentiment_score=0.0,
                            author_handle=item.get("author"),
                        ))
                    cred_scores = score_documents(docs)
                    avg_cred = sum(c.S_total for c in cred_scores) / len(cred_scores) if cred_scores else 0.5
                    result["credibility_score"] = float(avg_cred)
                else:
                    result["credibility_score"] = 0.5
                    
                return result
            return {}
    except Exception as e:
        logger.error(f"Error running Antigravity Agent for {ticker}: {e}")
        return {}
