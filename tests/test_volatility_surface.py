"""
tests/test_volatility_surface.py — Tests for pilots/volatility_surface.py.
==========================================================================

Verifies:
- Black-Scholes pricing & IV inversion solver.
- Historical realized volatility calculation over rolling windows.
- Strike-dimension IV smile spline interpolation.
- 25-delta Put/Call Skew, Skew Ratio, and 25-delta Butterfly.
- Term structure variance-space interpolation (7d, 14d, 30d, 60d, 90d, 180d, 365d).
- Volatility Risk Premium (VRP) Cone across [10d, 20d, 30d, 60d].
- Graceful degradation for missing/illiquid quotes.
- AST import safety (zero imports of processing_engine or other heavy engines).
"""

import ast
from datetime import date, datetime, timezone
import math
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from pilots.volatility_surface import (
    calculate_volatility_surface,
    calculate_realized_volatility,
    interpolate_smile_spline,
    compute_term_structure,
    compute_25delta_skew,
    compute_vrp_cone,
    implied_volatility_black_scholes,
    _black_scholes_price,
    _black_scholes_delta,
    parse_expiration_slice,
    STANDARD_TERM_HORIZONS,
    STANDARD_VRP_WINDOWS,
)


def test_black_scholes_pricing_and_delta():
    spot = 100.0
    strike = 100.0
    t_years = 0.25
    sigma = 0.20
    r = 0.05

    call_price = _black_scholes_price(spot, strike, t_years, sigma, "call", r)
    put_price = _black_scholes_price(spot, strike, t_years, sigma, "put", r)

    # Put-Call Parity: C - P = S - K * exp(-rT)
    discount = strike * math.exp(-r * t_years)
    assert abs((call_price - put_price) - (spot - discount)) < 1e-4

    call_delta = _black_scholes_delta(spot, strike, t_years, sigma, "call", r)
    put_delta = _black_scholes_delta(spot, strike, t_years, sigma, "put", r)

    assert 0.50 < call_delta < 0.65
    assert -0.50 < put_delta < -0.35
    assert abs((call_delta - put_delta) - 1.0) < 1e-4


def test_implied_volatility_solver_accuracy():
    spot = 150.0
    strike = 155.0
    t_years = 45.0 / 365.0
    true_sigma = 0.285
    r = 0.045

    # Generate synthetic market call price
    call_mkt_price = _black_scholes_price(spot, strike, t_years, true_sigma, "call", r)
    recovered_iv = implied_volatility_black_scholes(call_mkt_price, spot, strike, t_years, "call", r)

    assert recovered_iv is not None
    assert abs(recovered_iv - true_sigma) < 1e-4

    # Generate synthetic market put price
    put_mkt_price = _black_scholes_price(spot, strike, t_years, true_sigma, "put", r)
    recovered_put_iv = implied_volatility_black_scholes(put_mkt_price, spot, strike, t_years, "put", r)

    assert recovered_put_iv is not None
    assert abs(recovered_put_iv - true_sigma) < 1e-4


def test_implied_volatility_solver_edge_cases():
    # Below intrinsic value
    assert implied_volatility_black_scholes(price=2.0, spot=100.0, strike=90.0, t_years=0.1, option_type="call") is None
    # Zero or negative price
    assert implied_volatility_black_scholes(price=0.0, spot=100.0, strike=100.0, t_years=0.1) is None
    assert implied_volatility_black_scholes(price=-1.5, spot=100.0, strike=100.0, t_years=0.1) is None
    # Zero time to maturity
    assert implied_volatility_black_scholes(price=5.0, spot=100.0, strike=100.0, t_years=0.0) is None


def test_calculate_realized_volatility():
    # Constant prices -> zero vol
    constant_prices = [100.0] * 30
    assert calculate_realized_volatility(constant_prices, window=20) == 0.0

    # Insufficient length (< window + 1)
    short_prices = [100.0, 101.0, 102.0]
    assert calculate_realized_volatility(short_prices, window=20) is None

    # Deterministic price series with known log return standard deviation
    np.random.seed(42)
    # 21 prices = 20 returns
    log_returns = np.random.normal(loc=0.0, scale=0.01, size=20)
    prices = [100.0]
    for r in log_returns:
        prices.append(prices[-1] * math.exp(r))

    rv = calculate_realized_volatility(prices, window=20)
    assert rv is not None
    expected_rv = np.std(log_returns, ddof=1) * math.sqrt(252.0)
    assert abs(rv - expected_rv) < 1e-4


def test_interpolate_smile_spline():
    spot = 100.0
    t_years = 30.0 / 365.0
    # Standard equity smile: higher IV on OTM puts (K < S)
    strikes = [85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0]
    ivs = [0.32, 0.28, 0.25, 0.22, 0.20, 0.19, 0.19]

    fit = interpolate_smile_spline(strikes, ivs, spot, t_years, n_grid=25)
    assert fit is not None
    assert fit["atm_iv"] == 0.22
    assert len(fit["curve"]) == 25

    # Check monotonicity in put wing
    spline_fn = fit["spline_fn"]
    assert spline_fn(88.0) > spline_fn(98.0)
    assert spline_fn(98.0) > spline_fn(108.0)


def test_compute_25delta_skew():
    spot = 100.0
    t_years = 30.0 / 365.0
    r = 0.045

    # Equity skew: put vol > call vol
    def asymmetric_spline(k: float) -> float:
        # Downward slope (typical equity skew)
        return 0.22 - 0.002 * (k - spot)

    skew_res = compute_25delta_skew(spot, t_years, asymmetric_spline, r=r)

    assert skew_res["skew_25d"] is not None
    assert skew_res["skew_25d"] > 0  # Put IV higher than Call IV
    assert skew_res["put_25d_iv"] > skew_res["call_25d_iv"]
    assert skew_res["put_25d_strike"] < spot
    assert skew_res["call_25d_strike"] > spot
    assert skew_res["skew_ratio"] > 1.0


def test_compute_term_structure_contango_and_backwardation():
    # 1. Contango structure (IV rises with maturity)
    contango_data = {
        "2026-08-21": {"dte": 7.0, "atm_iv": 0.18},
        "2026-08-28": {"dte": 14.0, "atm_iv": 0.19},
        "2026-09-18": {"dte": 35.0, "atm_iv": 0.22},
        "2026-11-20": {"dte": 98.0, "atm_iv": 0.25},
        "2027-02-19": {"dte": 189.0, "atm_iv": 0.27},
    }

    ts_contango = compute_term_structure(contango_data, STANDARD_TERM_HORIZONS)
    assert ts_contango["structure_regime"] == "contango"
    assert ts_contango["term_slope_30_90"] > 0
    assert len(ts_contango["points"]) == len(STANDARD_TERM_HORIZONS)

    # 2. Backwardation structure (front-month IV elevated, e.g. earnings or panic)
    backwardation_data = {
        "2026-08-21": {"dte": 7.0, "atm_iv": 0.35},
        "2026-08-28": {"dte": 14.0, "atm_iv": 0.32},
        "2026-09-18": {"dte": 35.0, "atm_iv": 0.26},
        "2026-11-20": {"dte": 98.0, "atm_iv": 0.22},
        "2027-02-19": {"dte": 189.0, "atm_iv": 0.20},
    }

    ts_backward = compute_term_structure(backwardation_data, STANDARD_TERM_HORIZONS)
    assert ts_backward["structure_regime"] == "backwardation"
    assert ts_backward["term_slope_30_90"] < 0


def test_compute_vrp_cone():
    term_points = [
        {"target_dte": 10, "iv": 0.25},
        {"target_dte": 20, "iv": 0.26},
        {"target_dte": 30, "iv": 0.27},
        {"target_dte": 60, "iv": 0.28},
    ]

    # Historical prices with realized volatility around 0.15 (15%)
    np.random.seed(123)
    rets = np.random.normal(0.0, 0.15 / math.sqrt(252), size=70)
    prices = [100.0]
    for r in rets:
        prices.append(prices[-1] * math.exp(r))

    cone = compute_vrp_cone(term_points, prices, STANDARD_VRP_WINDOWS)

    assert "10d" in cone
    assert "20d" in cone
    assert "30d" in cone
    assert "60d" in cone

    for window_key, entry in cone.items():
        assert entry["implied_vol"] is not None
        assert entry["realized_vol"] is not None
        assert entry["vrp"] is not None
        # Since IV (~0.25-0.28) > RV (~0.15), VRP > 0 and regime is premium_rich
        assert entry["vrp"] > 0.02
        assert entry["regime"] == "premium_rich"


def test_calculate_volatility_surface_full_pipeline():
    # Build synthetic multi-expiration options chain data
    as_of = date(2026, 8, 14)
    spot = 150.0

    expirations = ["2026-08-21", "2026-09-18", "2026-10-16"]
    chain_data = {}

    for exp in expirations:
        strikes = [135.0, 140.0, 145.0, 150.0, 155.0, 160.0, 165.0]
        calls = []
        puts = []
        for k in strikes:
            # Base IV with skew
            iv = 0.25 - 0.001 * (k - spot)
            calls.append({
                "strike": k,
                "bid": 2.0,
                "ask": 2.2,
                "lastPrice": 2.1,
                "impliedVolatility": iv,
                "volume": 100,
                "openInterest": 500,
            })
            puts.append({
                "strike": k,
                "bid": 2.0,
                "ask": 2.2,
                "lastPrice": 2.1,
                "impliedVolatility": iv + 0.02,  # slightly higher put IV
                "volume": 120,
                "openInterest": 600,
            })
        chain_data[exp] = {"calls": calls, "puts": puts}

    # Generate synthetic price history
    hist_prices = [150.0 + np.sin(i / 5.0) * 2.0 for i in range(100)]

    surface = calculate_volatility_surface(
        ticker="AAPL",
        chain_data=chain_data,
        spot_price=spot,
        historical_prices=hist_prices,
        as_of=as_of,
    )

    assert surface["ticker"] == "AAPL"
    assert surface["spot_price"] == 150.0
    assert surface["missing_data"] is False
    assert len(surface["expirations"]) == 3

    # Check smiles
    assert "2026-09-18" in surface["smiles"]
    smile_sep = surface["smiles"]["2026-09-18"]
    assert smile_sep["atm_iv"] > 0
    assert smile_sep["skew_25d"] is not None
    assert len(smile_sep["curve"]) > 0

    # Check term structure
    assert len(surface["term_structure"]["points"]) == len(STANDARD_TERM_HORIZONS)

    # Check skew summary
    assert surface["skew_summary"]["front_month_skew_25d"] is not None
    assert surface["skew_summary"]["average_skew_25d"] is not None

    # Check VRP cone
    assert "30d" in surface["vrp_cone"]
    assert surface["vrp_cone"]["30d"]["vrp"] is not None

    # Check 3D surface mesh
    assert len(surface["surface_grid"]) > 0


def test_calculate_volatility_surface_missing_data_graceful():
    # 1. Empty chain data
    empty_res = calculate_volatility_surface(ticker="XYZ", chain_data={}, spot_price=100.0)
    assert empty_res["missing_data"] is True
    assert empty_res["reason"] is not None
    assert empty_res["smiles"] == {}

    # 2. Missing spot price but inferable from chain strikes
    chain_with_no_spot = {
        "2026-09-18": {
            "calls": [
                {"strike": 90.0, "impliedVolatility": 0.25},
                {"strike": 100.0, "impliedVolatility": 0.22},
                {"strike": 110.0, "impliedVolatility": 0.20},
            ],
            "puts": [
                {"strike": 90.0, "impliedVolatility": 0.27},
                {"strike": 100.0, "impliedVolatility": 0.24},
                {"strike": 110.0, "impliedVolatility": 0.21},
            ],
        }
    }
    inferred_res = calculate_volatility_surface(ticker="XYZ", chain_data=chain_with_no_spot, spot_price=None)
    assert inferred_res["spot_price"] == 100.0  # Inferred median
    assert inferred_res["missing_data"] is False
    assert len(inferred_res["warnings"]) > 0

    # 3. Missing IV with market prices -> inverted via Black-Scholes
    chain_missing_iv = {
        "2026-09-18": {
            "calls": [
                {"strike": 100.0, "bid": 4.5, "ask": 4.7, "impliedVolatility": None},
                {"strike": 105.0, "bid": 2.1, "ask": 2.3, "impliedVolatility": None},
            ],
            "puts": [
                {"strike": 95.0, "bid": 2.0, "ask": 2.2, "impliedVolatility": None},
                {"strike": 100.0, "bid": 4.0, "ask": 4.2, "impliedVolatility": None},
            ],
        }
    }
    inverted_res = calculate_volatility_surface(ticker="XYZ", chain_data=chain_missing_iv, spot_price=100.0)
    assert inverted_res["missing_data"] is False
    assert "2026-09-18" in inverted_res["smiles"]


def test_volatility_surface_ast_import_safety():
    """Verifies that pilots/volatility_surface.py never imports processing_engine or heavy engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "volatility_surface.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="volatility_surface.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "engine.advisory",
        "main",
        "main_orchestrator",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert forbidden not in node.module, f"Forbidden import from found: {node.module}"
