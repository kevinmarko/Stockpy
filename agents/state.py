"""
agents/state.py
================
Shared state definition for the multi-agent orchestration graph.
"""
from typing import TypedDict, List, Dict, Any

class MultiAgentState(TypedDict, total=False):
    # Inputs
    query: str
    symbols: List[str]
    
    # Research Agent Outputs
    portfolio_context: List[str]
    relevant_news: List[Dict[str, Any]]
    signals: Dict[str, Any]
    
    # Execution Agent Outputs
    advisory_rationale: str
    proposed_orders: List[Dict[str, Any]]
    
    # Error Handling
    errors: List[str]
