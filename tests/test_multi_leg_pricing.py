"""
tests/test_multi_leg_pricing.py
===============================
Unit tests for the Multi-Leg Options Pricing Engine and FastAPI endpoints:
- Black-Scholes Greeks with degenerate input guards (0DTE, zero-volatility, zero spot)
- Strategy validation for Iron Condors, Vertical Spreads, Straddles, and Strangles
- Multi-leg pricing, composite Greeks, max profit/loss, break-even detection, and payoff curves
- FastAPI endpoint contracts and AST import safety
"""

import ast
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from pilots.multi_leg_pricing import (
    OptionLegSpec,
    calculate_black_scholes_leg_greeks,
    parse_dte_to_years,
    price_multi_leg_structure,
    validate_multi_leg_structure,
)
from pilots.options_risk import calculate_black_scholes_greeks


# ===========================================================================
# 1. Black-Scholes Greeks & Numerical Safeguards
# ===========================================================================

def test_black_scholes_standard_call_and_put():
    """Validates Black-Scholes pricing for standard ATM call and put."""
    spot = 100.0
    strike = 100.0
    t_years = 30.0 / 252.0
    sigma = 0.20
    r = 0.05

    call_greeks = calculate_black_scholes_leg_greeks(spot, strike, t_years, sigma, "call", r)
    put_greeks = calculate_black_scholes_leg_greeks(spot, strike, t_years, sigma, "put", r)

    # Standard call and put price checks
    assert call_greeks["price"] > 0.0
    assert put_greeks["price"] > 0.0
    assert 0.4 < call_greeks["delta"] < 0.6
    assert -0.6 < put_greeks["delta"] < -0.4
    assert call_greeks["gamma"] > 0.0
    assert call_greeks["gamma"] == pytest.approx(put_greeks["gamma"], rel=1e-3)
    assert call_greeks["theta_daily"] < 0.0
    assert put_greeks["theta_daily"] < 0.0
    assert call_greeks["vega_1pct"] > 0.0
    assert put_greeks["vega_1pct"] > 0.0


def test_black_scholes_0dte_intrinsic_fallback():
    """Validates 0DTE fallback when T <= 1e-12."""
    spot = 105.0
    strike = 100.0

    # ITM Call at 0DTE
    call_itm = calculate_black_scholes_leg_greeks(spot, strike, t_years=0.0, sigma=0.20, option_type="call")
    assert call_itm["price"] == pytest.approx(5.0)
    assert call_itm["delta"] == 1.0
    assert call_itm["gamma"] == 0.0
    assert call_itm["theta_daily"] == 0.0
    assert call_itm["vega_1pct"] == 0.0

    # OTM Put at 0DTE
    put_otm = calculate_black_scholes_leg_greeks(spot, strike, t_years=0.0, sigma=0.20, option_type="put")
    assert put_otm["price"] == 0.0
    assert put_otm["delta"] == 0.0
    assert put_otm["gamma"] == 0.0


def test_black_scholes_zero_volatility_guard():
    """Validates zero/degenerate volatility fallback."""
    spot = 100.0
    strike = 95.0

    call_itm = calculate_black_scholes_leg_greeks(spot, strike, t_years=0.1, sigma=0.0, option_type="call")
    assert call_itm["price"] == pytest.approx(5.0)
    assert call_itm["delta"] == 1.0
    assert call_itm["gamma"] == 0.0


def test_black_scholes_non_positive_inputs():
    """Validates spot <= 0 or strike <= 0 returns zero Greeks without throwing."""
    res = calculate_black_scholes_leg_greeks(spot=0.0, strike=100.0, t_years=0.1, sigma=0.2)
    assert res["price"] == 0.0
    assert res["delta"] == 0.0


def test_black_scholes_leg_greeks_delegates_to_canonical_pricer():
    """F4 dedup (docs/module_efficiency_redundancy_audit.md):
    calculate_black_scholes_leg_greeks now delegates to
    pilots.options_risk.calculate_black_scholes_greeks instead of carrying
    its own near-verbatim copy. Seeded grid across standard, 0DTE, and
    degenerate-vol inputs proves this module's 5-key return contract
    (price/delta/gamma/theta_daily/vega_1pct) is byte-identical to a subset
    of the canonical function's own return dict, not merely "close" --
    matching this repo's precedent of proving numeric equivalence before a
    migration lands (see the ETF-transmission flag-off parity proof)."""
    grid = [
        # (spot, strike, t_years, sigma, option_type)
        (100.0, 100.0, 0.25, 0.20, "call"),
        (100.0, 100.0, 0.25, 0.20, "put"),
        (150.0, 95.0, 1.0, 0.35, "call"),
        (50.0, 60.0, 0.5, 0.50, "put"),
        (100.0, 100.0, 0.0, 0.20, "call"),  # 0DTE
        (100.0, 95.0, 0.1, 0.0, "call"),  # degenerate sigma
        (0.0, 100.0, 0.1, 0.2, "call"),  # non-positive spot
    ]
    for spot, strike, t_years, sigma, option_type in grid:
        leg_result = calculate_black_scholes_leg_greeks(spot, strike, t_years, sigma, option_type)
        canonical_result = calculate_black_scholes_greeks(spot, strike, t_years, sigma, option_type)
        for key in ("price", "delta", "gamma", "theta_daily", "vega_1pct"):
            assert leg_result[key] == pytest.approx(canonical_result[key], abs=1e-12), (
                f"{key} diverged for spot={spot} strike={strike} t_years={t_years} "
                f"sigma={sigma} option_type={option_type}"
            )


# ===========================================================================
# 2. Multi-Leg Structure Validation
# ===========================================================================

def test_validate_iron_condor_valid():
    """Validates a structurally sound 4-leg Iron Condor."""
    legs = [
        OptionLegSpec(strike=90.0, option_type="put", action="buy", expiration="2026-09-18"),
        OptionLegSpec(strike=95.0, option_type="put", action="sell", expiration="2026-09-18"),
        OptionLegSpec(strike=105.0, option_type="call", action="sell", expiration="2026-09-18"),
        OptionLegSpec(strike=110.0, option_type="call", action="buy", expiration="2026-09-18"),
    ]
    is_valid, errors = validate_multi_leg_structure("IRON_CONDOR", legs)
    assert is_valid is True
    assert errors == []


def test_validate_iron_condor_invalid_wing_ordering():
    """Validates rejection when Put strikes overlap or exceed Call strikes."""
    legs = [
        OptionLegSpec(strike=90.0, option_type="put", action="buy"),
        OptionLegSpec(strike=115.0, option_type="put", action="sell"),  # Inverted strike
        OptionLegSpec(strike=105.0, option_type="call", action="sell"),
        OptionLegSpec(strike=110.0, option_type="call", action="buy"),
    ]
    is_valid, errors = validate_multi_leg_structure("IRON_CONDOR", legs)
    assert is_valid is False
    assert any("Put strikes < Call strikes" in err for err in errors)


def test_validate_vertical_spread_valid_and_invalid():
    """Validates Vertical Spread rules (2 legs, same type, 1 buy, 1 sell)."""
    # Valid Bull Call Spread
    valid_spread = [
        OptionLegSpec(strike=100.0, option_type="call", action="buy"),
        OptionLegSpec(strike=105.0, option_type="call", action="sell"),
    ]
    is_valid, errors = validate_multi_leg_structure("VERTICAL_SPREAD", valid_spread)
    assert is_valid is True
    assert errors == []

    # Invalid: Same strike
    same_strike = [
        OptionLegSpec(strike=100.0, option_type="call", action="buy"),
        OptionLegSpec(strike=100.0, option_type="call", action="sell"),
    ]
    is_valid, errors = validate_multi_leg_structure("VERTICAL_SPREAD", same_strike)
    assert is_valid is False
    assert any("different strike prices" in err for err in errors)


def test_validate_straddle_and_strangle():
    """Validates Straddle (same strike) and Strangle (put < call) validation."""
    # Valid Straddle
    straddle = [
        OptionLegSpec(strike=100.0, option_type="call", action="buy"),
        OptionLegSpec(strike=100.0, option_type="put", action="buy"),
    ]
    is_valid, _ = validate_multi_leg_structure("STRADDLE", straddle)
    assert is_valid is True

    # Valid Strangle
    strangle = [
        OptionLegSpec(strike=95.0, option_type="put", action="buy"),
        OptionLegSpec(strike=105.0, option_type="call", action="buy"),
    ]
    is_valid, _ = validate_multi_leg_structure("STRANGLE", strangle)
    assert is_valid is True


# ===========================================================================
# 3. Multi-Leg Pricing & Payoff Calculations
# ===========================================================================

def test_price_iron_condor_net_credit_and_breakevens():
    """Tests pricing and break-even resolution for a net credit Iron Condor."""
    spot = 100.0
    legs = [
        OptionLegSpec(strike=90.0, option_type="put", action="buy", premium=0.50),
        OptionLegSpec(strike=95.0, option_type="put", action="sell", premium=1.50),
        OptionLegSpec(strike=105.0, option_type="call", action="sell", premium=1.50),
        OptionLegSpec(strike=110.0, option_type="call", action="buy", premium=0.50),
    ]

    res = price_multi_leg_structure(spot=spot, legs=legs, default_iv=0.25)

    assert res["net_order_action"] == "CREDIT"
    # Net credit = (1.50 - 0.50) + (1.50 - 0.50) = 2.00 per share ($200 per contract)
    assert res["net_cashflow_per_contract"] == pytest.approx(200.0)
    assert res["is_defined_risk"] is True
    # Max profit = $200
    assert res["max_profit"] == pytest.approx(200.0)
    # Max loss = Width (5) - Credit (2) = 3 per share ($300 per contract)
    assert res["max_loss"] == pytest.approx(300.0)
    # Break-evens: Lower = 95 - 2 = 93; Upper = 105 + 2 = 107
    assert len(res["breakeven_points"]) == 2
    assert res["breakeven_points"][0] == pytest.approx(93.0, abs=0.5)
    assert res["breakeven_points"][1] == pytest.approx(107.0, abs=0.5)
    assert len(res["payoff_curve"]) == 100


def test_price_bull_call_spread_net_debit():
    """Tests pricing and break-even resolution for a net debit Bull Call Spread."""
    spot = 100.0
    legs = [
        OptionLegSpec(strike=100.0, option_type="call", action="buy", premium=3.00),
        OptionLegSpec(strike=105.0, option_type="call", action="sell", premium=1.00),
    ]

    res = price_multi_leg_structure(spot=spot, legs=legs, default_iv=0.25)

    assert res["net_order_action"] == "DEBIT"
    assert res["net_cashflow_per_contract"] == pytest.approx(-200.0)
    assert res["max_profit"] == pytest.approx(300.0)  # (5 - 2) * 100
    assert res["max_loss"] == pytest.approx(200.0)    # 2 * 100
    # Break-even = 100 + 2 = 102
    assert len(res["breakeven_points"]) == 1
    assert res["breakeven_points"][0] == pytest.approx(102.0, abs=0.5)


# ===========================================================================
# 4. FastAPI Endpoint Integration Tests
# ===========================================================================

def test_api_multi_leg_pricing_endpoint(monkeypatch):
    """Tests POST /pilots/options/multi-leg/price."""
    from api.pilots_api import app
    from settings import settings

    # This endpoint is gated by require_read_token (STATE_API_TOKEN). No
    # Authorization header is sent below, relying on the
    # fail-open-on-loopback path -- pinned explicitly so it doesn't depend
    # on the machine's real .env leaving STATE_API_TOKEN unset.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", None)

    client = TestClient(app, client=("127.0.0.1", 54123))
    payload = {
        "symbol": "AAPL",
        "structure_type": "IRON_CONDOR",
        "underlying_price": 150.0,
        "iv_override": 0.25,
        "legs": [
            {"strike": 140.0, "option_type": "put", "action": "buy", "premium": 1.0},
            {"strike": 145.0, "option_type": "put", "action": "sell", "premium": 2.5},
            {"strike": 155.0, "option_type": "call", "action": "sell", "premium": 2.5},
            {"strike": 160.0, "option_type": "call", "action": "buy", "premium": 1.0},
        ],
    }

    resp = client.post("/pilots/options/multi-leg/price", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "AAPL"
    assert data["net_order_action"] == "CREDIT"
    assert "composite_greeks" in data
    assert "payoff_curve" in data
    assert len(data["payoff_curve"]) > 0


def test_api_multi_leg_validation_endpoint(monkeypatch):
    """Tests POST /pilots/options/multi-leg/validate."""
    from api.pilots_api import app
    from settings import settings

    # Gated by require_read_token (STATE_API_TOKEN); see the pricing
    # endpoint test above for why this is pinned explicitly.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", None)

    client = TestClient(app, client=("127.0.0.1", 54123))
    # Valid vertical spread
    resp = client.post(
        "/pilots/options/multi-leg/validate",
        json={
            "structure_type": "VERTICAL_SPREAD",
            "legs": [
                {"strike": 100.0, "option_type": "call", "action": "buy"},
                {"strike": 105.0, "option_type": "call", "action": "sell"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is True

    # Invalid: non-positive strike triggers Pydantic 422
    resp_invalid = client.post(
        "/pilots/options/multi-leg/validate",
        json={
            "structure_type": "VERTICAL_SPREAD",
            "legs": [
                {"strike": -10.0, "option_type": "call", "action": "buy"},
                {"strike": 105.0, "option_type": "call", "action": "sell"},
            ],
        },
    )
    assert resp_invalid.status_code == 422


# ===========================================================================
# 5. AST Import Safety Validation
# ===========================================================================

def test_multi_leg_pricing_module_never_imports_heavy_engines():
    """Verifies pilots/multi_leg_pricing.py never imports forbidden heavy engines."""
    file_path = Path("pilots/multi_leg_pricing.py")
    assert file_path.exists()

    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    forbidden = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                assert root_pkg not in forbidden, f"Forbidden direct import '{alias.name}' in pilots/multi_leg_pricing.py"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in forbidden, f"Forbidden from-import '{node.module}' in pilots/multi_leg_pricing.py"
