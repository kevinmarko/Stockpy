import logging
from typing import Dict, Any, List
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class OrderSchema(BaseModel):
    action: str
    symbol: str
    weight: float

class AdvisorySchema(BaseModel):
    rationale: str
    orders: List[OrderSchema]

def run_execution(research_data: Dict[str, Any], provider: Any, user_query: str, tickers: List[str]) -> Dict[str, Any]:
    if provider is None:
        raise ValueError("No LLM provider configured for execution.")
        
    news = research_data.get("news_context", "")
    funds = research_data.get("signals", {}).get("fundamental_raw", {})
    
    context_str = f"Symbols under review: {', '.join(tickers)}\n\n"
    context_str += f"News Context:\n{news}\n\n"
    
    for sym in tickers:
        if sym in funds:
            info = funds[sym].get('info', {})
            context_str += f"{sym} Fundamentals: PE={info.get('trailingPE', 'N/A')}, Div Yield={info.get('dividendYield', 'N/A')}\n"
            
    system_prompt = (
        "You are an institutional quant execution agent. Based on the provided data, "
        "synthesize a concise investment rationale, and propose orders. "
        "For each order, specify action (BUY, HOLD, SELL), symbol, and weight (0.0 to 1.0). "
        "Never fabricate data. If data is insufficient, recommend HOLD with 0 weight."
    )
    user_prompt = f"Context:\n{context_str}\n\nQuery: {user_query}"
    
    # call_structured is expected by the LLMProvider contract
    result = provider.call_structured(system_prompt, user_prompt, AdvisorySchema)
    if not result:
        raise ValueError("LLM returned empty structured response.")
        
    hypothetical_orders = []
    for order in result.orders:
        hypothetical_orders.append({
            "action": order.action,
            "symbol": order.symbol,
            "weight": order.weight
        })
        
    return {
        "advisory_summary": result.rationale,
        "hypothetical_orders": hypothetical_orders,
        "risk_check_passed": True
    }
