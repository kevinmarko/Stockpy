import math
import pytest
import pandas as pd
from datetime import datetime, date
from volatility.iv_engine import get_30d_atm_iv

class MockOptionChain:
    def __init__(self, calls, puts):
        self.calls = pd.DataFrame(calls)
        self.puts = pd.DataFrame(puts)

class MockDataEngineForIV:
    def __init__(self, expirations, chains):
        self.expirations = expirations
        self.chains = chains

    def fetch_options_chain(self, ticker, expiration=None):
        if expiration is None:
            return self.expirations
        return self.chains.get(expiration)

    def fetch_technical_raw(self, tickers):
        # Return empty so get_30d_atm_iv falls back or raises unless spot is provided
        return {}

def test_iv_linear_interpolation():
    """
    Test linear interpolation of implied volatilities to 30 calendar days.
    """
    # Expirations: 10 days and 40 days in the future
    exp1 = (datetime.now() + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    exp2 = (datetime.now() + pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    
    # Strike is 100 (ATM)
    chain1 = MockOptionChain(
        calls=[{'strike': 100.0, 'impliedVolatility': 0.20}],
        puts=[{'strike': 100.0, 'impliedVolatility': 0.20}]
    )
    chain2 = MockOptionChain(
        calls=[{'strike': 100.0, 'impliedVolatility': 0.30}],
        puts=[{'strike': 100.0, 'impliedVolatility': 0.30}]
    )
    
    mock_de = MockDataEngineForIV(
        expirations=[exp1, exp2],
        chains={exp1: chain1, exp2: chain2}
    )
    
    # 30d ATM IV should be 0.20 + (0.30 - 0.20) * (30 - 10) / (40 - 10) = 0.266667
    iv_30 = get_30d_atm_iv(mock_de, "AAPL", datetime.now().strftime("%Y-%m-%d"), spot_price=100.0)
    
    assert not math.isnan(iv_30)
    assert abs(iv_30 - 0.2666667) < 1e-5

def test_iv_interpolation_edge_cases():
    """
    Test edge cases when fetching options (empty list of expirations, missing chain data, etc.)
    """
    # 1. Empty expirations
    mock_de_empty = MockDataEngineForIV(expirations=[], chains={})
    iv_empty = get_30d_atm_iv(mock_de_empty, "AAPL", datetime.now().strftime("%Y-%m-%d"), spot_price=100.0)
    assert math.isnan(iv_empty)

    # 2. Only 1 expiration (cannot interpolate)
    exp = (datetime.now() + pd.Timedelta(days=15)).strftime("%Y-%m-%d")
    mock_de_one = MockDataEngineForIV(expirations=[exp], chains={})
    iv_one = get_30d_atm_iv(mock_de_one, "AAPL", datetime.now().strftime("%Y-%m-%d"), spot_price=100.0)
    assert math.isnan(iv_one)
