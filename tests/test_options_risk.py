"""Tests for pilots/options_risk.py (Portfolio Risk & Aggregate Greeks Engine)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import numpy as np
import pandas as pd
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


class _FakeHistoricalStoreForBeta:
    """Exposes ONLY ``get_bars`` -- deliberately no ``get_symbol_beta`` (that
    method never existed on the real ``HistoricalStore``; a regression here
    would raise AttributeError instead of silently degrading, unlike the
    original bug's bare ``except Exception: pass``)."""

    construct_count = 0
    get_bars_call_count = 0

    def __init__(self, *args, **kwargs):
        type(self).construct_count += 1

    def get_bars(self, symbol, lookback_days=None):
        type(self).get_bars_call_count += 1
        return _BETA_TEST_BARS.get(str(symbol).upper())


def _synthetic_beta_bars():
    """Builds daily-close price series for AAPL/TSLA/SPY whose regression
    betas vs SPY are near-exactly 1.20 / 2.00 by construction (returns are a
    deterministic linear function of SPY's returns plus tiny independent
    noise), so the test asserts on the REAL Cov/Var computation rather than a
    mocked beta value."""
    n = 80
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    rng = np.random.RandomState(7)
    spy_returns = rng.normal(0, 0.01, n)
    aapl_returns = 1.20 * spy_returns + rng.normal(0, 0.0005, n)
    tsla_returns = 2.00 * spy_returns + rng.normal(0, 0.0005, n)

    def _closes(returns):
        return 100.0 * np.cumprod(1.0 + returns)

    return {
        "AAPL": pd.DataFrame({"Close": _closes(aapl_returns)}, index=dates),
        "TSLA": pd.DataFrame({"Close": _closes(tsla_returns)}, index=dates),
        "SPY": pd.DataFrame({"Close": _closes(spy_returns)}, index=dates),
    }


_BETA_TEST_BARS = _synthetic_beta_bars()


def test_beta_weighted_delta_spy_calculation(monkeypatch):
    """Verifies that beta-weighted SPY delta scales by each symbol's true
    regression beta, computed through the REAL _resolve_symbol_betas() ->
    HistoricalStore.get_bars() -> compute_beta() pipeline.

    Regression test for the bug where _resolve_symbol_beta() called
    HistoricalStore.get_symbol_beta() -- a method that never existed -- and
    silently fell back to a hardcoded beta of 1.0 for every symbol, every
    time, with no error surfaced anywhere. The prior version of this test
    monkeypatched _resolve_symbol_beta directly and so could never have
    caught that; this one only mocks the data source (HistoricalStore),
    leaving the beta math itself genuinely exercised.
    """
    _FakeHistoricalStoreForBeta.construct_count = 0
    _FakeHistoricalStoreForBeta.get_bars_call_count = 0
    monkeypatch.setattr("data.historical_store.HistoricalStore", _FakeHistoricalStoreForBeta)

    pos_aapl = PaperPosition(symbol="AAPL", qty=100.0, avg_entry_price=150.0)  # Dollar Delta = 15,000
    pos_tsla = PaperPosition(symbol="TSLA", qty=50.0, avg_entry_price=200.0)   # Dollar Delta = 10,000

    mock_provider = MagicMock()
    mock_provider.get_latest_quote.side_effect = lambda sym: MagicMock(price=150.0 if sym == "AAPL" else 200.0)

    greeks = calculate_portfolio_greeks(positions=[pos_aapl, pos_tsla], market_provider=mock_provider, spy_spot=500.0)

    assert greeks["net_dollar_delta"] == 25000.0
    assert greeks["beta_data_unavailable_symbols"] == []
    aapl_beta = greeks["positions"][0]["beta"]
    tsla_beta = greeks["positions"][1]["beta"]
    assert aapl_beta == pytest.approx(1.20, abs=0.05)
    assert tsla_beta == pytest.approx(2.00, abs=0.05)
    assert aapl_beta != 1.0 and tsla_beta != 1.0  # would be the silently-broken fallback value
    assert greeks["positions"][0]["beta_dollar_delta"] == pytest.approx(15000.0 * aapl_beta, abs=1.0)
    assert greeks["positions"][1]["beta_dollar_delta"] == pytest.approx(10000.0 * tsla_beta, abs=1.0)
    expected_beta_weighted = round((15000.0 * aapl_beta + 10000.0 * tsla_beta) / 500.0, 2)
    assert greeks["beta_weighted_delta_spy"] == expected_beta_weighted

    # Batched: ONE store construction for the whole call (previously a fresh
    # HistoricalStore() was built per POSITION -- 2 here, even for just 2
    # positions on 2 distinct tickers, and would scale linearly with
    # position count regardless of how many distinct tickers were involved).
    assert _FakeHistoricalStoreForBeta.construct_count == 1
    # get_bars called once per distinct ticker (AAPL, TSLA) plus once for the
    # shared SPY series -- not once per position, and not once per ticker
    # PLUS a redundant SPY refetch per ticker.
    assert _FakeHistoricalStoreForBeta.get_bars_call_count == 3


def test_beta_unavailable_excludes_only_the_beta_weighted_sum(monkeypatch):
    """A ticker with no cached bars degrades to an honestly NaN/None beta --
    never a fabricated neutral default -- and is excluded from
    net_beta_dollar_delta/beta_weighted_delta_spy specifically, while still
    counting toward every other aggregate (net_dollar_delta, greeks)."""
    class _EmptyBarsStore:
        def __init__(self, *a, **k):
            pass

        def get_bars(self, symbol, lookback_days=None):
            return None  # no cached history for anything, including SPY

    monkeypatch.setattr("data.historical_store.HistoricalStore", _EmptyBarsStore)

    pos = PaperPosition(symbol="AAPL", qty=100.0, avg_entry_price=150.0)
    mock_provider = MagicMock()
    mock_provider.get_latest_quote.return_value = MagicMock(price=150.0)

    greeks = calculate_portfolio_greeks(positions=[pos], market_provider=mock_provider, spy_spot=500.0)

    assert greeks["net_dollar_delta"] == 15000.0  # still counted
    assert greeks["beta_data_unavailable_symbols"] == ["AAPL"]
    assert greeks["positions"][0]["beta"] is None
    assert greeks["positions"][0]["beta_dollar_delta"] is None
    assert greeks["beta_weighted_delta_spy"] == 0.0  # nothing contributed to the sum


