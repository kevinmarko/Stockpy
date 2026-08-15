import numpy as np
import pytest
from execution.almgren_chriss_router import compute_trading_trajectory, calculate_efficient_frontier
import math
def test_twap_trajectory():
    """Test risk-neutral case which should reduce to TWAP."""
    res = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.1,
        temp_impact=0.01,
        perm_impact=0.001,
        risk_aversion=0.0
    )
    
    trade_list = res['trade_list']
    trajectory = res['trajectory']
    
    assert len(trade_list) == 10
    assert len(trajectory) == 11
    assert np.allclose(trade_list, 100.0)
    assert np.allclose(trajectory[-1], 0.0)
    assert np.allclose(trajectory[0], 1000.0)

def test_exponential_decay():
    """Test risk-averse case for exponential decay properties."""
    res = compute_trading_trajectory(
        total_shares=10000.0,
        total_time=5.0,
        n_intervals=50,
        volatility=0.2,
        temp_impact=0.05,
        perm_impact=0.002,
        risk_aversion=1e-4
    )
    
    trade_list = res['trade_list']
    
    # In Almgren-Chriss, risk aversion > 0 causes front-loading
    # Therefore, initial trades should be larger than later trades
    assert trade_list[0] > trade_list[-1]
    
    # Check that trade list is monotonically decreasing
    # We round slightly to handle floating point issues at the very tail
    diffs = np.diff(np.round(trade_list, 8))
    assert np.all(diffs <= 0)

def test_invalid_parameters():
    """Test parameter validation."""
    with pytest.raises(ValueError):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=-1.0,
            n_intervals=10,
            volatility=0.1,
            temp_impact=0.01,
            perm_impact=0.001,
            risk_aversion=0.0
        )
        
    with pytest.raises(ValueError):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=1.0,
            n_intervals=0,
            volatility=0.1,
            temp_impact=0.01,
            perm_impact=0.001,
            risk_aversion=0.0
        )
        
    with pytest.raises(ValueError):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=1.0,
            n_intervals=10,
            volatility=0.1,
            temp_impact=0.0, # Cannot be zero
            perm_impact=0.001,
            risk_aversion=0.0
        )

    with pytest.raises(ValueError):
        compute_trading_trajectory(
            total_shares=0.0,
            total_time=1.0,
            n_intervals=10,
            volatility=0.1,
            temp_impact=0.01,
            perm_impact=0.001,
            risk_aversion=0.0
        )

    with pytest.raises(ValueError):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=1.0,
            n_intervals=10,
            volatility=-0.1,
            temp_impact=0.01,
            perm_impact=0.001,
            risk_aversion=0.0
        )

    with pytest.raises(ValueError):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=1.0,
            n_intervals=10,
            volatility=0.1,
            temp_impact=0.01,
            perm_impact=-0.001,
            risk_aversion=0.0
        )

def test_shortfall_and_variance():
    """Test that shortfall and variance calculations make sense."""
    res_high_risk = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.2,
        temp_impact=0.01,
        perm_impact=0.001,
        risk_aversion=1.0
    )
    
    res_low_risk = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.2,
        temp_impact=0.01,
        perm_impact=0.001,
        risk_aversion=0.001
    )
    
    # Higher risk aversion leads to faster execution -> higher impact cost (shortfall), lower variance
    assert res_high_risk['expected_shortfall'] > res_low_risk['expected_shortfall']
    assert res_high_risk['variance'] < res_low_risk['variance']

def test_zero_volatility():
    """Test that zero volatility results in a TWAP trajectory."""
    res = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.0,
        temp_impact=0.01,
        perm_impact=0.001,
        risk_aversion=1.0  # Risk aversion > 0, but vol is 0
    )
    
    trade_list = res['trade_list']
    trajectory = res['trajectory']
    
    assert len(trade_list) == 10
    assert len(trajectory) == 11
    assert np.allclose(trade_list, 100.0)
    assert np.allclose(trajectory[-1], 0.0)
    assert np.allclose(trajectory[0], 1000.0)

def test_extreme_risk_aversion():
    """Test extreme risk aversion parameter does not overflow."""
    res_10 = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.1,
        temp_impact=0.01,
        perm_impact=0.001,
        risk_aversion=10.0
    )
    assert not math.isnan(res_10['expected_shortfall'])
    assert not math.isnan(res_10['variance'])
    
    res_1000 = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.1,
        temp_impact=0.01,
        perm_impact=0.001,
        risk_aversion=1000.0
    )
    assert not math.isnan(res_1000['expected_shortfall'])
    assert not math.isnan(res_1000['variance'])
    assert res_1000['trade_list'][0] > res_10['trade_list'][0]

def test_calculate_efficient_frontier():
    """Test that the efficient frontier function returns a valid list of dictionaries."""
    points = calculate_efficient_frontier(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=10,
        volatility=0.1,
        temp_impact=0.01,
        perm_impact=0.001,
        lambda_min=1e-8,
        lambda_max=1e-2,
        n_points=5
    )
    
    assert len(points) == 5
    for pt in points:
        assert "risk_aversion" in pt
        assert "expected_shortfall" in pt
        assert "variance" in pt
    
    # Frontier should show tradeoff: as risk aversion goes up, shortfall goes up and variance goes down
    assert points[-1]['expected_shortfall'] > points[0]['expected_shortfall']
    assert points[-1]['variance'] < points[0]['variance']
