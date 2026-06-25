"""
tests/test_correlation_check.py
================================
Focused tests for PreTradeRiskGate.max_correlation_check.

Scenarios
---------
* High positive correlation: blocked
* Low correlation: passes
* High negative correlation: blocked (|r| check)
* Configurable threshold
* Multi-position scenario: any single breach blocks
* Conservative-pass edge cases: no returns, empty frame, no positions,
  symbol missing, fewer than 20 observations
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from execution.broker_base import OrderIntent, OrderSide, OrderType, PositionSnapshot
from execution.risk_gate import PreTradeRiskGate, RiskContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy(symbol: str = "AAPL") -> OrderIntent:
    return OrderIntent(
        strategy_id="test",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
    )


def _pos(symbol: str) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol, qty=100.0, avg_entry_price=100.0,
        market_value=10_000.0, unrealized_pl=0.0,
    )


def _make_returns(corr: float, sym_new: str = "AAPL", sym_held: str = "MSFT", n: int = 60) -> pd.DataFrame:
    """Generate a returns DataFrame with the given correlation between sym_new and sym_held."""
    rng = np.random.default_rng(0)
    base = rng.standard_normal(n)
    noise = rng.standard_normal(n)
    # y = corr * x + sqrt(1 - corr²) * noise ensures |corr(x, y)| ≈ |corr|
    other = corr * base + np.sqrt(max(0.0, 1 - corr ** 2)) * noise
    return pd.DataFrame({sym_new: base, sym_held: other})


# ---------------------------------------------------------------------------
# Core correlation scenarios
# ---------------------------------------------------------------------------

def test_high_positive_correlation_blocked():
    gate = PreTradeRiskGate(max_correlation=0.85)
    ctx = RiskContext(
        open_positions=[_pos("MSFT")],
        returns_df=_make_returns(0.95),
    )
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert not result.passed
    assert "AAPL" in result.reason and "MSFT" in result.reason


def test_low_correlation_passes():
    gate = PreTradeRiskGate(max_correlation=0.85)
    ctx = RiskContext(
        open_positions=[_pos("MSFT")],
        returns_df=_make_returns(0.30),
    )
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed


def test_high_negative_correlation_blocked():
    """Both tail-amplifying and negatively-correlated shorts must be blocked."""
    gate = PreTradeRiskGate(max_correlation=0.85)
    ctx = RiskContext(
        open_positions=[_pos("MSFT")],
        returns_df=_make_returns(-0.95),
    )
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert not result.passed


def test_configurable_threshold():
    """Raising the threshold allows a previously-blocked correlation to pass."""
    corr_df = _make_returns(0.90)
    ctx = RiskContext(open_positions=[_pos("MSFT")], returns_df=corr_df)

    tight_gate = PreTradeRiskGate(max_correlation=0.80)
    loose_gate = PreTradeRiskGate(max_correlation=0.95)

    assert not tight_gate.max_correlation_check(_buy(), ctx).passed
    assert loose_gate.max_correlation_check(_buy(), ctx).passed


# ---------------------------------------------------------------------------
# Multi-position scenario
# ---------------------------------------------------------------------------

def test_multi_position_any_breach_blocks():
    """If ANY existing holding breaches the threshold the order is blocked."""
    rng = np.random.default_rng(1)
    base = rng.standard_normal(60)
    safe_other = rng.standard_normal(60)  # uncorrelated with base
    dangerous_other = 0.93 * base + np.sqrt(1 - 0.93 ** 2) * rng.standard_normal(60)

    df = pd.DataFrame({"AAPL": base, "SAFE": safe_other, "DANGER": dangerous_other})

    gate = PreTradeRiskGate(max_correlation=0.85)
    ctx = RiskContext(
        open_positions=[_pos("SAFE"), _pos("DANGER")],
        returns_df=df,
    )
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert not result.passed
    assert "DANGER" in result.reason


# ---------------------------------------------------------------------------
# Conservative-pass edge cases
# ---------------------------------------------------------------------------

def test_conservative_pass_no_returns():
    gate = PreTradeRiskGate(max_correlation=0.50)
    ctx = RiskContext(open_positions=[_pos("MSFT")])
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed


def test_conservative_pass_empty_returns_df():
    gate = PreTradeRiskGate(max_correlation=0.50)
    ctx = RiskContext(open_positions=[_pos("MSFT")], returns_df=pd.DataFrame())
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed


def test_conservative_pass_no_positions():
    gate = PreTradeRiskGate(max_correlation=0.50)
    ctx = RiskContext(
        open_positions=[],
        returns_df=_make_returns(0.99),
    )
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed


def test_conservative_pass_symbol_missing_from_returns():
    gate = PreTradeRiskGate(max_correlation=0.50)
    # Returns frame has GOOG and MSFT but NOT AAPL
    df = _make_returns(0.99, sym_new="GOOG", sym_held="MSFT")
    ctx = RiskContext(open_positions=[_pos("MSFT")], returns_df=df)
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed


def test_conservative_pass_fewer_than_20_observations():
    gate = PreTradeRiskGate(max_correlation=0.50)
    df = _make_returns(0.99, n=15)  # only 15 rows — below the minimum
    ctx = RiskContext(open_positions=[_pos("MSFT")], returns_df=df)
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed


def test_conservative_pass_existing_symbol_missing_from_returns():
    """Held ticker not in returns_df: skip that pair, don't block."""
    gate = PreTradeRiskGate(max_correlation=0.50)
    # returns_df has AAPL but NOT MSFT
    df = pd.DataFrame({"AAPL": np.random.default_rng(9).standard_normal(60)})
    ctx = RiskContext(open_positions=[_pos("MSFT")], returns_df=df)
    result = gate.max_correlation_check(_buy("AAPL"), ctx)
    assert result.passed
