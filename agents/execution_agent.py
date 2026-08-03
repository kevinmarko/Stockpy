"""
agents/execution_agent.py
=========================
Execution Agent nodes for strategy scoring, sizing, and advisory generation.
"""
import logging
import json
from typing import Dict, Any, List

from agents.state import MultiAgentState

logger = logging.getLogger(__name__)

class _AdvisoryOutput:
    # A simple pseudo-schema to represent the expected output from the LLM.
    # Since llm.router call_structured uses Pydantic, we define it here.
    from pydantic import BaseModel
    class Schema(BaseModel):
        rationale: str
        recommended_action: str
        target_allocation_pct: float

def generate_advisory(state: MultiAgentState) -> Dict[str, Any]:
    """Synthesizes research data into actionable advice and sizing."""
    errors = state.get("errors", [])
    signals = state.get("signals", {})
    news = state.get("relevant_news", [])
    portfolio = state.get("portfolio_context", [])
    query = state.get("query", "")
    symbols = state.get("symbols", [])
    
    # Format context for the LLM
    context_str = f"Symbols under review: {', '.join(symbols)}\n\n"
    if portfolio:
        context_str += f"Portfolio Context: {', '.join(portfolio)}\n\n"
    
    if news:
        context_str += "Relevant News:\n"
        for n in news:
            context_str += f"- [{n.get('ticker')}] {n.get('title')}\n"
    
    # We pass fundamental data snippets if available
    fund_raw = signals.get("fundamental_raw", {})
    for sym in symbols:
        if sym in fund_raw:
            info = fund_raw[sym].get('info', {})
            context_str += f"\n{sym} Fundamentals: PE={info.get('trailingPE', 'N/A')}, Div Yield={info.get('dividendYield', 'N/A')}"
            
    system_prompt = (
        "You are an institutional quant execution agent. Based on the provided data, "
        "synthesize a concise investment rationale, recommend an action (BUY, HOLD, SELL), "
        "and suggest a target allocation percentage (0.0 to 100.0). "
        "Never fabricate data. If data is insufficient, recommend HOLD with 0 allocation."
    )
    user_prompt = f"Context:\n{context_str}\n\nQuery: {query}"
    
    try:
        from llm.router import get_rationale_provider
        provider = get_rationale_provider()
        
        if provider is not None:
            # call_structured is expected by the LLMProvider contract
            result = provider.call_structured(system_prompt, user_prompt, _AdvisoryOutput.Schema)
            if result:
                return {
                    "advisory_rationale": result.rationale,
                    "proposed_orders": [{
                        "action": result.recommended_action,
                        "target_pct": result.target_allocation_pct,
                        "symbols": symbols
                    }]
                }
            else:
                return {"errors": errors + ["LLM returned empty structured response."]}
        else:
            return {"errors": errors + ["No LLM provider configured for rationale."]}
    except Exception as exc:
        logger.error(f"Execution advisory failed: {exc}")
        return {"errors": errors + [f"Advisory error: {exc}"]}
