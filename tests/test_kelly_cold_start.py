"""
InvestYo Quant Platform - Kelly Cold-Start Fallback Tests
==========================================================
Acceptance tests for cold-start scenarios in kelly_sizing_for_strategy():
  - Zero trades total → vol-target fallback
  - strategy_id provided, but zero matching trades → vol-target fallback
  - strategy_id provided, but < 30 matching trades → vol-target fallback
  - strategy_id provided, no 'strategy' column in trades table → vol-target fallback
  - realized_vol unavailable + no history → weight=0.0, tag="cold_start_no_vol"

INVARIANT: cold-start fallback MUST:
  1. Return the same value as volatility_target_weight(realized_vol, target_vol, max_leverage).
  2. Tag the path as "vol_target_fallback" (not "bootstrap_kelly_*").
  3. NEVER return NaN silently — always 0.0 or a positive weight.
"""

import math

import pandas as pd
import pytest

from sizing.kelly import kelly_sizing_for_strategy, MIN_TRADES_REQUIRED
from sizing.vol_target import volatility_target_weight
from transactions_store import TransactionsStore


# =============================================================================
# Helpers
# =============================================================================

REALIZED_VOL = 0.20
STRATEGY_A = "momentum_v1"
STRATEGY_B = "mean_reversion_v1"


def _seed_store_with_trades(store: TransactionsStore, n: int, strategy: str, win: bool = True):
    """Add n closed trades to the store, all wins or all losses."""
    ts = pd.Timestamp.utcnow()
    for i in range(n):
        tid = store.record_trade(
            symbol="AAPL",
            side="long",
            entry_ts=ts + pd.Timedelta(minutes=i),
            entry_price=100.0,
            shares=10.0,
            strategy=strategy,
        )
        exit_price = 110.0 if win else 95.0
        store.close_trade(tid, exit_ts=ts + pd.Timedelta(days=1, minutes=i), exit_price=exit_price)


# =============================================================================
# COLD START: ZERO TRADES TOTAL
# =============================================================================

class TestColdStartZeroTrades:
    """Empty store → always vol-target fallback, regardless of strategy_id."""

    def test_empty_store_with_strategy_id_falls_back(self):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=REALIZED_VOL
        )
        expected = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0)
        assert math.isclose(weight, expected, rel_tol=1e-9)
        assert tag == "vol_target_fallback"

    def test_empty_store_no_realized_vol_returns_zero(self):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=None
        )
        assert weight == 0.0
        assert tag == "cold_start_no_vol"

    def test_empty_store_zero_realized_vol_returns_zero(self):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=0.0
        )
        assert weight == 0.0
        assert tag == "cold_start_no_vol"


# =============================================================================
# COLD START: STRATEGY_ID WITH NO MATCHING TRADES
# =============================================================================

class TestColdStartNoMatchingTrades:
    """Store has trades for STRATEGY_B, but query is for STRATEGY_A → fallback."""

    def test_unmatched_strategy_id_falls_back_to_vol_target(self):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        # Seed enough trades for strategy B (>= 30)
        _seed_store_with_trades(store, n=40, strategy=STRATEGY_B, win=True)

        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=REALIZED_VOL
        )
        expected = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0)
        assert math.isclose(weight, expected, rel_tol=1e-9)
        assert tag == "vol_target_fallback", f"Expected 'vol_target_fallback', got '{tag}'"

    def test_unmatched_strategy_id_no_vol_returns_zero(self):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        _seed_store_with_trades(store, n=40, strategy=STRATEGY_B, win=True)
        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=None
        )
        assert weight == 0.0
        assert tag == "cold_start_no_vol"


# =============================================================================
# COLD START: STRATEGY_ID WITH FEWER THAN MIN_TRADES_REQUIRED MATCHING TRADES
# =============================================================================

class TestColdStartInsufficientMatchingTrades:
    """Matching trades exist but below the MIN_TRADES_REQUIRED=30 gate → fallback."""

    @pytest.mark.parametrize("n", [0, 1, 10, MIN_TRADES_REQUIRED - 1])
    def test_below_minimum_trades_falls_back(self, n):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        if n > 0:
            _seed_store_with_trades(store, n=n, strategy=STRATEGY_A, win=True)

        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=REALIZED_VOL
        )
        expected = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0)
        assert math.isclose(weight, expected, rel_tol=1e-9), (
            f"Expected vol-target weight ({expected:.4f}) for n={n} trades, got ({weight:.4f})"
        )
        assert tag == "vol_target_fallback", (
            f"Expected 'vol_target_fallback' for n={n} trades, got '{tag}'"
        )

    def test_exactly_at_threshold_activates_bootstrap(self):
        """Exactly MIN_TRADES_REQUIRED matching trades → bootstrap path activates."""
        store = TransactionsStore(db_url="sqlite:///:memory:")
        # 18 wins (60%), 12 losses (40%) to give a real edge (p=0.6, b=2.0)
        _seed_store_with_trades(store, n=18, strategy=STRATEGY_A, win=True)
        _seed_store_with_trades(store, n=12, strategy=STRATEGY_A, win=False)

        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=REALIZED_VOL
        )
        # Bootstrap path should have activated (not vol-target fallback)
        assert tag.startswith("bootstrap_kelly_5th_pct"), (
            f"Expected bootstrap path at n=30 exact, got tag='{tag}'"
        )
        # Weight must be positive and <= MAX_LEVERAGE cap
        assert 0.0 < weight <= 2.0, f"Weight {weight:.4f} out of range"


# =============================================================================
# FALLBACK VALUES ARE DETERMINISTIC AND NON-NEGATIVE
# =============================================================================

class TestColdStartProperties:
    """Invariants that must hold in ALL cold-start scenarios."""

    @pytest.mark.parametrize("realized_vol,expected_tag", [
        (0.20, "vol_target_fallback"),
        (0.15, "vol_target_fallback"),
        (None, "cold_start_no_vol"),
        (float("nan"), "cold_start_no_vol"),
        (0.0, "cold_start_no_vol"),
        (-0.05, "cold_start_no_vol"),
    ])
    def test_cold_start_returns_correct_tag(self, realized_vol, expected_tag):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        weight, tag = kelly_sizing_for_strategy(
            store, strategy_id=STRATEGY_A, realized_vol=realized_vol
        )
        assert tag == expected_tag, f"Vol={realized_vol}: expected tag '{expected_tag}', got '{tag}'"
        assert weight >= 0.0, f"Weight must be non-negative; got {weight}"
        assert not math.isnan(weight), f"Weight must not be NaN; got {weight}"

    def test_cold_start_vol_target_matches_standalone_function(self):
        """The fallback value must equal the standalone volatility_target_weight()."""
        store = TransactionsStore(db_url="sqlite:///:memory:")
        for vol in [0.10, 0.15, 0.20, 0.30, 0.50]:
            weight, _tag = kelly_sizing_for_strategy(
                store, strategy_id=STRATEGY_A, realized_vol=vol
            )
            expected = volatility_target_weight(vol, target_vol=0.10, max_leverage=2.0)
            assert math.isclose(weight, expected, rel_tol=1e-9), (
                f"Cold-start weight ({weight:.4f}) != standalone vol-target ({expected:.4f}) "
                f"for realized_vol={vol}"
            )
