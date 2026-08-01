import pytest
from execution.options_analytics import compute_net_dealer_premium, compute_0dte_theta_decay, get_options_analytics_summary

def test_compute_net_dealer_premium():
    premium = compute_net_dealer_premium("SPY")
    assert isinstance(premium, float)

def test_compute_0dte_theta_decay():
    decay_series = compute_0dte_theta_decay()
    assert len(decay_series) == 13
    assert 'theta' in decay_series[0]
    assert 'gamma' in decay_series[0]
    
    # Check that theta and gamma are generally increasing
    assert decay_series[-1]['theta'] > decay_series[0]['theta']
    assert decay_series[-1]['gamma'] > decay_series[0]['gamma']

def test_get_options_analytics_summary():
    summary = get_options_analytics_summary("AAPL")
    assert summary["symbol"] == "AAPL"
    assert "net_dealer_premium" in summary
    assert "regime" in summary
    assert "intraday_series" in summary
    assert len(summary["intraday_series"]) == 13
