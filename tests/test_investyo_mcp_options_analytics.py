import pytest
import math
from investyo_mcp_server import analyze_options_chain, simulate_0dte_payoff

def test_simulate_0dte_payoff_honest_status():
    result = simulate_0dte_payoff("SPY", 1)
    
    assert "live_exit_gate_wired" in result
    assert result["live_exit_gate_wired"] is False, "Must honestly report that the live exit gate is not wired"
    
    assert "strategy_registry_status" in result
    assert result["strategy_registry_status"] == "unregistered", "Must honestly report it is unregistered"
    
    # Must never call execute_0dte_trade (implicit, we don't mock it because it would fail if called)

def test_analyze_options_chain_missing_data(monkeypatch):
    # Test that missing chain data degrades gracefully without crashing
    def mock_fetch_chain(*args, **kwargs):
        raise ValueError("Network error")
        
    class MockOptionsProvider:
        def fetch_options_chain(self, *args, **kwargs):
            raise ValueError("Network error")
            
    monkeypatch.setattr("data.market_data.get_options_provider", lambda: MockOptionsProvider())
    
    result = analyze_options_chain("XYZ")
    assert "error" in result
    assert "No chain data" in result["error"]
