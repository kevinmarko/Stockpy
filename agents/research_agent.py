"""
agents/research_agent.py
=========================
Research Agent nodes for gathering market data and news.
"""
import logging
from typing import Dict, Any

from agents.state import MultiAgentState

logger = logging.getLogger(__name__)

def fetch_market_data(state: MultiAgentState) -> Dict[str, Any]:
    """Fetches market data for the requested symbols."""
    symbols = state.get("symbols", [])
    errors = state.get("errors", [])
    if not symbols:
        return {"errors": errors + ["No symbols provided for research."]}
        
    try:
        from data_engine import DataEngine
        from settings import settings
        
        # Use DataEngine to fetch data
        engine = DataEngine(fred_api_key=getattr(settings, "FRED_API_KEY", ""))
        
        # Fetch technicals and fundamentals
        tech_data = engine.fetch_technical_raw_cached(symbols)
        fund_data = engine.fetch_fundamentals_raw(symbols)
        
        # We store these raw inputs under signals for the execution agent to score
        return {
            "signals": {
                "technical_raw": {sym: df.to_dict() if hasattr(df, "to_dict") else df for sym, df in tech_data.items()},
                "fundamental_raw": fund_data
            }
        }
    except Exception as exc:
        logger.error(f"Research fetch failed: {exc}")
        return {"errors": errors + [f"Market data fetch error: {exc}"]}

def retrieve_news(state: MultiAgentState) -> Dict[str, Any]:
    """Retrieves relevant news using Qdrant (via rag_orchestrator logic)."""
    errors = state.get("errors", [])
    query = state.get("query", "")
    
    if not query:
        # Default query based on symbols if none provided
        symbols = state.get("symbols", [])
        if symbols:
            query = f"Recent news for {', '.join(symbols)}"
        else:
            return {"relevant_news": []}
            
    try:
        from agents.rag_orchestrator import retrieve_documents, relevance_filter
        
        # Adapt state to RAGState for the existing functions
        rag_state = {"query": query, "portfolio_context": [], "retrieved_docs": [], "relevant_docs": [], "final_analysis": ""}
        
        retrieved = retrieve_documents(rag_state)
        rag_state.update(retrieved)
        
        filtered = relevance_filter(rag_state)
        
        return {"relevant_news": filtered.get("relevant_docs", [])}
    except Exception as exc:
        logger.error(f"News retrieval failed: {exc}")
        return {"errors": errors + [f"News retrieval error: {exc}"]}
