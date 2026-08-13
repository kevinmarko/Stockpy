"""
supervisor.py

Top-level LangGraph orchestrator for the InvestYo Quant Platform.
Coordinates the Research Agent and Execution Agent, enforcing distinct LLM routing,
strict fail-closed safety constraints (ADVISORY_ONLY=True), and graceful degradation.
"""

import logging
import traceback
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, END

# Architecture Imports (No mock fallbacks!)
from llm.router import get_research_provider, get_rationale_provider
from agents.research_agent import run_research
from agents.execution_agent import run_execution

logger = logging.getLogger("InvestYo.Supervisor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

class SupervisorState(TypedDict):
    """
    The strict state dictionary passed between nodes in the LangGraph workflow.
    """
    tickers: List[str]
    user_query: str
    market_context: Dict[str, Any]
    
    # Populated by Research Agent
    research_data: Optional[Dict[str, Any]]
    
    # Populated by Execution Agent
    execution_plan: Optional[Dict[str, Any]]
    
    # State tracking and Graceful Degradation
    errors: List[str]
    workflow_status: str

def research_node(state: SupervisorState) -> Dict[str, Any]:
    """
    Agent 1: Research/Data
    Fetches market data, calculates signals, and retrieves news context.
    """
    logger.info(f"Starting Research Node for tickers: {state['tickers']}")
    
    # Request distinct model routing: High Context Window for RAG/News
    provider = get_research_provider()
    
    try:
        research_results = run_research(
            tickers=state["tickers"],
            context=state["market_context"],
            query=state["user_query"],
            provider=provider
        )
        
        return {
            "research_data": research_results,
            "workflow_status": "RESEARCH_COMPLETE"
        }
    except Exception as e:
        error_msg = f"Research Agent failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return {
            "errors": state.get("errors", []) + [error_msg],
            "workflow_status": "RESEARCH_FAILED"
        }

def execution_node(state: SupervisorState) -> Dict[str, Any]:
    """
    Agent 2: Strategy/Execution
    Evaluates strategy, calculates Kelly/vol-target sizing, and formats advisory.
    """
    logger.info("Starting Execution Node")
    
    # Request distinct model routing: High Reasoning for strategy formulation
    provider = get_rationale_provider()
    
    try:
        # Enforce fail-closed state safety check
        if not state.get("research_data"):
            raise ValueError("Research data is missing; cannot proceed to execution.")
            
        execution_results = run_execution(
            research_data=state["research_data"],
            provider=provider,
            user_query=state.get("user_query", ""),
            tickers=state["tickers"]
        )
        
        # Hardcoded Safety Override: Guarantee ADVISORY_ONLY
        execution_results["mode"] = "advisory"
        execution_results["live_trading_enabled"] = False
        
        return {
            "execution_plan": execution_results,
            "workflow_status": "EXECUTION_COMPLETE"
        }
    except Exception as e:
        error_msg = f"Execution Agent failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return {
            "errors": state.get("errors", []) + [error_msg],
            "workflow_status": "EXECUTION_FAILED"
        }

def fallback_node(state: SupervisorState) -> Dict[str, Any]:
    """
    Graceful Degradation Handler.
    """
    logger.warning("Initiating Fallback Node due to prior workflow failures.")
    
    pure_heuristic_plan = {
        "advisory_summary": "AI processing unavailable. Reverting to basic heuristic evaluation. No trades recommended.",
        "hypothetical_orders": [],
        "mode": "advisory",
        "live_trading_enabled": False,
        "fallback_activated": True
    }
    
    return {
        "execution_plan": pure_heuristic_plan,
        "workflow_status": "FALLBACK_COMPLETE"
    }

def route_after_research(state: SupervisorState) -> str:
    status = state.get("workflow_status")
    if status == "RESEARCH_COMPLETE":
        return "execution_node"
    else:
        return "fallback_node"
        
def route_after_execution(state: SupervisorState) -> str:
    status = state.get("workflow_status")
    if status == "EXECUTION_COMPLETE":
        return END
    else:
        return "fallback_node"

def build_supervisor_graph() -> Any:
    builder = StateGraph(SupervisorState)
    
    builder.add_node("research_node", research_node)
    builder.add_node("execution_node", execution_node)
    builder.add_node("fallback_node", fallback_node)
    
    builder.set_entry_point("research_node")
    
    builder.add_conditional_edges(
        "research_node",
        route_after_research,
        {
            "execution_node": "execution_node",
            "fallback_node": "fallback_node"
        }
    )
    
    builder.add_conditional_edges(
        "execution_node",
        route_after_execution,
        {
            END: END,
            "fallback_node": "fallback_node"
        }
    )
    
    builder.add_edge("fallback_node", END)
    return builder.compile()

def run_two_agent_analysis(tickers: List[str], user_query: str = "", market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Main entry point for api/pilots_api.py to invoke the agent graph.
    """
    app = build_supervisor_graph()
    
    initial_state: SupervisorState = {
        "tickers": tickers,
        "user_query": user_query,
        "market_context": market_context or {},
        "research_data": None,
        "execution_plan": None,
        "errors": [],
        "workflow_status": "INITIALIZED"
    }
    
    try:
        final_state = app.invoke(initial_state)
        return final_state
    except Exception as e:
        logger.critical(f"Catastrophic graph failure: {str(e)}")
        # Ultimate fail-closed payload if LangGraph itself crashes
        return {
            "execution_plan": {
                "advisory_summary": "System error. All trading halted.",
                "hypothetical_orders": [],
                "mode": "advisory",
                "live_trading_enabled": False,
                "fallback_activated": True
            },
            "errors": [str(e)],
            "workflow_status": "SYSTEM_FAULT"
        }
