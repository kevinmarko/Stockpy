import numpy as np
import pandas as pd
import pytest
from pairs.cointegration import find_cointegrated_pairs, compute_half_life

def test_engle_granger_cointegration():
    """
    Test that find_cointegrated_pairs correctly identifies cointegrated series
    and rejects random walk pairs.
    """
    np.random.seed(42)
    n = 252
    # Create a random walk X
    x = np.cumsum(np.random.normal(0, 1, n)) + 100
    
    # Create a cointegrated series Y with AR(1) spread of 0.9 (half life ~ 6.5 days)
    # y = 0.5 * x + 10 + spread
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.9 * spread[-1] + np.random.normal(0, 0.5))
    spread = np.array(spread)
    y = 0.5 * x + 10.0 + spread
    
    # Create another random walk Z that is independent
    z = np.cumsum(np.random.normal(0, 1, n)) + 100
    
    df = pd.DataFrame({
        'Y': y,
        'X': x,
        'Z': z
    })
    
    # Run cointegration finder
    pairs = find_cointegrated_pairs(df, p_threshold=0.05)
    
    # The pair (Y, X) should be identified as cointegrated
    pair_names = [(p.ticker1, p.ticker2) for p in pairs]
    assert ('Y', 'X') in pair_names or ('X', 'Y') in pair_names
    
    # The pair (Y, Z) or (X, Z) should NOT be identified
    assert ('Y', 'Z') not in pair_names
    assert ('X', 'Z') not in pair_names

def test_half_life_calculation():
    """
    Test that compute_half_life returns expected values for mean-reverting series
    and infinity/nan/large values for non-mean-reverting series.
    """
    # 1. Create a mean-reverting series (AR(1) with coeff 0.9 -> lambda = -0.1)
    # Expected half life = -ln(2) / ln(0.9) = 6.57 days
    np.random.seed(42)
    n = 500
    spread = [0.0]
    for _ in range(n):
        spread.append(0.9 * spread[-1] + np.random.normal(0, 0.1))
        
    spread_series = pd.Series(spread)
    hl = compute_half_life(spread_series)
    assert 5.0 <= hl <= 8.0
    
    # 2. Create a non-mean-reverting series (random walk, lambda = 0)
    rw_series = pd.Series(np.cumsum(np.random.normal(0, 1, n)))
    hl_rw = compute_half_life(rw_series)
    assert hl_rw > 100 or np.isinf(hl_rw) or np.isnan(hl_rw)
