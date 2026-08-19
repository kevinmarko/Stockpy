"""Tests for pilots/options_risk.py (Portfolio Risk & Aggregate Greeks Engine)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest


from data.paper_account_store import PaperAccountStore, PaperPosition
from pilots.options_risk import (
    parse_option_symbol,
    calculate_black_scholes_greeks,
    calculate_position_greeks,
    calculate_portfolio_greeks,
)


def test_parse_option_symbol():
    res_call = parse_option_symbol("AAPL 2026-09-18 $150.00 CALL")
    assert res_call is not None
    assert res_call["ticker"] == "AAPL"
    assert res_call["expiration"] == "2026-09-18"
    assert res_call["strike"] == 150.0
    assert res_call["option_type"] == "call"

    res_put = parse_option_symbol("MSFT 2026-10-16 $420.50 PUT")
    assert res_put is not None
    assert res_put["ticker"] == "MSFT"
    assert res_put["strike"] == 420.50
    assert res_put["option_type"] == "put"

    res_stock = parse_option_symbol("AAPL")
    assert res_stock is None


def test_calculate_black_scholes_greeks_call_and_put():
    spot = 150.0
    strike = 150.0
    t_years = 30.0 / 365.0
    sigma = 0.25

    g_call = calculate_black_scholes_greeks(spot, strike, t_years, sigma, option_type="call")
    assert 0.45 < g_call["delta"] < 0.65
    assert g_call["gamma"] > 0
    assert g_call["vega_1pct"] > 0
    assert g_call["theta_daily"] < 0  # Long option loses value to time decay
    assert g_call["rho_1pct"] > 0     # Call gains value when interest rates rise
    assert g_call["rho"] > 0

    g_put = calculate_black_scholes_greeks(spot, strike, t_years, sigma, option_type="put")
    assert -0.55 < g_put["delta"] < -0.35
    assert g_put["gamma"] > 0
    assert g_put["vega_1pct"] > 0
    assert g_put["theta_daily"] < 0
    assert g_put["rho_1pct"] < 0     # Put loses value when interest rates rise
    assert g_put["rho"] < 0


def test_calculate_position_greeks_stock():
    pos = PaperPosition(symbol="AAPL", qty=100.0, avg_entry_price=150.0)
    g = calculate_position_greeks(pos, spot_price=155.0)

    assert g["asset_type"] == "stock"
    assert g["position_delta"] == 100.0
    assert g["position_dollar_delta"] == 15500.0
    assert g["position_gamma"] == 0.0
    assert g["position_theta_daily"] == 0.0
    assert g["position_vega_1pct"] == 0.0


def test_calculate_position_greeks_short_put():
    # Short 1 put contract (qty = -1.0)
    pos = PaperPosition(symbol="AAPL 2026-09-18 $145.00 PUT", qty=-1.0, avg_entry_price=2.0)
    fixed_now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    g = calculate_position_greeks(pos, spot_price=150.0, now=fixed_now)

    assert g["asset_type"] == "option"
    assert g["qty"] == -1.0
    # Short put has positive delta (bullish) and positive theta (decay income)
    assert g["position_delta"] > 0
    assert g["position_theta_daily"] > 0
    assert g["position_vega_1pct"] < 0  # Short put is short volatility


def test_calculate_portfolio_greeks_multi_leg_spread():
    store = PaperAccountStore(db_url="sqlite:///:memory:")

    # Setup Put Credit Spread: Short 1 contract 150P, Long 1 contract 145P
    legs = [
        {"symbol": "AAPL 2026-09-18 $150.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 250.0},
        {"symbol": "AAPL 2026-09-18 $145.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 100.0},
    ]
    store.apply_multi_leg_fill(
        client_order_id="test_pcs_1",
        symbol="AAPL",
        strategy_name="Put Credit Spread",
        contracts=1,
        legs=legs,
        net_cash_impact=148.70,
        commission_and_fees=1.30,
    )

    mock_provider = MagicMock()
    mock_quote = MagicMock(price=150.0)
    mock_provider.get_latest_quote.return_value = mock_quote

    greeks = calculate_portfolio_greeks(store=store, market_provider=mock_provider, spy_spot=500.0)



    assert greeks["total_positions"] == 2
    assert greeks["option_positions_count"] == 2
    assert greeks["stock_positions_count"] == 0

    # Put Credit Spread is net short premium:
    # 1. Net theta must be positive (collecting decay from the higher-strike short leg)
    assert greeks["net_theta_daily"] > 0
    # 2. Net vega must be negative (short vol)
    assert greeks["net_vega_1pct"] < 0
    # 3. Net delta must be positive (bullish)
    assert greeks["net_delta_shares"] > 0
    assert greeks["net_dollar_delta"] > 0
    assert greeks["beta_weighted_delta_spy"] > 0


def test_calculate_black_scholes_greeks_0dte_fallback():
    """At 0DTE (T <= 1e-12), delta falls back to exact intrinsic delta and decay/vega Greeks are 0."""
    spot = 155.0
    strike = 150.0

    # ITM Call at expiration
    g_call_itm = calculate_black_scholes_greeks(spot, strike, t_years=0.0, sigma=0.25, option_type="call")
    assert g_call_itm["delta"] == 1.0
    assert g_call_itm["gamma"] == 0.0
    assert g_call_itm["theta_daily"] == 0.0
    assert g_call_itm["vega_1pct"] == 0.0
    assert g_call_itm["price"] == 5.0

    # OTM Call at expiration
    g_call_otm = calculate_black_scholes_greeks(145.0, strike, t_years=0.0, sigma=0.25, option_type="call")
    assert g_call_otm["delta"] == 0.0
    assert g_call_otm["price"] == 0.0

    # ITM Put at expiration
    g_put_itm = calculate_black_scholes_greeks(140.0, strike, t_years=0.0, sigma=0.25, option_type="put")
    assert g_put_itm["delta"] == -1.0
    assert g_put_itm["price"] == 10.0


def test_calculate_black_scholes_greeks_degenerate_volatility():
    """Degenerate or zero volatility falls back gracefully without division by zero."""
    g = calculate_black_scholes_greeks(spot=150.0, strike=150.0, t_years=0.1, sigma=1e-15, option_type="call")
    assert g["gamma"] == 0.0
    assert g["theta_daily"] == 0.0
    assert g["vega_1pct"] == 0.0


def test_calculate_portfolio_greeks_missing_data_exclusion():
    """Positions with unresolvable quotes are excluded from sum and listed in positions_with_missing_data."""
    pos1 = PaperPosition(symbol="AAPL", qty=100.0, avg_entry_price=150.0)
    pos2 = PaperPosition(symbol="UNRESOLVABLE_SYM", qty=50.0, avg_entry_price=20.0)

    mock_provider = MagicMock()
    # AAPL has quote, UNRESOLVABLE_SYM returns None
    mock_provider.get_latest_quote.side_effect = lambda sym: MagicMock(price=150.0) if sym == "AAPL" else None

    greeks = calculate_portfolio_greeks(positions=[pos1, pos2], market_provider=mock_provider)

    assert "UNRESOLVABLE_SYM" in greeks["positions_with_missing_data"]
    assert "UNRESOLVABLE_SYM" in greeks["beta_excluded_symbols"]
    # Only AAPL's delta is included
    assert greeks["net_delta_shares"] == 100.0
    assert greeks["net_dollar_delta"] == 15000.0


def test_options_risk_ast_import_safety():
    """Verifies that pilots/options_risk.py never imports processing_engine even transitively."""
    import ast
    with open("pilots/options_risk.py", "r") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "processing_engine" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "processing_engine" not in node.module


def test_calculate_black_scholes_exact_analytical_references():
    """Verifies Black-Scholes Greeks against exact closed-form analytical references."""
    spot = 100.0
    strike = 100.0
    t_years = 1.0
    sigma = 0.20
    r = 0.05

    g_call = calculate_black_scholes_greeks(spot, strike, t_years, sigma, r=r, option_type="call")
    # Exact d1 = (ln(1) + (0.05 + 0.02)*1) / 0.2 = 0.35
    # N(0.35) = 0.63683
    assert pytest.approx(g_call["delta"], abs=1e-3) == 0.6368
    # Exact Gamma = N'(0.35) / (100 * 0.2 * 1) = 0.37524 / 20 = 0.01876
    assert pytest.approx(g_call["gamma"], abs=1e-4) == 0.0188
    # Exact Vega (1% IV change) = 100 * 1 * 0.37524 * 0.01 = 0.3752
    assert pytest.approx(g_call["vega_1pct"], abs=1e-3) == 0.3752
    # Put Delta = N(d1) - 1 = -0.3632
    g_put = calculate_black_scholes_greeks(spot, strike, t_years, sigma, r=r, option_type="put")
    assert pytest.approx(g_put["delta"], abs=1e-3) == -0.3632
    # Put Gamma matches Call Gamma exactly
    assert pytest.approx(g_put["gamma"], abs=1e-4) == g_call["gamma"]
    # Put Vega matches Call Vega exactly
    assert pytest.approx(g_put["vega_1pct"], abs=1e-3) == g_call["vega_1pct"]


def test_calculate_portfolio_greeks_per_symbol_beta():
    """Verifies that beta-weighted SPY delta applies per-symbol regression beta."""
    # Long 100 shares of NVDA (beta = 1.8), spot = $100 -> dollar delta = $10,000, beta dollar delta = $18,000
    pos_nvda = PaperPosition(symbol="NVDA", qty=100.0, avg_entry_price=100.0)
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.side_effect = lambda sym: MagicMock(price=100.0) if sym == "NVDA" else (MagicMock(price=500.0) if sym == "SPY" else None)

    from unittest.mock import patch
    with patch("pilots.options_risk._resolve_symbol_beta", side_effect=lambda sym: 1.8 if sym == "NVDA" else 1.0):
        g = calculate_portfolio_greeks(positions=[pos_nvda], market_provider=mock_provider, spy_spot=500.0)
        assert g["net_dollar_delta"] == 10000.0
        # Beta-weighted delta SPY shares = 18000 / 500 = 36.0 shares
        assert pytest.approx(g["beta_weighted_delta_spy"], abs=0.1) == 36.0


def test_beta_weighted_delta_spy_calculation(monkeypatch):
    """Verifies that beta-weighted SPY delta scales by each symbol's true regression beta."""
    from pilots.options_risk import _resolve_symbol_beta

    # Mock beta lookup
    betas = {"AAPL": 1.20, "TSLA": 2.00, "SPY": 1.00}
    monkeypatch.setattr("pilots.options_risk._resolve_symbol_beta", lambda sym: betas.get(sym, 1.0))

    pos_aapl = PaperPosition(symbol="AAPL", qty=100.0, avg_entry_price=150.0)  # Dollar Delta = 15,000 * 1.2 = 18,000
    pos_tsla = PaperPosition(symbol="TSLA", qty=50.0, avg_entry_price=200.0)   # Dollar Delta = 10,000 * 2.0 = 20,000

    mock_provider = MagicMock()
    mock_provider.get_latest_quote.side_effect = lambda sym: MagicMock(price=150.0 if sym == "AAPL" else 200.0)

    # SPY Spot = 500.0
    # Expected Beta Dollar Delta = 18,000 + 20,000 = 38,000
    # Expected SPY Delta Shares = 38,000 / 500 = 76.0
    greeks = calculate_portfolio_greeks(positions=[pos_aapl, pos_tsla], market_provider=mock_provider, spy_spot=500.0)

    assert greeks["net_dollar_delta"] == 25000.0
    assert greeks["beta_weighted_delta_spy"] == 76.0
    assert greeks["positions"][0]["symbol_beta"] == 1.20
    assert greeks["positions"][0]["beta_dollar_delta"] == 18000.0
    assert greeks["positions"][1]["symbol_beta"] == 2.00
    assert greeks["positions"][1]["beta_dollar_delta"] == 20000.0



def test_black_scholes_greeks_exact_analytical_reference():
    """Validates calculate_black_scholes_greeks against exact hand-computed closed-form reference values.

    Parameters: S=100.0, K=100.0, T=1.0 yr, sigma=0.20, r=0.05
    d1 = (0 + 0.05 + 0.02) / 0.20 = 0.35
    d2 = 0.35 - 0.20 = 0.15
    N(d1) = 0.63683065, N(d2) = 0.55961769, N'(d1) = 0.375240
    """
    g_call = calculate_black_scholes_greeks(spot=100.0, strike=100.0, t_years=1.0, sigma=0.20, r=0.05, option_type="call")
    assert pytest.approx(g_call["price"], abs=0.02) == 10.45
    assert pytest.approx(g_call["delta"], abs=0.005) == 0.637
    assert pytest.approx(g_call["gamma"], abs=0.001) == 0.0188
    assert pytest.approx(g_call["vega_1pct"], abs=0.01) == 0.38
    assert pytest.approx(g_call["theta_daily"], abs=0.005) == -0.0255
    assert pytest.approx(g_call["rho_1pct"], abs=0.01) == 0.53


    g_put = calculate_black_scholes_greeks(spot=100.0, strike=100.0, t_years=1.0, sigma=0.20, r=0.05, option_type="put")
    assert pytest.approx(g_put["price"], abs=0.02) == 5.57
    assert pytest.approx(g_put["delta"], abs=0.005) == -0.363
    assert pytest.approx(g_put["gamma"], abs=0.001) == 0.0188
    assert pytest.approx(g_put["vega_1pct"], abs=0.01) == 0.38
    assert pytest.approx(g_put["rho_1pct"], abs=0.01) == -0.42



