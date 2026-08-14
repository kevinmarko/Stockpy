"""
pilots/order_sizing.py
======================
Order sizing calculation and validation module for stock and options trading.

Handles:
  - Conversion of dollar budgets ($) to shares or contracts
  - 100x multiplier scaling for options contracts
  - Safe cash preset calculation (75% max single-tap cap)
  - Pre-execution affordability and sufficiency checks
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


def calculate_stock_sizing(
    dollar_amount: float, 
    price: float, 
    *, 
    allow_fractional: bool = True
) -> float:
    """
    Computes number of stock shares from a dollar amount budget.
    """
    if price <= 0 or dollar_amount <= 0:
        return 0.0
    shares = dollar_amount / price
    if not allow_fractional:
        return float(math.floor(shares))
    return round(shares, 4)


def calculate_option_sizing(
    dollar_amount: float, 
    cost_per_share: float, 
    *, 
    multiplier: int = 100
) -> int:
    """
    Computes integer contract count from a dollar amount budget.
    Each standard option contract controls `multiplier` (100) shares.
    """
    if cost_per_share <= 0 or dollar_amount <= 0:
        return 0
    cost_per_contract = cost_per_share * multiplier
    return int(math.floor(dollar_amount / cost_per_contract))


def calculate_safe_cash_preset(
    available_cash: float, 
    percentage: float = 0.75
) -> float:
    """
    Calculates the maximum safe single-tap preset dollar amount.
    Defaults to 75% of available cash to ensure a single tap cannot commit 100%
    of paper cash to one position.
    """
    if available_cash <= 0:
        return 0.0
    safe_amount = math.floor(available_cash * percentage)
    return max(0.0, float(safe_amount))


def validate_order_sizing(
    estimated_total: float, 
    available_cash: float, 
    *, 
    max_position_pct: float = 1.0
) -> Tuple[bool, Optional[str]]:
    """
    Validates if the estimated order total fits within available cash and sizing limits.
    """
    if estimated_total <= 0:
        return False, "Order total must be greater than zero."
    if available_cash < estimated_total:
        return False, f"Insufficient cash: required ${estimated_total:.2f}, available ${available_cash:.2f}."
    max_allowed = available_cash * max_position_pct
    if estimated_total > max_allowed:
        return False, f"Order exceeds maximum position sizing limit (${max_allowed:.2f})."
    return True, None
