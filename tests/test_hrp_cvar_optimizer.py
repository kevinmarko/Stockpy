import numpy as np
import pandas as pd
import pytest

from sizing.hrp_cvar_optimizer import (
    compute_correlation_distance,
    quasi_diagonalization,
    recursive_bisection,
    calculate_cvar,
    constrain_cvar
)

def test_compute_correlation_distance():
    cov = pd.DataFrame([
        [1.0, 0.8, 0.0],
        [0.8, 1.0, -0.8],
        [0.0, -0.8, 1.0]
    ])
    dist = compute_correlation_distance(cov)
    
    # D_{i,i} should be 0
    np.testing.assert_almost_equal(np.diag(dist), np.zeros(3))
    
    # D_{0,1} = sqrt(0.5*(1-0.8)) = sqrt(0.1) ~ 0.31622
    expected_d_0_1 = np.sqrt(0.5 * (1 - 0.8))
    np.testing.assert_almost_equal(dist.iloc[0, 1], expected_d_0_1)
    
    # D_{1,2} = sqrt(0.5*(1 - (-0.8))) = sqrt(0.9) ~ 0.94868
    expected_d_1_2 = np.sqrt(0.5 * (1 + 0.8))
    np.testing.assert_almost_equal(dist.iloc[1, 2], expected_d_1_2)

def test_quasi_diagonalization():
    # Construct a distance matrix where 0 and 2 are closest, 1 is far
    dist = pd.DataFrame([
        [0.0, 0.9, 0.1],
        [0.9, 0.0, 0.8],
        [0.1, 0.8, 0.0]
    ])
    
    sort_ix = quasi_diagonalization(dist)
    # The cluster should group 0 and 2 together
    assert abs(sort_ix.index(0) - sort_ix.index(2)) == 1

def test_recursive_bisection():
    cov = pd.DataFrame([
        [0.04, 0.0, 0.0],
        [0.0, 0.01, 0.0],
        [0.0, 0.0, 0.09]
    ], index=['A', 'B', 'C'], columns=['A', 'B', 'C'])
    # sort_ix groups 0 and 1, then 2
    sort_ix = [0, 1, 2]
    
    w = recursive_bisection(cov, sort_ix)
    
    # They should sum to 1
    assert np.isclose(w.sum(), 1.0)
    
    # Manual calculation:
    # Group [0, 1] vs Group [2]
    # Cluster [0]: var = 0.04 -> ivp = 25
    # Cluster [1]: var = 0.01 -> ivp = 100
    # -> ivp_0 = 0.2, ivp_1 = 0.8
    # Group [0, 1] var = w.T * cov * w where w = [0.2, 0.8] -> 0.04 * 0.04 + 0.64 * 0.01 = 0.0016 + 0.0064 = 0.008
    # Group [2] var = 0.09
    # alpha = 1 - (0.008 / (0.008 + 0.09)) = 1 - 0.008/0.098 = 0.09 / 0.098 = 90/98 = 45/49
    # w[0, 1] = 45/49
    # w[2] = 4/49
    # w[0] = 45/49 * 0.2
    # w[1] = 45/49 * 0.8
    # Check w['C']:
    np.testing.assert_almost_equal(w.loc['C'], 4/49)
    np.testing.assert_almost_equal(w.loc['A'], 45/49 * 0.2)
    np.testing.assert_almost_equal(w.loc['B'], 45/49 * 0.8)

def test_calculate_cvar():
    np.random.seed(42)
    returns = np.random.normal(0, 0.01, size=(1000, 2))
    weights = np.array([0.5, 0.5])
    cvar = calculate_cvar(weights, returns, alpha=0.05)
    assert cvar > 0 # CVaR should be positive loss
    
    port_ret = np.dot(returns, weights)
    var = np.percentile(port_ret, 5)
    expected_cvar = -port_ret[port_ret <= var].mean()
    np.testing.assert_almost_equal(cvar, expected_cvar)

def test_constrain_cvar():
    np.random.seed(42)
    # Generate random returns
    returns = pd.DataFrame(np.random.normal(0.001, 0.02, size=(1000, 3)), columns=['A', 'B', 'C'])
    # create a large negative tail for asset A
    returns.iloc[0:100, 0] = -0.2
    
    initial_weights = pd.Series([0.5, 0.3, 0.2], index=['A', 'B', 'C'])
    # initial CVaR will be high because of Asset A's tail
    
    max_cvar = 0.04 # restrict CVaR to a feasible level
    
    w_new = constrain_cvar(returns, initial_weights, max_cvar, alpha=0.05)
    
    # They should sum to 1
    assert np.isclose(w_new.sum(), 1.0)
    
    # Calculate new CVaR
    port_ret = returns.values.dot(w_new.values)
    var = np.percentile(port_ret, 5)
    cvar = -port_ret[port_ret <= var].mean()
    
    # CVaR should be bounded
    assert cvar <= max_cvar + 1e-5
    
    # Weight of asset A should decrease significantly to meet the CVaR constraint
    assert w_new.loc['A'] < initial_weights.loc['A']

def test_constrain_cvar_no_change():
    np.random.seed(42)
    returns = pd.DataFrame(np.random.normal(0.001, 0.01, size=(1000, 3)), columns=['A', 'B', 'C'])
    initial_weights = pd.Series([0.33, 0.33, 0.34], index=['A', 'B', 'C'])
    
    # Provide a very high max_cvar so it's not constrained
    max_cvar = 0.5
    w_new = constrain_cvar(returns, initial_weights, max_cvar, alpha=0.05)
    
    # Should be almost exactly the same weights
    np.testing.assert_almost_equal(w_new.values, initial_weights.values, decimal=4)

def test_zero_variance_asset():
    cov = pd.DataFrame([
        [0.0, 0.0, 0.0],
        [0.0, 0.04, 0.01],
        [0.0, 0.01, 0.09]
    ], index=['Cash', 'A', 'B'], columns=['Cash', 'A', 'B'])
    dist = compute_correlation_distance(cov)
    assert not dist.isna().any().any()
    np.testing.assert_almost_equal(np.diag(dist), np.zeros(3))

def test_collinear_returns_singular_cov():
    cov = pd.DataFrame([
        [0.04, 0.04, 0.0],
        [0.04, 0.04, 0.0],
        [0.0, 0.0, 0.09]
    ], index=['A1', 'A2', 'B'], columns=['A1', 'A2', 'B'])
    dist = compute_correlation_distance(cov)
    assert not dist.isna().any().any()
    np.testing.assert_almost_equal(dist.loc['A1', 'A2'], 0.0)

def test_calculate_cvar_empty_returns():
    weights = np.array([0.5, 0.5])
    returns = np.empty((0, 2))
    cvar = calculate_cvar(weights, returns, alpha=0.05)
    assert cvar == 0.0

def test_degenerate_bisections():
    cov2 = pd.DataFrame([
        [0.04, 0.0],
        [0.0, 0.09]
    ], index=['A', 'B'], columns=['A', 'B'])
    w2 = recursive_bisection(cov2, [0, 1])
    assert np.isclose(w2.sum(), 1.0)
    
    cov4 = pd.DataFrame(np.eye(4), index=['A', 'B', 'C', 'D'], columns=['A', 'B', 'C', 'D'])
    w4 = recursive_bisection(cov4, [0, 1, 2, 3])
    assert np.isclose(w4.sum(), 1.0)
