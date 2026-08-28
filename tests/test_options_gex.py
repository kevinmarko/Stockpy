"""
tests/test_options_gex.py — Comprehensive Unit Tests for Options GEX & Volatility Regime Engine.
================================================================================================

Tests:
1. Black-Scholes Gamma math & degenerate input guards.
2. Net GEX evaluation at candidate spot prices.
3. Zero-Gamma Flip ($S^*$) root-finding and percentage distance calculations.
4. Volatility Regime classification (POSITIVE_GAMMA, NEGATIVE_GAMMA, PIN_RISK_HIGH).
5. Gamma walls identification (Call Wall, Put Wall, Absolute Major Wall) & pin risk.
6. Strike-level GEX profile aggregation & concentration metrics.
7. End-to-end GexAnalysisResult pipeline with DataFrame / Dict / Object inputs & JSON serialization.
8. AST import safety verification (CONSTRAINT #1 & #3).
"""

import ast
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from pilots.options_gex import (
    DEFAULT_CONCENTRATION_THRESHOLD_PCT,
    _OPTION_SYM_RE,
    DEFAULT_PIN_RISK_THRESHOLD_PCT,
    DEFAULT_SEARCH_RANGE_PCT,
    GexAnalysisResult,
    GammaRegime,
    REGIME_DESCRIPTIONS,
    REGIME_NEGATIVE_GAMMA,
    REGIME_PIN_RISK_HIGH,
    REGIME_POSITIVE_GAMMA,
    StrikeGex,
    _bisection_root,
    _normalize_chain_data,
    _parse_expiration_dte,
    analyze_options_gex,
    calculate_black_scholes_gamma,
    calculate_gex_profile,
    calculate_strike_gex,
    calculate_zero_gamma_flip,
    classify_gamma_regime,
    compute_total_net_gex_at_spot,
    generate_synthetic_options_chain,
    identify_gamma_walls,
)


# ---------------------------------------------------------------------------
# 1. Black-Scholes Gamma Math & Degenerate Guards
# ---------------------------------------------------------------------------

def test_black_scholes_gamma_standard_pricing():
    # Spot = 100, Strike = 100 (ATM), T = 1.0 year, Sigma = 0.20, r = 0.05
    gamma = calculate_black_scholes_gamma(spot=100.0, strike=100.0, t_years=1.0, sigma=0.20, r=0.05)
    assert gamma > 0.0
    # Analytical ATM gamma is approx ~ 0.01876
    assert pytest.approx(gamma, abs=1e-3) == 0.01876


def test_black_scholes_gamma_degenerate_guards():
    # Zero or negative spot / strike
    assert calculate_black_scholes_gamma(spot=0.0, strike=100.0, t_years=0.1, sigma=0.2) == 0.0
    assert calculate_black_scholes_gamma(spot=-50.0, strike=100.0, t_years=0.1, sigma=0.2) == 0.0
    assert calculate_black_scholes_gamma(spot=100.0, strike=0.0, t_years=0.1, sigma=0.2) == 0.0
    assert calculate_black_scholes_gamma(spot=100.0, strike=-100.0, t_years=0.1, sigma=0.2) == 0.0

    # 0 DTE / T <= 0
    assert calculate_black_scholes_gamma(spot=100.0, strike=100.0, t_years=0.0, sigma=0.2) == 0.0
    assert calculate_black_scholes_gamma(spot=100.0, strike=100.0, t_years=-0.5, sigma=0.2) == 0.0

    # Degenerate or NaN sigma
    assert calculate_black_scholes_gamma(spot=100.0, strike=100.0, t_years=0.1, sigma=0.0) == 0.0
    assert calculate_black_scholes_gamma(spot=100.0, strike=100.0, t_years=0.1, sigma=float("nan")) == 0.0


# ---------------------------------------------------------------------------
# 2. Net GEX Evaluation at Candidate Spot Prices
# ---------------------------------------------------------------------------

def test_compute_total_net_gex_at_spot_call_vs_put():
    # 1 Call contract: strike 500, OI 100
    call_chain = [{
        "strike": 500.0,
        "option_type": "CALL",
        "open_interest": 100,
        "implied_volatility": 0.20,
        "dte": 30.0,
    }]
    call_gex = compute_total_net_gex_at_spot(call_chain, spot=500.0)
    assert call_gex > 0.0

    # 1 Put contract: strike 500, OI 100
    put_chain = [{
        "strike": 500.0,
        "option_type": "PUT",
        "open_interest": 100,
        "implied_volatility": 0.20,
        "dte": 30.0,
    }]
    put_gex = compute_total_net_gex_at_spot(put_chain, spot=500.0)
    assert put_gex < 0.0

    # Equal Call and Put OI at exact same strike and IV -> Net GEX == 0.0
    balanced_chain = call_chain + put_chain
    net_gex = compute_total_net_gex_at_spot(balanced_chain, spot=500.0)
    assert pytest.approx(net_gex, abs=1e-4) == 0.0


def test_compute_total_net_gex_degenerate_inputs():
    assert compute_total_net_gex_at_spot([], spot=500.0) == 0.0
    assert compute_total_net_gex_at_spot(None, spot=500.0) == 0.0
    assert compute_total_net_gex_at_spot([{"strike": 500}], spot=0.0) == 0.0
    assert compute_total_net_gex_at_spot([{"strike": 500}], spot=-10.0) == 0.0


def test_net_gex_uses_percent_move_scaling_not_raw_100x():
    """Regression for the confirmed 100x dollar-scaling bug (secondary audit,
    2026-08-24): raw Gamma*OI*100*S^2 with no *0.01 "per 1% move" factor
    overstated every dollar GEX figure by exactly 100x relative to this
    module's own `dealer_hedging_flow` field and to the industry-standard
    SqueezeMetrics/SpotGamma convention. Pin the correct, hand-derived
    magnitude directly rather than just checking sign/nonzero.
    """
    gamma = calculate_black_scholes_gamma(spot=500.0, strike=500.0, t_years=30.0 / 365.0, sigma=0.20, r=0.045)
    oi = 10_000
    expected = round(gamma * oi * 100.0 * 500.0 * 500.0 * 0.01, 2)

    chain = [{
        "strike": 500.0,
        "option_type": "CALL",
        "open_interest": oi,
        "implied_volatility": 0.20,
        "dte": 30.0,
    }]
    net_gex = compute_total_net_gex_at_spot(chain, spot=500.0, r=0.045)
    assert pytest.approx(net_gex, rel=1e-6) == expected
    # A single ATM strike with realistic OI must land in a plausible
    # multi-million-dollar range, not the ~$3.46 BILLION the pre-fix raw
    # formula produced for this exact case.
    assert 0 < net_gex < 50_000_000


def test_get_options_gex_profile_net_gex_matches_dealer_hedging_flow(monkeypatch):
    """Regression: get_options_gex_profile's headline `net_gex` and its
    `dealer_hedging_flow` field must describe the same quantity (both are
    "dollar hedging flow per 1% move") -- pre-fix, dealer_hedging_flow applied
    the *0.01 convention correctly while net_gex/call_gex/put_gex/strikes[]
    did not, making the two fields silently disagree by 100x.
    """
    from unittest.mock import MagicMock, patch
    from pilots.options_gex import get_options_gex_profile

    mock_quote = MagicMock()
    mock_quote.price = 500.0
    mock_market_provider = MagicMock()
    mock_market_provider.get_latest_quote.return_value = mock_quote

    mock_options_provider = MagicMock()
    mock_options_provider.fetch_options_chain.side_effect = (
        lambda symbol, expiration=None: (
            ["2026-09-18"] if expiration is None else _make_fake_yf_chain()
        )
    )

    with patch("data.market_data.get_provider", return_value=mock_market_provider), \
         patch("data.market_data.get_options_provider", return_value=mock_options_provider):
        result = get_options_gex_profile("SPY")

    assert result["net_gex"] != 0.0
    assert pytest.approx(result["net_gex"], abs=0.01) == result["dealer_hedging_flow"]
    assert pytest.approx(result["net_gex"], abs=0.01) == result["dealer_hedging_per_1pct_move_dollars"]


# ---------------------------------------------------------------------------
# 2b. CONSTRAINT #4 -- missing IV/expiration must exclude the contract, never
#     fabricate a placeholder sigma=0.25 / dte=30.0
# ---------------------------------------------------------------------------

def test_normalize_chain_data_excludes_contract_with_missing_iv():
    records = _normalize_chain_data([
        {"strike": 500.0, "option_type": "CALL", "open_interest": 100, "dte": 30.0},  # no IV field at all
        {"strike": 505.0, "option_type": "CALL", "open_interest": 100, "implied_volatility": 0.0, "dte": 30.0},  # stale-quote zero IV
        {"strike": 510.0, "option_type": "CALL", "open_interest": 100, "implied_volatility": float("nan"), "dte": 30.0},
        {"strike": 515.0, "option_type": "CALL", "open_interest": 100, "implied_volatility": 0.20, "dte": 30.0},  # the one valid record
    ])
    assert len(records) == 1
    assert records[0]["strike"] == 515.0
    assert records[0]["sigma"] == pytest.approx(0.20)


def test_normalize_chain_data_excludes_contract_with_missing_expiration():
    records = _normalize_chain_data([
        {"strike": 500.0, "option_type": "CALL", "open_interest": 100, "implied_volatility": 0.20},  # no expiration at all
        {"strike": 505.0, "option_type": "CALL", "open_interest": 100, "implied_volatility": 0.20, "expiration": "not-a-date"},
        {"strike": 510.0, "option_type": "CALL", "open_interest": 100, "implied_volatility": 0.20, "dte": 30.0},  # the one valid record
    ])
    assert len(records) == 1
    assert records[0]["strike"] == 510.0
    assert records[0]["dte"] == pytest.approx(30.0)


def test_parse_expiration_dte_returns_none_never_fabricated_30():
    assert _parse_expiration_dte(None) is None
    assert _parse_expiration_dte("not-a-date") is None
    assert _parse_expiration_dte("") is None
    # Genuinely-parsed values are unaffected -- this only changes the
    # unparseable/missing fallback, not real parsing.
    assert _parse_expiration_dte(15.0) == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# 3. Zero-Gamma Flip Root Finder
# ---------------------------------------------------------------------------

def test_calculate_zero_gamma_flip_finds_exact_root():
    # Construct a chain with:
    # Heavy Put OI at 480 (Dealer Short Gamma below 500)
    # Heavy Call OI at 520 (Dealer Long Gamma above 500)
    chain = [
        {"strike": 480.0, "option_type": "PUT", "open_interest": 5000, "implied_volatility": 0.22, "dte": 25.0},
        {"strike": 520.0, "option_type": "CALL", "open_interest": 5000, "implied_volatility": 0.22, "dte": 25.0},
    ]

    spot = 500.0
    zero_flip_spot, dist_pct = calculate_zero_gamma_flip(chain, spot_price=spot, search_range_pct=0.20)

    assert zero_flip_spot is not None
    assert dist_pct is not None

    # Black-Scholes lognormal geometric drift shifts root slightly from 500 to ~497.24
    assert pytest.approx(zero_flip_spot, abs=3.0) == 500.0
    assert pytest.approx(zero_flip_spot, abs=0.1) == 497.24
    assert pytest.approx(dist_pct, abs=0.01) == 0.0

    # Verify that Net GEX at S* is practically 0 (relative to billion-dollar total GEX scale)
    net_gex_at_flip = compute_total_net_gex_at_spot(chain, spot=zero_flip_spot)
    assert pytest.approx(net_gex_at_flip, abs=5000.0) == 0.0


def test_calculate_zero_gamma_flip_asymmetric_oi():
    # Puts dominate lower strikes with huge OI -> Net GEX negative at lower prices
    # Calls dominate higher strikes -> Net GEX positive at higher prices
    chain = [
        {"strike": 450.0, "option_type": "PUT", "open_interest": 10000, "implied_volatility": 0.25, "dte": 30.0},
        {"strike": 480.0, "option_type": "PUT", "open_interest": 8000, "implied_volatility": 0.22, "dte": 30.0},
        {"strike": 520.0, "option_type": "CALL", "open_interest": 4000, "implied_volatility": 0.18, "dte": 30.0},
        {"strike": 550.0, "option_type": "CALL", "open_interest": 12000, "implied_volatility": 0.16, "dte": 30.0},
    ]

    spot = 500.0
    zero_flip_spot, dist_pct = calculate_zero_gamma_flip(chain, spot_price=spot, search_range_pct=0.25)

    assert zero_flip_spot is not None
    assert dist_pct is not None
    assert zero_flip_spot > 0.0

    # Distance % matches formula: (S* - Spot) / Spot
    expected_dist = (zero_flip_spot - spot) / spot
    assert pytest.approx(dist_pct, abs=1e-5) == expected_dist

    # Verify zero GEX at root relative to overall dollar GEX magnitude
    net_gex_at_root = compute_total_net_gex_at_spot(chain, spot=zero_flip_spot)
    assert pytest.approx(net_gex_at_root, abs=10000.0) == 0.0


def test_calculate_zero_gamma_flip_no_root():
    # Only calls exist -> Net GEX is positive for all spot prices, no zero flip
    call_only_chain = [
        {"strike": 500.0, "option_type": "CALL", "open_interest": 1000, "implied_volatility": 0.20, "dte": 30.0},
        {"strike": 520.0, "option_type": "CALL", "open_interest": 2000, "implied_volatility": 0.18, "dte": 30.0},
    ]
    flip, dist = calculate_zero_gamma_flip(call_only_chain, spot_price=500.0)
    assert flip is None
    assert dist is None

    # Empty chain
    flip_empty, dist_empty = calculate_zero_gamma_flip([], spot_price=500.0)
    assert flip_empty is None
    assert dist_empty is None


def test_bisection_root_fallback():
    # f(x) = x^2 - 4 on [0, 5] -> root = 2.0
    def f(x):
        return x * x - 4.0

    root = _bisection_root(f, 0.0, 5.0, tol=1e-5)
    assert root is not None
    assert pytest.approx(root, abs=1e-4) == 2.0

    # No sign change -> returns None
    assert _bisection_root(f, 3.0, 5.0) is None


# ---------------------------------------------------------------------------
# 4. Volatility Regime Classification
# ---------------------------------------------------------------------------

def test_classify_gamma_regime_positive_gamma():
    regime = classify_gamma_regime(net_gex=15000000.0, distance_to_flip_pct=0.04)
    assert regime == REGIME_POSITIVE_GAMMA
    assert regime == "POSITIVE_GAMMA"
    assert "Dealer Long Gamma -> Volatility Dampener / Mean-Reverting" in regime.description
    assert isinstance(regime, GammaRegime)
    assert isinstance(regime, str)


def test_classify_gamma_regime_negative_gamma():
    regime = classify_gamma_regime(net_gex=-8500000.0, distance_to_flip_pct=-0.03)
    assert regime == REGIME_NEGATIVE_GAMMA
    assert regime == "NEGATIVE_GAMMA"
    assert "Dealer Short Gamma -> Volatility Accelerator / Squeeze & Crash Hazard" in regime.description


def test_classify_gamma_regime_pin_risk_high():
    # Spot = 500.0, Major Wall Strike = 501.0 (Distance = |500-501|/500 = 0.002 = 0.2% <= 0.5%)
    # Concentration = 25.0% (>= 15.0%)
    regime = classify_gamma_regime(
        net_gex=5000000.0,
        distance_to_flip_pct=0.02,
        spot_price=500.0,
        major_wall_strike=501.0,
        major_wall_concentration_pct=25.0,
    )
    assert regime == REGIME_PIN_RISK_HIGH
    assert regime == "PIN_RISK_HIGH"
    assert "Spot within 0.5% of major gamma wall with high concentration" in regime.description


def test_classify_gamma_regime_pin_risk_override_and_low_concentration():
    # Near wall (0.2%) but low concentration (5% < 15%) -> does NOT trigger pin risk
    regime = classify_gamma_regime(
        net_gex=5000000.0,
        spot_price=500.0,
        major_wall_strike=501.0,
        major_wall_concentration_pct=5.0,
    )
    assert regime == REGIME_POSITIVE_GAMMA

    # Explicit override is_pin_risk=True
    regime_override = classify_gamma_regime(
        net_gex=-2000000.0,
        is_pin_risk=True,
    )
    assert regime_override == REGIME_PIN_RISK_HIGH


# ---------------------------------------------------------------------------
# 5. Gamma Walls Identification & Strike Profile Aggregation
# ---------------------------------------------------------------------------

def test_calculate_strike_gex_and_walls():
    chain = [
        {"strike": 490.0, "option_type": "PUT", "open_interest": 8000, "implied_volatility": 0.20, "dte": 20.0, "volume": 500},
        {"strike": 500.0, "option_type": "CALL", "open_interest": 2000, "implied_volatility": 0.20, "dte": 20.0, "volume": 100},
        {"strike": 500.0, "option_type": "PUT", "open_interest": 2000, "implied_volatility": 0.20, "dte": 20.0, "volume": 100},
        {"strike": 510.0, "option_type": "CALL", "open_interest": 10000, "implied_volatility": 0.20, "dte": 20.0, "volume": 1200},
    ]

    spot = 500.0
    strikes_gex = calculate_strike_gex(chain, spot_price=spot)

    assert len(strikes_gex) == 3
    assert [s.strike for s in strikes_gex] == [490.0, 500.0, 510.0]

    # Check concentration percentages sum to 100%
    total_conc = sum(s.gamma_concentration_pct for s in strikes_gex)
    assert pytest.approx(total_conc, abs=0.1) == 100.0

    walls = identify_gamma_walls(strikes_gex, spot_price=spot)

    # 510 has highest call OI -> Call Wall
    assert walls["call_wall_strike"] == 510.0

    # 490 has highest put OI -> Put Wall
    assert walls["put_wall_strike"] == 490.0

    # Major wall should be either 490 or 510
    assert walls["major_gamma_wall"] in (490.0, 510.0)
    assert walls["major_wall_concentration_pct"] > 30.0


# ---------------------------------------------------------------------------
# 6. End-to-End GEX Analysis Pipeline & Data Ingestion
# ---------------------------------------------------------------------------

def test_calculate_gex_profile_synthetic_chain():
    synth_chain = generate_synthetic_options_chain(
        spot_price=500.0,
        dte=30.0,
        call_oi_bias=1.2,
        put_oi_bias=0.8,
    )

    result = calculate_gex_profile(synth_chain, spot_price=500.0, ticker="SPY")

    assert isinstance(result, GexAnalysisResult)
    assert result.ticker == "SPY"
    assert result.spot_price == 500.0
    assert result.total_open_interest > 0
    assert result.total_call_oi > 0
    assert result.total_put_oi > 0
    assert len(result.strikes_profile) > 0
    assert result.call_wall_strike is not None
    assert result.put_wall_strike is not None
    assert result.major_gamma_wall is not None
    assert result.gamma_regime in (REGIME_POSITIVE_GAMMA, REGIME_NEGATIVE_GAMMA, REGIME_PIN_RISK_HIGH)
    assert len(result.regime_description) > 0

    # Verify dictionary conversion & JSON serialization
    res_dict = result.to_dict()
    assert res_dict["ticker"] == "SPY"
    assert "strikes_profile" in res_dict

    json_str = json.dumps(res_dict)
    assert "SPY" in json_str


def test_calculate_gex_profile_dataframe_input():
    df = pd.DataFrame({
        "strike": [490.0, 500.0, 510.0, 490.0, 500.0, 510.0],
        "option_type": ["PUT", "PUT", "PUT", "CALL", "CALL", "CALL"],
        "open_interest": [5000, 3000, 1000, 1000, 3000, 6000],
        "implied_volatility": [0.22, 0.20, 0.19, 0.22, 0.20, 0.19],
        "dte": [14.0, 14.0, 14.0, 14.0, 14.0, 14.0],
        "volume": [100, 50, 20, 20, 50, 120],
    })

    result = analyze_options_gex(df, spot_price=500.0, ticker="QQQ")
    assert result.ticker == "QQQ"
    assert result.total_open_interest == 19000
    assert result.call_wall_strike == 510.0
    assert result.put_wall_strike == 490.0


def test_calculate_gex_profile_empty_and_degenerate_degradation():
    # Empty DataFrame
    empty_res = calculate_gex_profile(pd.DataFrame(), spot_price=500.0, ticker="AAPL")
    assert empty_res.ticker == "AAPL"
    assert empty_res.net_gex == 0.0
    assert empty_res.zero_gamma_flip is None
    assert empty_res.total_open_interest == 0

    # Non-positive spot price
    zero_spot_res = calculate_gex_profile([{"strike": 100, "option_type": "CALL"}], spot_price=0.0)
    assert zero_spot_res.spot_price == 0.0
    assert zero_spot_res.net_gex == 0.0


# ---------------------------------------------------------------------------
# 7. get_options_gex_profile() Resolver -- Live Chain Shape Handling
# ---------------------------------------------------------------------------
#
# Regression coverage for a genuine bug (not a data-availability gap): the live
# resolver's options-chain provider is `data.market_data.CompositeOptionsProvider`,
# backed by yfinance's `Ticker.option_chain(expiration)`, which returns an `Options`
# namedtuple carrying SEPARATE `.calls`/`.puts` DataFrames -- a shape
# `_normalize_chain_data` has never understood (only a bare `pd.DataFrame` or a
# `list`/`tuple` of dict-like records). Passing that namedtuple straight through
# (the pre-fix behavior: `chain_map[str(exp)] = c`) silently produced an "Empty or
# unparseable option chain data" diagnostic on every real, correctly-entitled,
# non-empty chain fetch -- surfaced to the operator as "No real options chain data
# available" even though the provider had real strikes/OI the whole time.

class _FakeYFinanceOptions:
    """Mimics yfinance's `Options` namedtuple shape: separate `.calls`/`.puts`
    DataFrames (plus `.underlying`, unused here)."""

    def __init__(self, calls: pd.DataFrame, puts: pd.DataFrame):
        self.calls = calls
        self.puts = puts
        self.underlying = {}


def _make_fake_yf_chain(spot: float = 500.0) -> _FakeYFinanceOptions:
    calls = pd.DataFrame({
        "contractSymbol": ["SYN260918C00490000", "SYN260918C00500000", "SYN260918C00510000"],
        "strike": [490.0, 500.0, 510.0],
        "openInterest": [1000, 3000, 1500],
        "volume": [50, 150, 80],
        "impliedVolatility": [0.22, 0.20, 0.19],
    })
    puts = pd.DataFrame({
        "contractSymbol": ["SYN260918P00490000", "SYN260918P00500000", "SYN260918P00510000"],
        "strike": [490.0, 500.0, 510.0],
        "openInterest": [4000, 2000, 800],
        "volume": [200, 100, 40],
        "impliedVolatility": [0.24, 0.21, 0.19],
    })
    return _FakeYFinanceOptions(calls, puts)


def test_flatten_provider_chain_entry_yfinance_shaped_namedtuple():
    """The core bug fix: a yfinance-shaped Options object (.calls/.puts DataFrames)
    must flatten into tagged CALL/PUT records, not silently vanish."""
    from pilots.options_gex import _flatten_provider_chain_entry

    chain_obj = _make_fake_yf_chain()
    records = _flatten_provider_chain_entry(chain_obj, "2026-09-18")

    assert len(records) == 6  # 3 calls + 3 puts
    call_records = [r for r in records if r["option_type"] == "CALL"]
    put_records = [r for r in records if r["option_type"] == "PUT"]
    assert len(call_records) == 3
    assert len(put_records) == 3
    assert all(r["expiration"] == "2026-09-18" for r in records)
    # Original yfinance column names (openInterest, impliedVolatility) survive
    # untouched -- _normalize_chain_data is what maps those, not this helper.
    assert call_records[0]["openInterest"] == 1000
    assert call_records[0]["strike"] == 490.0


def test_flatten_provider_chain_entry_dataframe_and_list_and_none():
    from pilots.options_gex import _flatten_provider_chain_entry

    # Bare DataFrame (already-flat shape) -- tolerated, expiration back-filled.
    df = pd.DataFrame({
        "strike": [100.0],
        "option_type": ["CALL"],
        "open_interest": [10],
    })
    df_records = _flatten_provider_chain_entry(df, "2026-09-18")
    assert len(df_records) == 1
    assert df_records[0]["expiration"] == "2026-09-18"

    # List of dict records -- tolerated.
    list_records = _flatten_provider_chain_entry(
        [{"strike": 100.0, "option_type": "PUT", "open_interest": 5}], "2026-09-18"
    )
    assert len(list_records) == 1

    # None / empty DataFrame / empty list -- degrade to [], never raise.
    assert _flatten_provider_chain_entry(None, "2026-09-18") == []
    assert _flatten_provider_chain_entry(pd.DataFrame(), "2026-09-18") == []
    assert _flatten_provider_chain_entry([], "2026-09-18") == []


def test_get_options_gex_profile_resolves_real_gex_from_yfinance_shaped_chain(monkeypatch):
    """End-to-end regression: with a live spot quote AND a live yfinance-shaped
    options chain both available, get_options_gex_profile() must compute a real,
    non-degenerate GEX profile -- not the pre-fix "Empty or unparseable option
    chain data" degradation that presented real market structure as unavailable."""
    from unittest.mock import MagicMock, patch
    from pilots.options_gex import get_options_gex_profile

    mock_quote = MagicMock()
    mock_quote.price = 500.0
    mock_market_provider = MagicMock()
    mock_market_provider.get_latest_quote.return_value = mock_quote

    mock_options_provider = MagicMock()
    mock_options_provider.fetch_options_chain.side_effect = (
        lambda symbol, expiration=None: (
            ["2026-09-18"] if expiration is None else _make_fake_yf_chain()
        )
    )

    with patch("data.market_data.get_provider", return_value=mock_market_provider), \
         patch("data.market_data.get_options_provider", return_value=mock_options_provider):
        result = get_options_gex_profile("SPY")

    assert result["spot_price_source"] == "live"
    assert result["chain_source"] == "live"
    assert result["diagnostics"].get("warning") is None
    assert result["total_open_interest"] == 1000 + 3000 + 1500 + 4000 + 2000 + 800
    # A real chain around spot=500 with meaningful OI on both sides must resolve real
    # (non-degenerate) gamma walls and a non-zero Net GEX -- the exact symptom the
    # pre-fix bug produced as "—" / "+$0.0M" despite genuine, correctly-entitled
    # chain data being available the whole time.
    assert result["call_wall_strike"] is not None
    assert result["put_wall_strike"] is not None
    assert result["net_gex"] != 0.0
    assert len(result["strikes"]) > 0


# ---------------------------------------------------------------------------
# 7b. Option-symbol regex parity fix (F3, docs/module_efficiency_redundancy_audit.md)
# ---------------------------------------------------------------------------
#
# _OPTION_SYM_RE previously made the `$` optional, diverging from the SAME
# nominal "standardized option leg symbol" format's canonical regex in
# pilots/options_risk.py and pilots/realtime_risk_streamer.py (both require
# `$`). A symbol lacking `$` parsed successfully here and returned None in
# both siblings -- a real behavioral fork this fix closes.

def test_symbol_regex_fallback_parses_the_dollar_bearing_standard_format():
    """The happy path this fallback exists for: strike/type absent as
    structured fields, recovered from the standardized symbol string."""
    raw_list = [
        {
            "symbol": "AAPL 2026-09-18 $150.00 CALL",
            "expirationDate": "2026-09-18",
            "openInterest": 100,
            "impliedVolatility": 0.25,
        }
    ]
    records = _normalize_chain_data(raw_list, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(records) == 1
    assert records[0]["strike"] == pytest.approx(150.0)
    assert records[0]["option_type"] == "CALL"

def test_symbol_regex_no_longer_accepts_a_dollar_less_standard_format_string():
    """The actual regression this PR fixes: a symbol in the SAME nominal
    shape but missing `$` must now be rejected by _OPTION_SYM_RE (matching
    options_risk.py/realtime_risk_streamer.py), not silently accepted.
    Confirmed via the regex object directly -- _normalize_chain_data would
    otherwise also fall through to _OCC_SYM_RE, which could coincidentally
    match a differently-shaped string and mask this assertion."""
    m = _OPTION_SYM_RE.match("AAPL 2026-09-18 150.00 CALL")
    assert m is None

def test_symbol_regex_matches_options_risk_pys_canonical_pattern_exactly():
    """Direct parity check against the sibling module's own canonical
    regex -- both must agree on every input, not just the one hand-picked
    example above."""
    from pilots.options_risk import _OPTION_SYM_RE as canonical_re

    dollar_form = "AAPL 2026-09-18 $150.00 CALL"
    no_dollar_form = "AAPL 2026-09-18 150.00 CALL"

    assert bool(_OPTION_SYM_RE.match(dollar_form)) == bool(canonical_re.match(dollar_form)) == True
    assert bool(_OPTION_SYM_RE.match(no_dollar_form)) == bool(canonical_re.match(no_dollar_form)) == False

def test_occ_format_symbol_still_parses_via_the_separate_occ_regex():
    """The no-`$` OCC ticker format (a genuinely different shape) must
    still work via _OCC_SYM_RE -- this fix narrows _OPTION_SYM_RE only,
    it does not narrow OCC-format support."""
    raw_list = [
        {
            "symbol": "AAPL240119C00150000",
            "expirationDate": "2024-01-19",
            "openInterest": 50,
            "impliedVolatility": 0.30,
        }
    ]
    records = _normalize_chain_data(raw_list, now=datetime(2019, 1, 1, tzinfo=timezone.utc))
    assert len(records) == 1
    assert records[0]["strike"] == pytest.approx(150.0)
    assert records[0]["option_type"] == "CALL"


# ---------------------------------------------------------------------------
# 8. AST Import Safety Test (CONSTRAINT #1 & #3)
# ---------------------------------------------------------------------------

def test_options_gex_ast_import_safety():
    """Verifies that pilots/options_gex.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "options_gex.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="options_gex.py")

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
            mod_name = node.module or ""
            for forbidden in forbidden_modules:
                assert forbidden not in mod_name, f"Forbidden from-import found: {mod_name}"
