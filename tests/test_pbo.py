import numpy as np
from validation.metrics import probability_of_backtest_overfitting

def test_pbo_random_data():
    """
    With 100 random strategies on random data, PBO should be ~0.5
    due to lack of relationship between IS and OOS performance.
    """
    np.random.seed(42)
    # 45 paths, 100 strategies
    n_paths = 45
    n_strategies = 100
    
    is_sharpes = np.random.randn(n_paths, n_strategies)
    oos_sharpes = np.random.randn(n_paths, n_strategies)
    
    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    # Check that PBO is around 0.5 (within standard range 0.3 to 0.7)
    assert 0.3 <= pbo <= 0.7

def test_pbo_perfect_strategy():
    """
    With 1 strategy that perfectly predicts the test set, PBO should be exactly 0.
    """
    n_paths = 45
    n_strategies = 10
    
    is_sharpes = np.random.randn(n_paths, n_strategies)
    oos_sharpes = np.random.randn(n_paths, n_strategies)
    
    # Strategy 0 is perfect: always highest IS and OOS
    is_sharpes[:, 0] = 5.0
    oos_sharpes[:, 0] = 5.0
    
    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    assert pbo == 0.0
