import pytest
from agents.supervisor import run_two_agent_analysis

def test_multi_agent_returns_advisory_only():
    """
    Ensures that the LangGraph workflow properly applies the ADVISORY_ONLY flag
    and gracefully handles the analysis.
    """
    tickers = ["AAPL"]
    query = "Is this a good time to buy?"
    context = {"VIX": 15.0}

    # Run the graph
    result = run_two_agent_analysis(tickers, query, context)

    # It must return ADVISORY_ONLY = True no matter what
    if "error" in result:
        assert result["ADVISORY_ONLY"] is True
    else:
        assert "execution_plan" in result
        assert result["execution_plan"]["ADVISORY_ONLY"] is True
