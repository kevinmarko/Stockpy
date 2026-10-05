import pytest
import math
from volatility.iv_engine import IVHistoryStore, calculate_true_ivr

def test_iv_history_no_lookahead():
    """
    Verifies that IVR calculated at date D only queries history from dates strictly prior to D,
    protecting against lookahead bias.
    """
    # Use in-memory SQLite store
    store = IVHistoryStore(db_url="sqlite:///:memory:")
    ticker = "AAPL"
    
    # Record historical IVs
    # Dates: 2026-06-01, 2026-06-05, 2026-06-10, 2026-06-15, 2026-06-20
    store.record_iv(ticker, "2026-06-01", 0.20)
    store.record_iv(ticker, "2026-06-05", 0.30)
    store.record_iv(ticker, "2026-06-10", 0.40)
    store.record_iv(ticker, "2026-06-15", 0.50)
    store.record_iv(ticker, "2026-06-20", 0.60) # Date D
    store.record_iv(ticker, "2026-06-25", 0.80) # Future date D+5

    # Compute IVR as of 2026-06-20 (Date D)
    # The history queried must only contain dates < 2026-06-20: [0.20, 0.30, 0.40, 0.50]
    # The current IV at date D is 0.60
    current_iv = 0.60
    
    ivr_at_d = calculate_true_ivr(ticker, current_iv, "2026-06-20", store)
    
    # Query history using the raw function to verify contents
    history = store.get_historical_ivs(ticker, "2026-06-20")
    assert "2026-06-20" not in history
    assert "2026-06-25" not in history
    assert len(history) == 4
    
    # Establish expected IVR calculation:
    # All IVs = history + [current_iv] = [0.20, 0.30, 0.40, 0.50, 0.60]
    # min = 0.20, max = 0.60
    # ivr = (0.60 - 0.20) / (0.60 - 0.20) * 100 = 100.0
    assert abs(ivr_at_d - 100.0) < 1e-9

    # Now perturb a future date (e.g. 2026-06-25) and verify it does NOT affect ivr_at_d
    store.record_iv(ticker, "2026-06-25", 1.50)
    ivr_perturbed = calculate_true_ivr(ticker, current_iv, "2026-06-20", store)
    assert abs(ivr_perturbed - 100.0) < 1e-9
