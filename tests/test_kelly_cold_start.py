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
  1. Return volatility_target_weight(realized_vol, target_vol, max_leverage)
     RAMPED IN by the WS3 scale-in factor min(1, n_trades / MIN_TRADES_REQUIRED)
     so sizing doesn't jump discontinuously to the full vol-target weight the
     instant a strategy is new. At n_trades >= MIN_TRADES_REQUIRED the factor is
     1.0 and the value equals the standalone vol-target weight exactly.
  2. Tag the path with the "vol_target_fallback" prefix (not "bootstrap_kelly_*"),
     now carrying an audit suffix "(scalein=<f>,n=<n>)".
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
    ts = pd.Timestamp.now("UTC")
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
        # WS3: 0 trades -> scale-in factor 0 -> weight ramps to 0.0.
        assert math.isclose(weight, 0.0, abs_tol=1e-12)
        assert tag == "vol_target_fallback(scalein=0.00,n=0)"

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
        # WS3: 0 matching trades for STRATEGY_A -> scale-in 0 -> weight 0.0.
        assert math.isclose(weight, 0.0, abs_tol=1e-12)
        assert tag == "vol_target_fallback(scalein=0.00,n=0)", (
            f"Expected 'vol_target_fallback(scalein=0.00,n=0)', got '{tag}'"
        )

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
        # WS3: weight is the vol-target weight ramped by min(1, n/30).
        scale_in = min(1.0, n / MIN_TRADES_REQUIRED)
        expected = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0) * scale_in
        assert math.isclose(weight, expected, rel_tol=1e-9, abs_tol=1e-12), (
            f"Expected scaled vol-target weight ({expected:.4f}) for n={n} trades, got ({weight:.4f})"
        )
        assert tag == f"vol_target_fallback(scalein={scale_in:.2f},n={n})", (
            f"Expected scale-in-tagged fallback for n={n} trades, got '{tag}'"
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
        # WS3: the vol-target fallback tag carries a "(scalein=..,n=..)" suffix;
        # the cold_start_no_vol tag is unchanged (exact).
        assert tag.startswith(expected_tag), f"Vol={realized_vol}: expected tag prefix '{expected_tag}', got '{tag}'"
        assert weight >= 0.0, f"Weight must be non-negative; got {weight}"
        assert not math.isnan(weight), f"Weight must not be NaN; got {weight}"

    def test_cold_start_vol_target_matches_standalone_function(self):
        """At the warm scale-in ceiling (n_trades >= MIN_TRADES_REQUIRED) the
        fallback value equals the standalone volatility_target_weight().

        Seeds 30 all-winning trades for STRATEGY_A: the payoff ratio b is
        undefined (no losses) so the per-strategy path still takes the vol-target
        fallback, but with n_trades=30 the WS3 scale-in factor is 1.0."""
        store = TransactionsStore(db_url="sqlite:///:memory:")
        _seed_store_with_trades(store, n=MIN_TRADES_REQUIRED, strategy=STRATEGY_A, win=True)
        for vol in [0.10, 0.15, 0.20, 0.30, 0.50]:
            weight, tag = kelly_sizing_for_strategy(
                store, strategy_id=STRATEGY_A, realized_vol=vol
            )
            expected = volatility_target_weight(vol, target_vol=0.10, max_leverage=2.0)
            assert math.isclose(weight, expected, rel_tol=1e-9), (
                f"Warm-ceiling weight ({weight:.4f}) != standalone vol-target ({expected:.4f}) "
                f"for realized_vol={vol}"
            )
            assert tag == "vol_target_fallback(scalein=1.00,n=30)", tag
