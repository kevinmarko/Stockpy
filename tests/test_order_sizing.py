"""
tests/test_order_sizing.py
==========================
Tests for pilots/order_sizing.py sizing calculations and safe cash presets.
"""

from pilots.order_sizing import (
    calculate_stock_sizing,
    calculate_option_sizing,
    calculate_safe_cash_preset,
    validate_order_sizing,
)


def test_calculate_stock_sizing_fractional_and_integer():
    # $500 at $10/share = 50 shares
    assert calculate_stock_sizing(500.0, 10.0) == 50.0

    # $500 at $15/share = 33.3333 shares
    assert calculate_stock_sizing(500.0, 15.0) == 33.3333

    # $500 at $15/share non-fractional = 33 shares
    assert calculate_stock_sizing(500.0, 15.0, allow_fractional=False) == 33.0


def test_calculate_option_sizing_integer_contracts():
    # $500 on option with $0.50 premium ($50/contract) = 10 contracts
    assert calculate_option_sizing(500.0, 0.50) == 10

    # $500 on option with $0.15 premium ($15/contract) = 33 contracts
    assert calculate_option_sizing(500.0, 0.15) == 33

    # $500 on option with $6.00 premium ($600/contract) = 0 contracts (cannot afford 1)
    assert calculate_option_sizing(500.0, 6.00) == 0


def test_calculate_safe_cash_preset_caps_at_75_percent():
    # $10,000 cash -> 75% preset is $7,500
    assert calculate_safe_cash_preset(10000.0) == 7500.0

    # $100 cash -> 75% preset is $75
    assert calculate_safe_cash_preset(100.0) == 75.0

    # $0 cash -> 0
    assert calculate_safe_cash_preset(0.0) == 0.0


def test_validate_order_sizing():
    valid, err = validate_order_sizing(500.0, 10000.0)
    assert valid is True
    assert err is None

    # Insufficient cash
    valid, err = validate_order_sizing(15000.0, 10000.0)
    assert valid is False
    assert "Insufficient cash" in err

    # Zero cost
    valid, err = validate_order_sizing(0.0, 10000.0)
    assert valid is False
