"""
tests/test_vol_mispricing.py — Comprehensive Test Suite for Volatility Mispricing Scanner.
==========================================================================================

Validates:
1. AST import boundary safety (no heavy engine imports).
2. Black-Scholes pricing, Greeks, and IV inversion with degenerate guards.
3. Volatility mispricing spread math and Rich / Cheap / Neutral classification.
4. Input container ingestion (DataFrame, dicts, yfinance-style objects, lists).
5. Fair IV resolution (scalars, mappings, callables, objects).
6. Overvalued (Rich) strikes -> Credit Spreads & Delta-Neutral Iron Condor trade construction.
7. Undervalued (Cheap) strikes -> Debit Spreads & Long Straddle/Strangle convexity trade construction.
8. Robustness & edge cases (empty chains, invalid spot, missing quotes, single strike).
9. Serialization and DTO dictionary compatibility.
"""

import ast
import math
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from pilots.vol_mispricing import (
    DEFAULT_CHEAP_VOL_THRESHOLD,
    DEFAULT_RICH_VOL_THRESHOLD,
    CandidateStrategyTrade,
    MispricingAnalysis,
    MispricingSummary,
    StrategyLeg,
    StrikeMispricingRecord,
    build_candidate_strategy_trades,
    calculate_black_scholes_greeks_and_price,
    calculate_strike_mispricing_spread,
    classify_strike_mispricing,
    evaluate_strike_mispricing,
    extract_chain_contracts,
    implied_volatility_from_price,
)


# ---------------------------------------------------------------------------
# 1. AST Import Safety Test
# ---------------------------------------------------------------------------


def test_vol_mispricing_ast_import_safety():
    """Verifies that pilots/vol_mispricing.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "vol_mispricing.py"
    assert file_path.exists(), f"File {file_path} not found"

    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="vol_mispricing.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
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


# ---------------------------------------------------------------------------
# 2. Black-Scholes Pricing & IV Inversion
# ---------------------------------------------------------------------------


def test_black_scholes_pricing_and_greeks():
    """Tests Black-Scholes theoretical pricing and Greek calculations."""
    spot = 100.0
    strike = 100.0
    t_years = 30.0 / 365.0
    sigma = 0.25
    rate = 0.045

    call_res = calculate_black_scholes_greeks_and_price(spot, strike, t_years, sigma, "call", rate)
    assert call_res["price"] is not None and call_res["price"] > 0
    assert 0.45 <= call_res["delta"] <= 0.60
    assert call_res["gamma"] > 0
    assert call_res["vega"] > 0
    assert call_res["theta"] < 0

    put_res = calculate_black_scholes_greeks_and_price(spot, strike, t_years, sigma, "put", rate)
    assert put_res["price"] is not None and put_res["price"] > 0
    assert -0.55 <= put_res["delta"] <= -0.40
    assert put_res["gamma"] > 0
    assert put_res["vega"] > 0

    # Put-Call Parity: C - P = S - K * exp(-r*T)
    disc_k = strike * math.exp(-rate * t_years)
    parity_diff = abs((call_res["price"] - put_res["price"]) - (spot - disc_k))
    assert parity_diff < 0.05


def test_black_scholes_degenerate_guards():
    """Tests degenerate and boundary inputs in Black-Scholes calculation."""
    # Zero or negative spot/strike
    res_zero = calculate_black_scholes_greeks_and_price(0.0, 100.0, 0.1, 0.20)
    assert res_zero["price"] == 0.0
    assert res_zero["delta"] == 0.0

    # 0DTE (T <= 0)
    res_0dte_call = calculate_black_scholes_greeks_and_price(105.0, 100.0, 0.0, 0.20, "call")
    assert res_0dte_call["price"] == 5.0
    assert res_0dte_call["delta"] == 1.0

    res_0dte_put = calculate_black_scholes_greeks_and_price(95.0, 100.0, 0.0, 0.20, "put")
    assert res_0dte_put["price"] == 5.0
    assert res_0dte_put["delta"] == -1.0


def test_implied_volatility_inversion():
    """Tests inversion of market option price back to implied volatility."""
    spot = 150.0
    strike = 150.0
    t_years = 45.0 / 365.0
    true_sigma = 0.32
    rate = 0.045

    call_price = calculate_black_scholes_greeks_and_price(spot, strike, t_years, true_sigma, "call", rate)["price"]
    assert call_price is not None and call_price > 0

    recovered_iv = implied_volatility_from_price(call_price, spot, strike, t_years, "call", rate)
    assert recovered_iv is not None
    assert pytest.approx(recovered_iv, abs=0.005) == true_sigma

    # Below intrinsic price -> should return None safely
    assert implied_volatility_from_price(1.0, 150.0, 100.0, t_years, "call", rate) is None


# ---------------------------------------------------------------------------
# 3. Spread Math & Classification
# ---------------------------------------------------------------------------


def test_calculate_strike_mispricing_spread():
    """Tests spread and percentage calculations."""
    # Overvalued strike (+3.0% vol points)
    spread, spread_pct = calculate_strike_mispricing_spread(0.28, 0.25)
    assert spread == 0.03
    assert spread_pct == 0.12

    # Undervalued strike (-5.0% vol points)
    spread, spread_pct = calculate_strike_mispricing_spread(0.20, 0.25)
    assert spread == -0.05
    assert spread_pct == -0.20

    # Missing / NaN inputs
    assert calculate_strike_mispricing_spread(None, 0.25) == (None, None)
    assert calculate_strike_mispricing_spread(0.25, None) == (None, None)
    assert calculate_strike_mispricing_spread(float("nan"), 0.25) == (None, None)


def test_classify_strike_mispricing():
    """Tests classification of spreads into RICH, CHEAP, NEUTRAL, and UNKNOWN."""
    assert classify_strike_mispricing(0.04) == "RICH"
    assert classify_strike_mispricing(0.03) == "RICH"
    assert classify_strike_mispricing(0.029) == "NEUTRAL"
    assert classify_strike_mispricing(0.00) == "NEUTRAL"
    assert classify_strike_mispricing(-0.029) == "NEUTRAL"
    assert classify_strike_mispricing(-0.03) == "CHEAP"
    assert classify_strike_mispricing(-0.06) == "CHEAP"
    assert classify_strike_mispricing(None) == "UNKNOWN"


# ---------------------------------------------------------------------------
# 4. Chain Extraction from Diverse Formats
# ---------------------------------------------------------------------------


def test_extract_chain_contracts_dataframe():
    """Tests extracting contracts from pandas DataFrame."""
    df = pd.DataFrame([
        {"strike": 100.0, "option_type": "call", "bid": 2.50, "ask": 2.70, "market_iv": 0.28},
        {"strike": 100.0, "option_type": "put", "bid": 2.10, "ask": 2.30, "market_iv": 0.29},
    ])
    contracts = extract_chain_contracts(df)
    assert len(contracts) == 2
    assert contracts[0]["strike"] == 100.0
    assert contracts[0]["option_type"] == "call"
    assert contracts[0]["market_iv"] == 0.28
    assert contracts[1]["option_type"] == "put"


def test_extract_chain_contracts_object():
    """Tests extracting contracts from object with .calls and .puts."""
    class MockChain:
        calls = [
            {"strike": 105.0, "bid": 1.50, "ask": 1.60, "impliedVolatility": 0.24},
        ]
        puts = pd.DataFrame([
            {"strike": 95.0, "bid": 1.20, "ask": 1.30, "impliedVolatility": 0.31},
        ])

    chain = MockChain()
    contracts = extract_chain_contracts(chain)
    assert len(contracts) == 2
    assert {c["strike"] for c in contracts} == {95.0, 105.0}


def test_extract_chain_contracts_dict():
    """Tests extracting contracts from dictionary containers."""
    dict_calls_puts = {
        "calls": [{"strike": 110.0, "bid": 0.80, "ask": 0.90, "iv": 0.22}],
        "puts": [{"strike": 90.0, "bid": 0.70, "ask": 0.80, "iv": 0.33}],
    }
    c1 = extract_chain_contracts(dict_calls_puts)
    assert len(c1) == 2

    dict_options_list = {
        "options": [
            {"strike": 100.0, "option_type": "call", "bid": 3.0, "ask": 3.2, "iv": 0.26}
        ]
    }
    c2 = extract_chain_contracts(dict_options_list)
    assert len(c2) == 1
    assert c2[0]["strike"] == 100.0


# ---------------------------------------------------------------------------
# 5. Overvalued (Rich) Strikes -> Credit Spreads & Iron Condor
# ---------------------------------------------------------------------------


def test_evaluate_strike_mispricing_rich_overvalued():
    """
    Simulates an option chain where OTM calls and puts have high implied volatility
    relative to fair forecast (Spread >= +0.03) and verifies Credit Spread & Iron Condor generation.
    """
    spot = 100.0
    fair_iv = 0.20  # Model fair IV is 20%
    exp_str = "2026-09-18"
    dte = 30.0

    # Chain with rich OTM put (strike 95: IV 0.26 -> spread +0.06)
    # and rich OTM call (strike 105: IV 0.25 -> spread +0.05)
    # along with wings (strike 90 and strike 110)
    chain = [
        {"strike": 90.0, "option_type": "put", "market_iv": 0.23, "bid": 0.80, "ask": 0.90, "expiration": exp_str, "dte": dte},
        {"strike": 95.0, "option_type": "put", "market_iv": 0.26, "bid": 2.00, "ask": 2.10, "expiration": exp_str, "dte": dte},
        {"strike": 100.0, "option_type": "put", "market_iv": 0.21, "bid": 3.50, "ask": 3.60, "expiration": exp_str, "dte": dte},
        {"strike": 100.0, "option_type": "call", "market_iv": 0.21, "bid": 3.70, "ask": 3.80, "expiration": exp_str, "dte": dte},
        {"strike": 105.0, "option_type": "call", "market_iv": 0.25, "bid": 1.90, "ask": 2.00, "expiration": exp_str, "dte": dte},
        {"strike": 110.0, "option_type": "call", "market_iv": 0.22, "bid": 0.70, "ask": 0.80, "expiration": exp_str, "dte": dte},
    ]

    analysis = evaluate_strike_mispricing(
        chain_data=chain,
        spot_price=spot,
        fair_iv_forecast=fair_iv,
        symbol="XYZ",
    )

    assert analysis.spot_price == 100.0
    assert analysis.symbol == "XYZ"
    assert len(analysis.strikes) == 6

    # Verify Summary
    summary = analysis.summary
    assert summary["rich_strikes_count"] >= 2
    assert summary["regime"] == "OVERVALUED_VOLATILITY"
    assert summary["max_rich_spread"] is not None and summary["max_rich_spread"] >= 0.05

    # Verify Candidate Trades
    trades = analysis.candidate_trades
    assert len(trades) > 0

    types = [t["strategy_type"] for t in trades]
    assert "iron_condor" in types
    assert "bull_put_spread" in types
    assert "bear_call_spread" in types

    # Check Iron Condor specifics
    condor = next(t for t in trades if t["strategy_type"] == "iron_condor")
    assert condor["is_credit"] is True
    assert condor["net_premium"] < 0  # Net credit
    assert condor["max_profit"] > 0
    assert condor["max_loss"] > 0
    assert len(condor["legs"]) == 4
    assert len(condor["breakeven_points"]) == 2
    assert condor["edge_type"] == "RICH_VOLATILITY_HARVEST"

    # Check Bull Put Credit Spread specifics
    bull_put = next(t for t in trades if t["strategy_type"] == "bull_put_spread")
    assert bull_put["is_credit"] is True
    assert bull_put["net_premium"] < 0
    assert len(bull_put["legs"]) == 2
    assert bull_put["max_profit"] > 0
    assert bull_put["max_loss"] > 0


# ---------------------------------------------------------------------------
# 6. Undervalued (Cheap) Strikes -> Debit Spreads & Long Straddle/Strangle
# ---------------------------------------------------------------------------


def test_evaluate_strike_mispricing_cheap_undervalued():
    """
    Simulates an option chain where strikes have low implied volatility
    relative to fair forecast (Spread <= -0.03) and verifies Debit Spread & Convexity generation.
    """
    spot = 100.0
    fair_iv = 0.30  # Model fair IV is 30%
    exp_str = "2026-09-18"
    dte = 30.0

    # Chain with cheap calls (IV 0.22 -> spread -0.08) and cheap puts (IV 0.23 -> spread -0.07)
    chain = [
        {"strike": 95.0, "option_type": "put", "market_iv": 0.26, "bid": 1.20, "ask": 1.30, "expiration": exp_str, "dte": dte},
        {"strike": 100.0, "option_type": "put", "market_iv": 0.23, "bid": 2.40, "ask": 2.50, "expiration": exp_str, "dte": dte},
        {"strike": 100.0, "option_type": "call", "market_iv": 0.22, "bid": 2.60, "ask": 2.70, "expiration": exp_str, "dte": dte},
        {"strike": 105.0, "option_type": "call", "market_iv": 0.25, "bid": 1.10, "ask": 1.20, "expiration": exp_str, "dte": dte},
    ]

    analysis = evaluate_strike_mispricing(
        chain_data=chain,
        spot_price=spot,
        fair_iv_forecast=fair_iv,
        symbol="ABC",
    )

    summary = analysis.summary
    assert summary["cheap_strikes_count"] >= 2
    assert summary["regime"] == "UNDERVALUED_VOLATILITY"
    assert summary["max_cheap_spread"] is not None and summary["max_cheap_spread"] <= -0.05

    trades = analysis.candidate_trades
    assert len(trades) > 0

    types = [t["strategy_type"] for t in trades]
    assert any(t in types for t in ["bull_call_spread", "bear_put_spread", "long_straddle", "long_strangle"])

    # Check Bull Call Debit Spread specifics
    if "bull_call_spread" in types:
        bull_call = next(t for t in trades if t["strategy_type"] == "bull_call_spread")
        assert bull_call["is_credit"] is False
        assert bull_call["net_premium"] > 0  # Net debit
        assert bull_call["max_profit"] > 0
        assert bull_call["max_loss"] > 0
        assert bull_call["edge_type"] == "CHEAP_CONVEXITY_CAPTURE"

    # Check Long Straddle / Strangle specifics
    if "long_straddle" in types or "long_strangle" in types:
        long_vol = next(t for t in trades if "straddle" in t["strategy_type"] or "strangle" in t["strategy_type"])
        assert long_vol["is_credit"] is False
        assert long_vol["net_premium"] > 0
        assert long_vol["net_vega"] > 0  # Long vega
        assert len(long_vol["breakeven_points"]) == 2


# ---------------------------------------------------------------------------
# 7. Fair IV Forecast Format Polymorphism
# ---------------------------------------------------------------------------


def test_evaluate_strike_mispricing_fair_iv_formats():
    """Tests that fair_iv_forecast accepts scalar, dict mapping, callable, and object."""
    chain = [
        {"strike": 100.0, "option_type": "call", "market_iv": 0.28, "bid": 3.0, "ask": 3.2},
        {"strike": 105.0, "option_type": "call", "market_iv": 0.25, "bid": 1.5, "ask": 1.6},
    ]

    # 1. Scalar float
    a1 = evaluate_strike_mispricing(chain, 100.0, fair_iv_forecast=0.25)
    assert a1.strikes[0]["fair_iv"] == 0.25
    assert a1.strikes[0]["spread"] == 0.03
    assert a1.strikes[0]["valuation_tag"] == "RICH"

    # 2. Dict mapping strike -> fair_iv
    a2 = evaluate_strike_mispricing(chain, 100.0, fair_iv_forecast={100.0: 0.30, 105.0: 0.20})
    assert a2.strikes[0]["fair_iv"] == 0.30
    assert a2.strikes[0]["spread"] == -0.02
    assert a2.strikes[1]["fair_iv"] == 0.20
    assert a2.strikes[1]["spread"] == 0.05

    # 3. Callable f(strike)
    a3 = evaluate_strike_mispricing(chain, 100.0, fair_iv_forecast=lambda k: 0.22 if k == 100.0 else 0.26)
    assert a3.strikes[0]["fair_iv"] == 0.22
    assert a3.strikes[0]["spread"] == 0.06

    # 4. Object with .fair_iv
    class MockForecast:
        fair_iv = 0.24

    a4 = evaluate_strike_mispricing(chain, 100.0, fair_iv_forecast=MockForecast())
    assert a4.strikes[0]["fair_iv"] == 0.24


# ---------------------------------------------------------------------------
# 8. Edge Cases & Resilience (Never Raises)
# ---------------------------------------------------------------------------


def test_evaluate_strike_mispricing_empty_and_invalid_inputs():
    """Verifies graceful degradation on missing, empty, or degenerate inputs."""
    # Empty chain
    a_empty = evaluate_strike_mispricing([], spot_price=100.0, fair_iv_forecast=0.25)
    assert a_empty.summary["total_strikes"] == 0
    assert len(a_empty.candidate_trades) == 0

    # None chain
    a_none = evaluate_strike_mispricing(None, spot_price=100.0, fair_iv_forecast=0.25)
    assert a_none.summary["total_strikes"] == 0

    # Non-positive spot price
    a_zero_spot = evaluate_strike_mispricing([{"strike": 100.0}], spot_price=0.0, fair_iv_forecast=0.25)
    assert a_zero_spot.spot_price == 0.0
    assert a_zero_spot.summary["regime"] == "INVALID_SPOT"

    # None fair IV forecast -> strikes preserved with UNKNOWN valuation
    a_no_fair = evaluate_strike_mispricing(
        [{"strike": 100.0, "option_type": "call", "market_iv": 0.25}],
        spot_price=100.0,
        fair_iv_forecast=None,
    )
    assert len(a_no_fair.strikes) == 1
    assert a_no_fair.strikes[0]["valuation_tag"] == "UNKNOWN"


def test_evaluate_strike_mispricing_iv_inversion_fallback():
    """Verifies that when market_iv is omitted, it is inferred from option price."""
    spot = 100.0
    strike = 100.0
    t_years = 30.0 / 365.0
    known_price = 3.0  # Approx ~25% vol

    chain = [
        {"strike": strike, "option_type": "call", "mid_price": known_price, "dte": 30.0},
    ]

    analysis = evaluate_strike_mispricing(chain, spot_price=spot, fair_iv_forecast=0.20)
    rec = analysis.strikes[0]
    assert rec["market_iv"] is not None
    assert rec["market_iv"] > 0
    assert rec["spread"] is not None


# ---------------------------------------------------------------------------
# 9. Serialization & DTO Compatibility
# ---------------------------------------------------------------------------


def test_mispricing_analysis_to_dict_and_indexing():
    """Tests .to_dict() and dictionary item access on MispricingAnalysis."""
    chain = [{"strike": 100.0, "option_type": "call", "market_iv": 0.28}]
    analysis = evaluate_strike_mispricing(chain, spot_price=100.0, fair_iv_forecast=0.25, symbol="TEST")

    # Dict item access
    assert analysis["symbol"] == "TEST"
    assert analysis["spot_price"] == 100.0
    assert len(analysis["strikes"]) == 1

    # .to_dict() serialization
    data = analysis.to_dict()
    assert isinstance(data, dict)
    assert data["symbol"] == "TEST"
    assert "strikes" in data
    assert "summary" in data
    assert "candidate_trades" in data
    assert "diagnostics" in data
