import numpy as np
import pandas as pd
import pytest

from sizing.hrp_cvar_optimizer import (
    compute_correlation_distance,
    quasi_diagonalization,
    recursive_bisection,
    calculate_cvar,
    constrain_cvar,
    optimize_hrp_cvar,
    optimize_turnover_regularized_hrp_cvar,
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
    sort_ix = [0, 1, 2]
    
    w = recursive_bisection(cov, sort_ix)
    
    # They should sum to 1
    assert np.isclose(w.sum(), 1.0)
    
    # Manual calculation checks
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
    returns = pd.DataFrame(np.random.normal(0.001, 0.02, size=(1000, 3)), columns=['A', 'B', 'C'])
    returns.iloc[0:100, 0] = -0.2
    
    initial_weights = pd.Series([0.5, 0.3, 0.2], index=['A', 'B', 'C'])
    max_cvar = 0.04
    
    w_new = constrain_cvar(returns, initial_weights, max_cvar, alpha=0.05)
    
    assert np.isclose(w_new.sum(), 1.0)
    
    port_ret = returns.values.dot(w_new.values)
    var = np.percentile(port_ret, 5)
    cvar = -port_ret[port_ret <= var].mean()
    
    assert cvar <= max_cvar + 1e-5
    assert w_new.loc['A'] < initial_weights.loc['A']

def test_constrain_cvar_no_change():
    np.random.seed(42)
    returns = pd.DataFrame(np.random.normal(0.001, 0.01, size=(1000, 3)), columns=['A', 'B', 'C'])
    initial_weights = pd.Series([0.33, 0.33, 0.34], index=['A', 'B', 'C'])
    
    max_cvar = 0.5
    w_new = constrain_cvar(returns, initial_weights, max_cvar, alpha=0.05)
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

def test_optimize_hrp_cvar_helper():
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 3)),
        columns=['AAPL', 'MSFT', 'GOOGL']
    )
    w = optimize_hrp_cvar(returns)
    assert isinstance(w, pd.Series)
    assert np.isclose(w.sum(), 1.0)
    assert (w >= 0.0).all()

    # With max_cvar
    w_cvar = optimize_hrp_cvar(returns, max_cvar=0.05)
    assert np.isclose(w_cvar.sum(), 1.0)

def test_turnover_regularization_large_lambda():
    """
    When lambda_turnover is very large (100.0), optimal weights converge exactly to current_weights.
    """
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 4)),
        columns=['A', 'B', 'C', 'D']
    )
    current_weights = {'A': 0.15, 'B': 0.35, 'C': 0.25, 'D': 0.25}

    res = optimize_turnover_regularized_hrp_cvar(
        returns,
        current_weights=current_weights,
        lambda_turnover=100.0,
        max_weight=0.50,
        min_weight=0.0,
        target_beta_range=None,
    )

    assert res["status"] == "optimal"
    assert np.isclose(sum(res["weights"].values()), 1.0)
    for sym, expected_w in current_weights.items():
        assert np.isclose(res["weights"][sym], expected_w, atol=1e-3)
    assert res["turnover"] < 1e-3

def test_turnover_regularization_zero_vs_monotonic():
    """
    When lambda_turnover = 0, matches pure risk parity/CVaR allocation.
    As lambda increases, turnover monotonically decreases toward incumbent.
    """
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 3)),
        columns=['A', 'B', 'C']
    )
    current_weights = {'A': 0.60, 'B': 0.20, 'C': 0.20}

    res_zero = optimize_turnover_regularized_hrp_cvar(
        returns, current_weights=current_weights, lambda_turnover=0.0, target_beta_range=None
    )
    res_low = optimize_turnover_regularized_hrp_cvar(
        returns, current_weights=current_weights, lambda_turnover=0.05, target_beta_range=None
    )
    res_high = optimize_turnover_regularized_hrp_cvar(
        returns, current_weights=current_weights, lambda_turnover=10.0, target_beta_range=None
    )

    assert res_zero["status"] == "optimal"
    assert res_low["status"] == "optimal"
    assert res_high["status"] == "optimal"
    assert res_zero["turnover"] >= res_low["turnover"] - 1e-4
    assert res_low["turnover"] >= res_high["turnover"] - 1e-4
    assert res_high["turnover"] < res_zero["turnover"]

def test_sector_cap_constraints():
    """
    Assert no sector exceeds its configured cap.
    """
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 4)),
        columns=['AAPL', 'MSFT', 'JPM', 'XOM']
    )
    sector_map = {
        'AAPL': 'Tech',
        'MSFT': 'Tech',
        'JPM': 'Financials',
        'XOM': 'Energy',
    }
    sector_caps = {'Tech': 0.30, 'Financials': 0.50, 'Energy': 0.50}

    res = optimize_turnover_regularized_hrp_cvar(
        returns,
        sector_map=sector_map,
        sector_caps=sector_caps,
        target_beta_range=None,
    )

    assert res["status"] == "optimal"
    assert np.isclose(sum(res["weights"].values()), 1.0)
    for sec, cap in sector_caps.items():
        assert res["sector_exposures"].get(sec, 0.0) <= cap + 1e-4

def test_beta_interval_constraints():
    """
    Assert portfolio beta is within [beta_min, beta_max].
    """
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 3)),
        columns=['A', 'B', 'C']
    )
    asset_betas = {'A': 0.4, 'B': 1.4, 'C': 1.8}
    target_beta_range = (0.85, 1.15)

    res = optimize_turnover_regularized_hrp_cvar(
        returns,
        asset_betas=asset_betas,
        target_beta_range=target_beta_range,
    )

    assert res["status"] == "optimal"
    assert target_beta_range[0] - 1e-4 <= res["portfolio_beta"] <= target_beta_range[1] + 1e-4
    assert res["diversification_ratio"] >= 1.0

def test_concentration_caps():
    """
    Assert no single asset exceeds max_weight.
    """
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 5)),
        columns=['A', 'B', 'C', 'D', 'E']
    )
    max_weight = 0.25

    res = optimize_turnover_regularized_hrp_cvar(
        returns,
        max_weight=max_weight,
        min_weight=0.05,
        target_beta_range=None,
    )

    assert res["status"] == "optimal"
    for sym, w in res["weights"].items():
        assert w <= max_weight + 1e-4
        assert w >= 0.05 - 1e-4
    assert np.isclose(sum(res["weights"].values()), 1.0)

def test_graceful_degradation_infeasible():
    """
    When constraints are contradictory/infeasible, optimizer falls back gracefully.
    """
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.001, 0.02, size=(300, 3)),
        columns=['A', 'B', 'C']
    )
    sector_map = {'A': 'Tech', 'B': 'Tech', 'C': 'Tech'}
    # Impossible: All assets are Tech but cap is 20% while sum(w) must be 100%
    sector_caps = {'Tech': 0.20}

    res = optimize_turnover_regularized_hrp_cvar(
        returns,
        sector_map=sector_map,
        sector_caps=sector_caps,
        target_beta_range=None,
    )

    assert res["status"] == "fallback"
    assert np.isclose(sum(res["weights"].values()), 1.0)
    assert len(res["weights"]) == 3

def test_edge_cases_single_and_empty():
    """
    Edge cases: empty DataFrame and 1-asset portfolio.
    """
    empty_df = pd.DataFrame()
    res_empty = optimize_turnover_regularized_hrp_cvar(empty_df)
    assert res_empty["status"] == "fallback"
    assert res_empty["weights"] == {}
    assert res_empty["turnover"] == 0.0

    single_df = pd.DataFrame({'AAPL': np.random.normal(0.001, 0.02, size=100)})
    res_single = optimize_turnover_regularized_hrp_cvar(
        single_df, current_weights={'AAPL': 0.5}, asset_betas={'AAPL': 1.2}, sector_map={'AAPL': 'Tech'}
    )
    assert res_single["status"] == "optimal"
    assert res_single["weights"] == {'AAPL': 1.0}
    assert np.isclose(res_single["turnover"], 0.25)
    assert np.isclose(res_single["portfolio_beta"], 1.2)
    assert res_single["sector_exposures"] == {'Tech': 1.0}
    assert res_single["diversification_ratio"] == 1.0


