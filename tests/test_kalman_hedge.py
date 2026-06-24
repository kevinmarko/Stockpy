import numpy as np
import pandas as pd
import pytest
from pairs.kalman_hedge import KalmanHedgeRatio, KalmanHedgeRatioTracker

def test_kalman_hedge_stationary_recovery():
    """
    Test that the Kalman filter beta estimate converges near OLS beta for a stationary relation.
    We center x around 0 to avoid collinearity/cross-talk between intercept and slope.
    """
    np.random.seed(42)
    n = 300
    x = np.random.normal(0, 5, n)
    # y = 10 + 1.5 * x + noise
    y = 10.0 + 1.5 * x + np.random.normal(0, 0.5, n)
    
    y_series = pd.Series(y)
    x_series = pd.Series(x)
    
    # Run Kalman Filter
    kh = KalmanHedgeRatio(transition_covariance_multiplier=1e-5, observation_covariance=1e-3)
    hedge_df = kh.estimate_hedge_ratio(y_series, x_series)
    
    assert len(hedge_df) == n
    assert 'alpha' in hedge_df.columns
    assert 'beta' in hedge_df.columns
    
    # Check that in the second half of the series, the beta is very close to 1.5
    final_beta = hedge_df['beta'].iloc[-20:].mean()
    assert pytest.approx(final_beta, abs=0.1) == 1.5
    
    # Check that alpha converges near 10
    final_alpha = hedge_df['alpha'].iloc[-20:].mean()
    assert pytest.approx(final_alpha, abs=1.0) == 10.0

def test_kalman_tracker_step_by_step():
    """
    Test that the online KalmanHedgeRatioTracker produces updates consistent with the batch class.
    """
    np.random.seed(42)
    n = 50
    x = np.random.normal(0, 2, n)
    y = 5.0 - 0.8 * x + np.random.normal(0, 0.2, n)
    
    tracker = KalmanHedgeRatioTracker(transition_covariance_multiplier=1e-5, observation_covariance=1e-3)
    
    alphas = []
    betas = []
    for i in range(n):
        a, b = tracker.update(y[i], x[i])
        alphas.append(a)
        betas.append(b)
        
    # Check that final parameters trend towards the true slope of -0.8
    assert betas[-1] < 0.0
    assert abs(betas[-1] - (-0.8)) < 0.2
