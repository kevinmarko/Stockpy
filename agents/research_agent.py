import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

def run_research(tickers: List[str], context: Dict[str, Any], query: str, provider: Any) -> Dict[str, Any]:
    from data_engine import DataEngine
    from settings import settings
    from agents.rag_orchestrator import retrieve_documents, relevance_filter
    
    engine = DataEngine(fred_api_key=getattr(settings, "FRED_API_KEY", ""))
    
    # 1. Fetch Fundamentals (we need to know if they are valid)
    fund_data = engine.fetch_fundamentals_raw(tickers)
    # Check if fundamentals are valid (i.e. not completely missing)
    fundamentals_valid = any(bool(v.get('info', {})) for v in fund_data.values())
    
    # 2. Retrieve News using RAG logic
    rag_query = query if query else f"Recent news and updates for {', '.join(tickers)}"
    rag_state = {"query": rag_query, "portfolio_context": [], "retrieved_docs": [], "relevant_docs": [], "final_analysis": ""}
    
    try:
        retrieved = retrieve_documents(rag_state)
        rag_state.update(retrieved)
        filtered = relevance_filter(rag_state)
        relevant_news = filtered.get("relevant_docs", [])
    except Exception as e:
        logger.warning(f"Failed to retrieve news: {e}")
        relevant_news = []
        
    news_context = ""
    for idx, doc in enumerate(relevant_news):
        news_context += f"{idx+1}. [{doc.get('ticker', 'N/A')}] {doc.get('title', '')}\n"
    
    if not news_context:
        news_context = "No relevant news found."
        
    # Optional: use provider to summarize news if a provider is configured and available
    if provider and relevant_news:
        try:
            summary = provider.call(f"Summarize the following news for {tickers}:\n{news_context}")
            if summary:
                news_context = summary
        except Exception as e:
            logger.warning(f"Research provider summary failed: {e}")
            
    return {
        "signals": {
            "fundamental_raw": fund_data,
        },
        "news_context": news_context,
        "fundamentals_valid": fundamentals_valid
    }
