from typing import List, Dict, Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

# Mocking your internal data providers based on the project spec
# from app.data.providers import MarketDataProvider, NewsProvider

def run_research(tickers: List[str], context: Dict[str, Any], query: str, llm: BaseChatModel) -> Dict[str, Any]:
    """
    Agent 1 (Research/Data): Gathers deterministc data and uses a high-context LLM 
    to summarize and synthesize market conditions.
    
    Args:
        tickers (List[str]): List of stock tickers to research.
        context (Dict[str, Any]): Existing state/context from the workflow.
        query (str): The user's original query.
        llm (BaseChatModel): The LangChain high-context chat model.
        
    Returns:
        Dict[str, Any]: Structured research data ready for the execution agent.
    """
    
    # 1. Deterministic Data Gathering (Fail-safe Python logic, not LLM tools)
    raw_market_data = {}
    for ticker in tickers:
        # Example of how you would call your internal providers:
        # fundamentals = MarketDataProvider.get_fundamentals(ticker)
        # news = NewsProvider.get_recent_headlines(ticker, limit=5)
        
        # Mock data structure for completeness
        raw_market_data[ticker] = {
            "price_action": "Up 2.5% on higher than average volume.",
            "fundamentals": "P/E ratio at 22, strong free cash flow.",
            "news_summary": "Recent earnings beat expectations; CEO announced share buyback."
        }
    
    # 2. LLM Synthesis
    system_prompt = (
        "You are a quantitative research assistant. Your job is to analyze the provided raw market data "
        "and the user's query. Output a highly structured, objective summary of the current market "
        "conditions, risks, and technical signals for the provided tickers. Do not recommend sizing."
    )
    
    human_prompt = f"""
    User Query: {query}
    
    Raw Market Data:
    {raw_market_data}
    
    Please provide the synthesized research payload.
    """
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt)
    ]
    
    response = llm.invoke(messages)
    
    # 3. Format state output
    research_payload = {
        "tickers": tickers,
        "original_query": query,
        "raw_data": raw_market_data,
        "llm_synthesis": response.content,
        "status": "research_complete"
    }
    
    return research_payload
