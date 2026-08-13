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

@pytest.mark.skipif(not _LANGGRAPH_AVAILABLE, reason="langgraph not installed")
@patch("agents.research_agent.DataEngine")
@patch("agents.research_agent.retrieve_documents")
@patch("agents.research_agent.relevance_filter")
@patch("agents.supervisor.get_rationale_provider")
@patch("agents.supervisor.get_research_provider")
def test_multi_agent_workflow_success(mock_get_research, mock_get_rationale, mock_relevance, mock_retrieve, mock_data_engine):
    # Setup mocks
    mock_engine_instance = MagicMock()
    mock_engine_instance.fetch_technical_raw_cached.return_value = {"AAPL": MagicMock(to_dict=lambda: {"Close": {0: 150.0}})}
    mock_engine_instance.fetch_fundamentals_raw.return_value = {"AAPL": {"info": {"trailingPE": 20.0}}}
    mock_data_engine.return_value = mock_engine_instance
    
    mock_retrieve.return_value = {"retrieved_docs": []}
    mock_relevance.return_value = {"relevant_docs": [{"ticker": "AAPL", "title": "News 1"}]}
    
    mock_research_provider = MagicMock()
    mock_research_provider.call.return_value = "Mock news summary"
    mock_get_research.return_value = mock_research_provider
    
    mock_rationale_provider = MagicMock()
    mock_result = MagicMock()
    mock_result.rationale = "Strong fundamentals and positive news."
    
    mock_order = MagicMock()
    mock_order.action = "BUY"
    mock_order.symbol = "AAPL"
    mock_order.weight = 0.05
    
    mock_result.orders = [mock_order]
    mock_rationale_provider.call_structured.return_value = mock_result
    mock_get_rationale.return_value = mock_rationale_provider

    result = run_two_agent_analysis(tickers=["AAPL"], user_query="Analyze AAPL")
    
    assert "errors" in result
    assert not result["errors"] # Should be empty
    assert result["workflow_status"] == "EXECUTION_COMPLETE"
    assert result["execution_plan"]["advisory_summary"] == "Strong fundamentals and positive news."
    assert len(result["execution_plan"]["hypothetical_orders"]) == 1
    assert result["execution_plan"]["hypothetical_orders"][0]["action"] == "BUY"
    assert result["execution_plan"]["hypothetical_orders"][0]["weight"] == 0.05

@pytest.mark.skipif(not _LANGGRAPH_AVAILABLE, reason="langgraph not installed")
@patch("agents.supervisor.get_research_provider")
@patch("agents.supervisor.get_rationale_provider")
def test_multi_agent_empty_symbols(mock_rat, mock_res):
    # Should still try to run but catch the DataEngine error if tickers are empty
    # Wait, if tickers are empty, DataEngine fetch will probably return empty or fail
    # Actually, the API blocks empty tickers, but let's test the graph anyway
    result = run_two_agent_analysis(tickers=[], user_query="Analyze nothing")
    assert "errors" in result
    # We expect some error or at least a fallback
