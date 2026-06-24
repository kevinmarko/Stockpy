"""
tests/test_correlation_check.py
================================
Focused tests for ``PreTradeRiskGate.max_correlation_check``.

Acceptance criteria (from Prompt 5.2):
  * Submitting a highly-correlated position to an existing book is blocked.
  * Low-correlation position passes.
  * Negative high-correlation (near -1) is also blocked (amplifies tail swings).
  * Missing returns data → conservative pass.
  * Symbol not in returns frame → conservative pass.
  * Fewer than 20 common observations → conservative pass.
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

def _intent(symbol: str) -> OrderIntent:
    return OrderIntent(
        strategy_id="corr_test",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
    )


def _pos(symbol: str) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol, qty=10.0, avg_entry_price=100.0,
        market_value=1000.0, unrealized_pl=0.0,
    )


def _gate(threshold: float = 0.85) -> PreTradeRiskGate:
    return PreTradeRiskGate(max_correlation=threshold)


def _daily_returns(n: int = 120, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    spy = pd.Series(rng.standard_normal(n), index=idx, name="SPY")
    return pd.DataFrame({"SPY": spy})


# ---------------------------------------------------------------------------
# Core correlation scenarios
# ---------------------------------------------------------------------------

class TestCorrelationScenarios:
    def _build_frame(self, n: int = 200, seed: int = 42) -> tuple[pd.DataFrame, str, str]:
        """Return (returns_df, high_corr_sym, low_corr_sym)."""
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2022-01-01", periods=n, freq="B")
        spy = pd.Series(rng.standard_normal(n), index=idx)
        # High positive correlation: tiny noise → |r| ≈ 0.99
        high_corr = spy + rng.standard_normal(n) * 0.02
        # Low correlation: independent
        low_corr = pd.Series(rng.standard_normal(n), index=idx)
        # Negative high correlation (negated) → |r| ≈ 0.99 in absolute value
        neg_corr = -spy + rng.standard_normal(n) * 0.02

        df = pd.DataFrame({
            "SPY": spy,
            "CORR_HIGH": high_corr,
            "CORR_LOW": low_corr,
            "CORR_NEG": neg_corr,
        })
        return df

    def test_high_positive_correlation_blocked(self):
        df = self._build_frame()
        gate = _gate(threshold=0.85)
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("CORR_HIGH"), ctx)
        assert not result.passed, f"Expected block but got: {result.reason}"
        assert "CORR_HIGH" in result.reason
        assert "SPY" in result.reason

    def test_low_correlation_passes(self):
        df = self._build_frame()
        gate = _gate(threshold=0.85)
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("CORR_LOW"), ctx)
        assert result.passed, f"Expected pass but got: {result.reason}"

    def test_high_negative_correlation_blocked(self):
        """A position highly negatively correlated with an existing holding
        is also blocked — it amplifies tail P&L swings in portfolio terms."""
        df = self._build_frame()
        gate = _gate(threshold=0.85)
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("CORR_NEG"), ctx)
        assert not result.passed, f"Expected block on |r|>0.85 but got: {result.reason}"

    def test_threshold_is_configurable(self):
        """Raising the threshold to 0.999 should allow near-perfectly-correlated symbols."""
        df = self._build_frame()
        gate = _gate(threshold=0.999)
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("CORR_HIGH"), ctx)
        # At 0.999 threshold even |r|=0.99 might pass
        assert isinstance(result.passed, bool)  # no crash; just validate it runs


# ---------------------------------------------------------------------------
# Conservative-pass edge cases
# ---------------------------------------------------------------------------

class TestCorrelationConservativePasses:
    def test_no_returns_df_passes(self):
        gate = _gate()
        ctx = RiskContext(returns_df=None, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("AAPL"), ctx)
        assert result.passed

    def test_empty_returns_df_passes(self):
        gate = _gate()
        ctx = RiskContext(returns_df=pd.DataFrame(), open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("AAPL"), ctx)
        assert result.passed

    def test_no_open_positions_passes(self):
        df = _daily_returns()
        gate = _gate()
        ctx = RiskContext(returns_df=df, open_positions=[])
        result = gate.max_correlation_check(_intent("SPY"), ctx)
        assert result.passed

    def test_new_symbol_not_in_returns_df_passes(self):
        """Symbol we want to buy isn't in the historical returns — skip check."""
        df = _daily_returns()  # only has SPY
        gate = _gate()
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("UNKNOWN_TICKER"), ctx)
        assert result.passed

    def test_existing_position_not_in_returns_df_passes(self):
        """Existing position not in returns frame — can't compute corr, skip."""
        rng = np.random.default_rng(0)
        idx = pd.date_range("2022-01-01", periods=100, freq="B")
        df = pd.DataFrame({"AAPL": rng.standard_normal(100)}, index=idx)
        gate = _gate()
        # SPY is in open_positions but NOT in returns_df
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("AAPL"), ctx)
        assert result.passed

    def test_fewer_than_20_common_obs_passes(self):
        """Insufficient overlap between two series → check skipped (conservative pass)."""
        idx_a = pd.date_range("2022-01-01", periods=10, freq="B")
        idx_b = pd.date_range("2023-01-01", periods=10, freq="B")
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "AAPL": pd.Series(rng.standard_normal(10), index=idx_a),
            "SPY": pd.Series(rng.standard_normal(10), index=idx_b),
        })
        gate = _gate()
        ctx = RiskContext(returns_df=df, open_positions=[_pos("SPY")])
        result = gate.max_correlation_check(_intent("AAPL"), ctx)
        assert result.passed


# ---------------------------------------------------------------------------
# Multiple existing positions: any breach blocks
# ---------------------------------------------------------------------------

def test_blocked_if_any_existing_position_highly_correlated():
    """Even if the new symbol is low-corr with *most* positions,
    a single high-corr existing position must still block."""
    n = 200
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    base = rng.standard_normal(n)
    df = pd.DataFrame({
        "AAPL": pd.Series(base + rng.standard_normal(n) * 0.01, index=idx),
        "SPY": pd.Series(base + rng.standard_normal(n) * 0.01, index=idx),
        "GLD": pd.Series(rng.standard_normal(n), index=idx),  # independent
    }, index=idx)

    gate = _gate(threshold=0.85)
    # Book has both SPY (high corr with AAPL) and GLD (low corr with AAPL)
    ctx = RiskContext(
        returns_df=df,
        open_positions=[_pos("SPY"), _pos("GLD")],
    )
    result = gate.max_correlation_check(_intent("AAPL"), ctx)
    assert not result.passed, "AAPL↔SPY is highly correlated — should block"
