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

# ---------------------------------------------------------------------------
# Architecture Imports
# ---------------------------------------------------------------------------
# These imports reflect the established architecture. If running in isolation 
# for testing, ensure mock equivalents are available in your PYTHONPATH.
try:
    from llm.router import get_model
    from agents.research_agent import run_research
    from agents.execution_agent import run_execution
except ImportError:
    # Graceful mock definitions to guarantee structural executability for testing
    logging.warning("Architecture modules not found. Using local mock implementations for testing.")
    
    def get_model(route_type: str) -> str:
        return f"Mock_LLM_Provider_{route_type.upper()}"
        
    def run_research(tickers: List[str], context: Dict[str, Any], query: str, llm: Any) -> Dict[str, Any]:
        return {
            "signals": {ticker: {"trend": "bullish", "momentum": 0.8} for ticker in tickers},
            "news_context": "Market is stable.",
            "fundamentals_valid": True
        }
        
    def run_execution(research_data: Dict[str, Any], llm: Any) -> Dict[str, Any]:
        return {
            "advisory_summary": "Based on bullish signals, optimal sizing calculated.",
            "hypothetical_orders": [{"ticker": t, "action": "BUY", "size": 100} for t in research_data.get("signals", {})],
            "risk_check_passed": True
        }

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logger = logging.getLogger("InvestYo.Supervisor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

# ---------------------------------------------------------------------------
# State Definition
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Node Definitions
# ---------------------------------------------------------------------------
def research_node(state: SupervisorState) -> Dict[str, Any]:
    """
    Agent 1: Research/Data
    Fetches market data, calculates signals, and retrieves news context.
    Uses an LLM optimized for large context windows and fast extraction.
    """
    logger.info(f"Starting Research Node for tickers: {state['tickers']}")
    
    # Request distinct model routing: High Context Window for RAG/News
    context_llm = get_model(route_type="high_context")
    
    try:
        # Pass all tickers in O(1) block request to the research agent
        research_results = run_research(
            tickers=state["tickers"],
            context=state["market_context"],
            query=state["user_query"],
            llm=context_llm
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
    Uses an LLM optimized for strict logical reasoning and constraint adherence.
    """
    logger.info("Starting Execution Node")
    
    # Request distinct model routing: High Reasoning for strategy formulation
    reasoning_llm = get_model(route_type="high_reasoning")
    
    try:
        # Enforce fail-closed state safety check
        if not state.get("research_data"):
            raise ValueError("Research data is missing; cannot proceed to execution.")
            
        execution_results = run_execution(
            research_data=state["research_data"],
            llm=reasoning_llm
        )
        
        # Hardcoded Safety Override: Guarantee ADVISORY_ONLY
        execution_results["mode"] = "ADVISORY_ONLY"
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
    If LLMs, Qdrant, or APIs fail, this node returns pure heuristic outputs
    without AI reasoning to ensure the application does not crash.
    """
    logger.warning("Initiating Fallback Node due to prior workflow failures.")
    
    pure_heuristic_plan = {
        "advisory_summary": "AI processing unavailable. Reverting to basic heuristic evaluation. No trades recommended.",
        "hypothetical_orders": [],
        "mode": "ADVISORY_ONLY",
        "live_trading_enabled": False,
        "fallback_activated": True
    }
    
    return {
        "execution_plan": pure_heuristic_plan,
        "workflow_status": "FALLBACK_COMPLETE"
    }

# ---------------------------------------------------------------------------
# Edge Routing Logic
# ---------------------------------------------------------------------------
def route_after_research(state: SupervisorState) -> str:
    """
    Determines the next node based on the success of the Research Agent.
    """
    status = state.get("workflow_status")
    if status == "RESEARCH_COMPLETE":
        return "execution_node"
    else:
        return "fallback_node"
        
def route_after_execution(state: SupervisorState) -> str:
    """
    Determines the next node based on the success of the Execution Agent.
    """
    status = state.get("workflow_status")
    if status == "EXECUTION_COMPLETE":
        return END
    else:
        return "fallback_node"

# ---------------------------------------------------------------------------
# Graph Compilation
# ---------------------------------------------------------------------------
def build_supervisor_graph() -> Any:
    """
    Constructs and compiles the Two-Agent LangGraph workflow.
    """
    builder = StateGraph(SupervisorState)
    
    # Add Nodes
    builder.add_node("research_node", research_node)
    builder.add_node("execution_node", execution_node)
    builder.add_node("fallback_node", fallback_node)
    
    # Define Graph Flow
    builder.set_entry_point("research_node")
    
    # Add Conditional Edges
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
    
    # Ensure fallback always ends the graph safely
    builder.add_edge("fallback_node", END)
    
    return builder.compile()

# ---------------------------------------------------------------------------
# Expose Execution Wrapper for API
# ---------------------------------------------------------------------------
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
                "mode": "ADVISORY_ONLY",
                "live_trading_enabled": False
            },
            "errors": [str(e)],
            "workflow_status": "SYSTEM_FAULT"
        }

# ---------------------------------------------------------------------------
# Direct Module Execution (For Testing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Test the multi-agent graph locally
    test_tickers = ["AAPL", "MSFT"]
    print(f"Running standalone test for: {test_tickers}")
    
    result = run_two_agent_analysis(
        tickers=test_tickers,
        user_query="Evaluate current tech momentum",
        market_context={"vix": 15.2, "fed_rate": 5.25}
    )
    
    import json
    print("\n--- Final Graph State ---")
    print(json.dumps(result, indent=2))
