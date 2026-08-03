"""
agents/supervisor.py

Supervisor orchestrator for the multi-agent analysis layer using LangGraph.
"""

from typing import Any, Dict, TypedDict
import logging
from langgraph.graph import StateGraph, END

from llm.router import get_model
from agents.research_agent import run_research
from agents.execution_agent import run_execution
from execution.risk_gate import PreTradeRiskGate

logger = logging.getLogger(__name__)

class SupervisorState(TypedDict):
    tickers: list[str]
    user_query: str
    market_context: Dict[str, Any]
    research_data: Dict[str, Any]
    execution_plan: Dict[str, Any]

def research_node(state: SupervisorState) -> SupervisorState:
    llm = get_model("high_context", use_langchain_native=True)
    state["research_data"] = run_research(
        tickers=state["tickers"],
        context=state["market_context"],
        query=state["user_query"],
        llm=llm
    )
    return state

def execution_node(state: SupervisorState) -> SupervisorState:
    llm = get_model("high_reasoning", use_langchain_native=True)
    plan = run_execution(state["research_data"], llm)
    
    # Strictly enforce ADVISORY_ONLY constraint
    plan["ADVISORY_ONLY"] = True
    
    # Ensure it passes through the risk gate even in advisory mode 
    # to vet the hypothetical orders
    if plan.get("execution_plan", {}).get("proposed_orders"):
        from execution.risk_gate import RiskContext
        from execution.broker_base import OrderIntent, OrderSide
        
        gate = PreTradeRiskGate()
        context = RiskContext()
        
        valid_orders = []
        for p_order in plan["proposed_orders"]:
            try:
                # Convert the raw order into an OrderIntent
                intent = OrderIntent(
                    strategy_id="multi_agent_advisory",
                    symbol=p_order["ticker"],
                    side=OrderSide(p_order["action"].lower()),
                    qty=float(p_order["quantity"])
                )
                
                # Evaluate the order
                passed, _ = gate.run_all(intent, context)
                if passed:
                    valid_orders.append(p_order)
                else:
                    logger.warning(f"Risk gate rejected order for {p_order['ticker']}")
            except Exception as e:
                logger.warning(f"Error evaluating risk for {p_order.get('ticker')}: {e}")
                
        plan["proposed_orders"] = valid_orders
        plan["risk_gate_passed"] = len(valid_orders) > 0
            
    state["execution_plan"] = plan
    return state

# Define LangGraph workflow
workflow = StateGraph(SupervisorState)
workflow.add_node("research", research_node)
workflow.add_node("execution", execution_node)

workflow.set_entry_point("research")
workflow.add_edge("research", "execution")
workflow.add_edge("execution", END)

supervisor_app = workflow.compile()

def run_two_agent_analysis(tickers: list[str], query: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Entry point for the API to run the LangGraph analysis.
    """
    initial_state = {
        "tickers": tickers,
        "user_query": query,
        "market_context": context or {},
        "research_data": {},
        "execution_plan": {}
    }
    
    try:
        final_state = supervisor_app.invoke(initial_state)
        return final_state
    except Exception as e:
        logger.error(f"Error in multi-agent analysis: {e}")
        return {
            "error": str(e),
            "ADVISORY_ONLY": True,
            "fallback": "Analysis failed gracefully."
        }
