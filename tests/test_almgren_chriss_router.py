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

def test_degenerate_cost_parameterization_raises():
    """AC(2001) requires eta_tilde = temp_impact - 0.5*perm_impact*tau > 0 for a well-posed
    convex cost function. A temp_impact too small relative to perm_impact*tau must raise
    rather than silently produce a nonsensical (possibly negative) expected_shortfall."""
    # tau = total_time / n_intervals = 1.0 / 1 = 1.0
    # threshold = 0.5 * perm_impact * tau = 0.5 * 1.0 * 1.0 = 0.5
    # temp_impact = 0.01 < 0.5 -> degenerate, must raise
    with pytest.raises(ValueError, match="well-posed"):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=1.0,
            n_intervals=1,
            volatility=0.1,
            temp_impact=0.01,
            perm_impact=1.0,
            risk_aversion=0.5
        )

    # Exactly at the boundary (eta_tilde == 0) is still degenerate (guard uses <=).
    with pytest.raises(ValueError, match="well-posed"):
        compute_trading_trajectory(
            total_shares=1000.0,
            total_time=1.0,
            n_intervals=1,
            volatility=0.1,
            temp_impact=0.5,
            perm_impact=1.0,
            risk_aversion=0.5
        )


def test_well_posed_cost_parameterization_just_above_threshold_does_not_raise():
    """A temp_impact just above the AC(2001) well-posedness threshold must NOT raise --
    guards against the new check over-triggering on legitimate parameterizations."""
    # Same tau=1.0, threshold=0.5 as above; temp_impact=0.51 clears it.
    res = compute_trading_trajectory(
        total_shares=1000.0,
        total_time=1.0,
        n_intervals=1,
        volatility=0.1,
        temp_impact=0.51,
        perm_impact=1.0,
        risk_aversion=0.5
    )
    assert not math.isnan(res["expected_shortfall"])


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


def test_risk_aversion_kappa_front_loading():
    """Verify that an increase in risk aversion lambda raises kappa = sqrt(lambda * sigma^2 / eta)
    and front-loads trades to shed inventory risk quickly."""
    sigma = 0.2
    eta = 0.05
    gamma = 0.002
    
    lambda_low = 1e-4
    lambda_high = 1e-2
    
    kappa_low = math.sqrt(lambda_low * (sigma ** 2) / eta)
    kappa_high = math.sqrt(lambda_high * (sigma ** 2) / eta)
    
    assert kappa_high > kappa_low
    
    res_low = compute_trading_trajectory(
        total_shares=10000.0,
        total_time=5.0,
        n_intervals=50,
        volatility=sigma,
        temp_impact=eta,
        perm_impact=gamma,
        risk_aversion=lambda_low,
    )
    
    res_high = compute_trading_trajectory(
        total_shares=10000.0,
        total_time=5.0,
        n_intervals=50,
        volatility=sigma,
        temp_impact=eta,
        perm_impact=gamma,
        risk_aversion=lambda_high,
    )
    
    # High risk aversion should trade more in the very first interval (front-loading)
    assert res_high['trade_list'][0] > res_low['trade_list'][0]
    # And leave less remaining inventory at mid-point
    assert res_high['trajectory'][25] < res_low['trajectory'][25]


# ---------------------------------------------------------------------------
# POST /pilots/execution/optimize/almgren-chriss -- `expected_price` must be
# computed off a real current spot price for the requested symbol, not the
# hardcoded `100.0` base the endpoint used to fall back to unconditionally
# (CONSTRAINT #4). The price lookup lives in `api/pilots_api.py` (the
# endpoint layer, via `pilots.price_provider.get_latest_price`), so these
# tests exercise it through the real FastAPI app.
# ---------------------------------------------------------------------------

from unittest import mock

from fastapi.testclient import TestClient

import api.pilots_api as pilots_api

_ac_client = TestClient(pilots_api.app, client=("127.0.0.1", 54127))


def test_almgren_chriss_endpoint_uses_real_spot_price_as_impact_base():
    """`expected_price` for every trajectory point must be derived from the
    REAL spot price returned by `pilots.price_provider.get_latest_price`,
    not the old hardcoded `100.0` base."""
    with mock.patch("pilots.price_provider.get_latest_price", return_value=250.0):
        resp = _ac_client.post(
            "/pilots/execution/optimize/almgren-chriss",
            json={"symbol": "AAPL", "quantity": 1000.0, "horizon_steps": 5},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["spot_price"] == 250.0
    assert body["spot_price_reason"] is None
    assert len(body["trajectory"]) == 5
    for pt in body["trajectory"]:
        assert pt["expected_price"] is not None
        # Impact-adjusted price must be anchored near the real 250.0 spot
        # price, not the old fabricated 100.0 base.
        assert 240.0 <= pt["expected_price"] <= 250.0


def test_almgren_chriss_endpoint_different_spot_price_changes_expected_price():
    """A different real spot price must produce a materially different
    `expected_price` -- proving the base price is genuinely read per-request
    rather than a disguised constant."""
    with mock.patch("pilots.price_provider.get_latest_price", return_value=50.0):
        resp_low = _ac_client.post(
            "/pilots/execution/optimize/almgren-chriss",
            json={"symbol": "LOWPRICE", "quantity": 1000.0, "horizon_steps": 5},
        )
    with mock.patch("pilots.price_provider.get_latest_price", return_value=500.0):
        resp_high = _ac_client.post(
            "/pilots/execution/optimize/almgren-chriss",
            json={"symbol": "HIGHPRICE", "quantity": 1000.0, "horizon_steps": 5},
        )

    assert resp_low.status_code == 200 and resp_high.status_code == 200
    price_low = resp_low.json()["trajectory"][0]["expected_price"]
    price_high = resp_high.json()["trajectory"][0]["expected_price"]
    assert price_low != price_high
    assert price_high > price_low + 100.0


def test_almgren_chriss_endpoint_degrades_honestly_when_no_live_quote():
    """CONSTRAINT #4: when no live quote is available for the symbol
    (`get_latest_price` returns 0.0, its documented "unavailable" sentinel),
    the endpoint must NEVER fall back to a fabricated base price -- every
    trajectory point's `expected_price` must be null, and the response must
    carry an honest `spot_price_reason` explaining why."""
    with mock.patch("pilots.price_provider.get_latest_price", return_value=0.0):
        resp = _ac_client.post(
            "/pilots/execution/optimize/almgren-chriss",
            json={"symbol": "NOPRICE", "quantity": 1000.0, "horizon_steps": 5},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["spot_price"] is None
    assert body["spot_price_reason"] is not None
    assert "NOPRICE" in body["spot_price_reason"]
    assert len(body["trajectory"]) == 5
    for pt in body["trajectory"]:
        assert pt["expected_price"] is None
    # The rest of the response (real math, not price-dependent) must still
    # be computed and returned -- degrading the price alone, not the whole
    # endpoint.
    assert body["expected_shortfall"] is not None
    assert body["variance"] is not None
    assert body["half_life"] is not None

