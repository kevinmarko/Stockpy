import pytest
import math
from investyo_mcp_server import analyze_options_chain, scan_0dte_signals

def test_scan_0dte_signals_honest_status():
    result = scan_0dte_signals("SPY", 1)
    
    # Evaluate what the setting actually is right now
    try:
        from settings import settings as _s
        expected_wired = bool(getattr(_s, "OPTIONS_0DTE_ENABLED", False))
    except ImportError:
        expected_wired = False

    assert "live_exit_gate_wired" in result
    assert result["live_exit_gate_wired"] is expected_wired, "Must honestly report the real live exit gate status"
    
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
