import pytest
import os
from unittest.mock import patch, MagicMock

# Skip if langgraph is not installed
try:
    from langgraph.graph import StateGraph
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False

if _LANGGRAPH_AVAILABLE:
    from agents.supervisor import run_two_agent_analysis
    from agents.state import MultiAgentState


@pytest.mark.skipif(not _LANGGRAPH_AVAILABLE, reason="langgraph not installed")
@patch("agents.research_agent.DataEngine")
@patch("agents.research_agent.retrieve_documents")
@patch("agents.research_agent.relevance_filter")
@patch("agents.execution_agent.get_rationale_provider")
def test_multi_agent_workflow_success(mock_get_provider, mock_relevance, mock_retrieve, mock_data_engine):
    # Setup mocks
    mock_engine_instance = MagicMock()
    mock_engine_instance.fetch_technical_raw_cached.return_value = {"AAPL": MagicMock(to_dict=lambda: {"Close": {0: 150.0}})}
    mock_engine_instance.fetch_fundamentals_raw.return_value = {"AAPL": {"info": {"trailingPE": 20.0}}}
    mock_data_engine.return_value = mock_engine_instance
    
    mock_retrieve.return_value = {"retrieved_docs": []}
    mock_relevance.return_value = {"relevant_docs": [{"ticker": "AAPL", "title": "News 1"}]}
    
    mock_provider = MagicMock()
    mock_result = MagicMock()
    mock_result.rationale = "Strong fundamentals and positive news."
    mock_result.recommended_action = "BUY"
    mock_result.target_allocation_pct = 5.0
    mock_provider.call_structured.return_value = mock_result
    mock_get_provider.return_value = mock_provider

    result = run_two_agent_analysis(tickers=["AAPL"], user_query="Analyze AAPL")
    
    assert "errors" in result
    assert not result["errors"] # Should be empty
    assert result["advisory_rationale"] == "Strong fundamentals and positive news."
    assert len(result["proposed_orders"]) == 1
    assert result["proposed_orders"][0]["action"] == "BUY"
    assert result["proposed_orders"][0]["target_pct"] == 5.0

@pytest.mark.skipif(not _LANGGRAPH_AVAILABLE, reason="langgraph not installed")
def test_multi_agent_empty_symbols():
    result = run_two_agent_analysis(tickers=[], user_query="Analyze nothing")
    assert "errors" in result
    assert any("No symbols provided" in e for e in result["errors"])
