"""
Tests for pilots/options_hedging.py (Dynamic Delta Hedging Engine).
"""

import ast
from unittest.mock import MagicMock
import pytest

from data.paper_account_store import PaperAccountStore, PaperPosition
from pilots.options_hedging import (
    calculate_delta_hedge_order,
    execute_delta_hedge,
)
from settings import settings


def test_calculate_delta_hedge_order_within_deadband():
    """Delta within tolerance band returns None (no hedge needed)."""
    # Default band is 25.0 shares
    greeks = {"beta_weighted_delta_spy": 12.0}
    order = calculate_delta_hedge_order(greeks, spy_spot=500.0, tolerance_band_shares=25.0)
    assert order is None

    # Negative small delta
    greeks_neg = {"beta_weighted_delta_spy": -24.9}
    order_neg = calculate_delta_hedge_order(greeks_neg, spy_spot=500.0, tolerance_band_shares=25.0)
    assert order_neg is None

    # Zero delta
    assert calculate_delta_hedge_order({"beta_weighted_delta_spy": 0.0}, spy_spot=500.0) is None


def test_calculate_delta_hedge_order_positive_delta_sells_spy():
    """Positive delta portfolio (net long) generates a SELL SPY market order."""
    greeks = {"beta_weighted_delta_spy": 100.0}
    order = calculate_delta_hedge_order(greeks, spy_spot=500.0, tolerance_band_shares=25.0)

    assert order is not None
    assert order["symbol"] == "SPY"
    assert order["side"] == "sell"
    assert order["qty"] == 100
    assert order["order_type"] == "market"
    assert order["current_beta_weighted_delta"] == 100.0
    assert order["shares_needed"] == -100.0


def test_calculate_delta_hedge_order_negative_delta_buys_spy():
    """Negative delta portfolio (net short) generates a BUY SPY market order."""
    greeks = {"beta_weighted_delta_spy": -75.4}
    order = calculate_delta_hedge_order(greeks, spy_spot=500.0, tolerance_band_shares=25.0)

    assert order is not None
    assert order["symbol"] == "SPY"
    assert order["side"] == "buy"
    assert order["qty"] == 75
    assert order["order_type"] == "market"
    assert order["current_beta_weighted_delta"] == -75.4
    assert order["shares_needed"] == 75.4


def test_calculate_delta_hedge_order_rounding_and_tolerance():
    """Rounds shares appropriately and respects custom tolerance band."""
    # 30.6 rounds to 31
    order = calculate_delta_hedge_order(30.6, spy_spot=500.0, tolerance_band_shares=10.0)
    assert order is not None
    assert order["side"] == "sell"
    assert order["qty"] == 31

    # Custom tolerance band of 50.0 filters out 30.6
    order_filtered = calculate_delta_hedge_order(30.6, spy_spot=500.0, tolerance_band_shares=50.0)
    assert order_filtered is None


def test_calculate_delta_hedge_order_input_formats():
    """Supports dict with net_dollar_delta, object attributes, and direct float."""
    # Dollar delta conversion: $50,000 dollar delta with SPY at $500 = 100 SPY shares
    greeks_dollar = {"net_dollar_delta": 50000.0}
    order1 = calculate_delta_hedge_order(greeks_dollar, spy_spot=500.0, tolerance_band_shares=25.0)
    assert order1 is not None
    assert order1["qty"] == 100
    assert order1["side"] == "sell"

    # Mock object with beta_weighted_delta_spy attribute
    class MockGreeks:
        beta_weighted_delta_spy = -80.0

    order2 = calculate_delta_hedge_order(MockGreeks(), spy_spot=500.0, tolerance_band_shares=25.0)
    assert order2 is not None
    assert order2["qty"] == 80
    assert order2["side"] == "buy"

    # Direct float
    order3 = calculate_delta_hedge_order(-50.0, spy_spot=500.0, tolerance_band_shares=25.0)
    assert order3 is not None
    assert order3["qty"] == 50
    assert order3["side"] == "buy"


def test_execute_delta_hedge_within_tolerance():
    """When delta is within tolerance, execute_delta_hedge returns hedged=False with no fill."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    res = execute_delta_hedge(
        store=store,
        portfolio_greeks={"beta_weighted_delta_spy": 10.0},
        spy_spot=500.0,
        tolerance_band_shares=25.0,
    )

    assert res["ok"] is True
    assert res["hedged"] is False
    assert res["order"] is None
    assert res["fill"] is None


def test_execute_delta_hedge_sell_fill():
    """Executes a SELL SPY delta hedge and updates store positions."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")

    # Portfolio is long 100 SPY delta
    greeks = {"beta_weighted_delta_spy": 100.0}
    res = execute_delta_hedge(
        store=store,
        portfolio_greeks=greeks,
        spy_spot=500.0,
        tolerance_band_shares=25.0,
    )

    assert res["ok"] is True
    assert res["hedged"] is True
    assert res["order"]["side"] == "sell"
    assert res["order"]["qty"] == 100
    assert res["fill"]["symbol"] == "SPY"
    assert res["fill"]["side"] == "sell"
    assert res["fill"]["qty"] == 100.0
    assert res["fill"]["fill_price"] == 500.0

    # Store should now hold short 100 shares of SPY
    positions = store.get_open_positions()
    assert len(positions) == 1
    spy_pos = positions[0]
    assert spy_pos.symbol == "SPY"
    assert spy_pos.qty == -100.0


def test_execute_delta_hedge_buy_fill():
    """Executes a BUY SPY delta hedge and updates store positions."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")

    # Portfolio is short 60 SPY delta
    greeks = {"beta_weighted_delta_spy": -60.0}
    res = execute_delta_hedge(
        store=store,
        portfolio_greeks=greeks,
        spy_spot=500.0,
        tolerance_band_shares=25.0,
    )

    assert res["ok"] is True
    assert res["hedged"] is True
    assert res["order"]["side"] == "buy"
    assert res["order"]["qty"] == 60
    assert res["fill"]["symbol"] == "SPY"
    assert res["fill"]["side"] == "buy"
    assert res["fill"]["qty"] == 60.0

    positions = store.get_open_positions()
    assert len(positions) == 1
    spy_pos = positions[0]
    assert spy_pos.symbol == "SPY"
    assert spy_pos.qty == 60.0


def test_options_hedging_ast_import_safety():
    """Verifies that pilots/options_hedging.py never imports processing_engine even transitively."""
    with open("pilots/options_hedging.py", "r") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "processing_engine" not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "processing_engine" not in node.module, f"Forbidden from-import found: {node.module}"


def test_settings_delta_hedge_defaults():
    """Verifies that the new delta hedging settings are registered with proper defaults."""
    assert hasattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED")
    assert hasattr(settings, "OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES")
    assert settings.OPTIONS_DELTA_HEDGE_ENABLED is False
    assert settings.OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES == 25.0
