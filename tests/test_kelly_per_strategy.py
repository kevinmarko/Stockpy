"""
InvestYo Quant Platform - Per-Strategy Kelly Sizing Tests
==========================================================
Acceptance tests for estimate_win_rate_and_payoff_per_strategy() and the
per-strategy bootstrap pipeline (kelly_sizing_for_strategy()).

ACCEPTANCE CRITERIA:
  1. Two strategies with different p/b → different Kelly fractions from
     estimate_win_rate_and_payoff_per_strategy().
  2. One strategy cold, one warm → cold falls back to vol-target; warm
     uses bootstrap Kelly (tagged "bootstrap_kelly_5th_pct(...)").
  3. Global aggregate vs per-strategy: point estimates differ when
     strategies have genuinely different edges.
  4. strategy_id=None in StrategyEngine._calculate_kelly_sizing() uses the
     global aggregate path (backward-compatible, tagged "aggregate_kelly").
  5. strategy_id provided in StrategyEngine._calculate_kelly_sizing() uses
     the per-strategy bootstrap path (tagged "bootstrap_kelly_5th_pct(...)").

ARCHITECTURAL INVARIANT:
  StrategyEngine._calculate_kelly_sizing(realized_vol, strategy_id=None)
  must be backward-compatible: calling without strategy_id must produce
  IDENTICAL behavior to pre-Stage-1.7 code (global pool point estimate).
"""

import math

import pandas as pd
import pytest

from sizing.kelly import (
    estimate_win_rate_and_payoff,
    estimate_win_rate_and_payoff_per_strategy,
    kelly_sizing_for_strategy,
    MIN_TRADES_REQUIRED,
)
from sizing.vol_target import volatility_target_weight
from strategy_engine import StrategyEngine
from transactions_store import TransactionsStore


# =============================================================================
# Helpers
# =============================================================================

REALIZED_VOL = 0.20
STRAT_MOMENTUM = "momentum_v1"
STRAT_MEAN_REV = "mean_reversion_v1"


def _seed_store(
    store: TransactionsStore,
    strategy: str,
    n_wins: int,
    n_losses: int,
    win_pct: float = 0.10,
    loss_pct: float = -0.05,
) -> None:
    """Seed the store with n_wins+n_losses trades for a given strategy."""
    ts = pd.Timestamp.utcnow()
    for i in range(n_wins):
        tid = store.record_trade(
            symbol="AAPL",
            side="long",
            entry_ts=ts + pd.Timedelta(minutes=i),
            entry_price=100.0,
            shares=10.0,
            strategy=strategy,
        )
        store.close_trade(
            tid,
            exit_ts=ts + pd.Timedelta(days=1, minutes=i),
            exit_price=100.0 * (1 + win_pct),
        )
    for i in range(n_losses):
        tid = store.record_trade(
            symbol="AAPL",
            side="long",
            entry_ts=ts + pd.Timedelta(days=2, minutes=i),
            entry_price=100.0,
            shares=10.0,
            strategy=strategy,
        )
        store.close_trade(
            tid,
            exit_ts=ts + pd.Timedelta(days=3, minutes=i),
            exit_price=100.0 * (1 + loss_pct),
        )


# =============================================================================
# ACCEPTANCE TEST 1: DIFFERENT STRATEGIES → DIFFERENT KELLY FRACTIONS
# =============================================================================

class TestPerStrategyDifferentEdges:
    """Two strategies with genuinely different p/b → different fractions."""

    def test_two_strategies_produce_different_p_b(self):
        """
        STRAT_MOMENTUM: 60% win rate (p=0.6), payoff ratio ~2.0
        STRAT_MEAN_REV: 40% win rate (p=0.4), payoff ratio ~0.5 (or loss-making)

        estimate_win_rate_and_payoff_per_strategy() must return different p,b for each.
        """
        store = TransactionsStore(db_url="sqlite:///:memory:")
        # Momentum: high win rate, big payoff
        _seed_store(store, STRAT_MOMENTUM, n_wins=60, n_losses=40,
                    win_pct=0.10, loss_pct=-0.05)
        # Mean rev: low win rate, tiny payoff
        _seed_store(store, STRAT_MEAN_REV, n_wins=20, n_losses=50,
                    win_pct=0.03, loss_pct=-0.05)

        p_mom, b_mom, n_mom = estimate_win_rate_and_payoff_per_strategy(store, STRAT_MOMENTUM)
        p_rev, b_rev, n_rev = estimate_win_rate_and_payoff_per_strategy(store, STRAT_MEAN_REV)

        # Neither should be NaN (both have >= 30 trades)
        assert not math.isnan(p_mom), "MOMENTUM p should not be NaN"
        assert not math.isnan(p_rev), "MEAN_REV p should not be NaN"

        # Momentum must have higher p and higher b than mean reversion
        assert p_mom > p_rev, (
            f"Momentum p ({p_mom:.3f}) should exceed mean_rev p ({p_rev:.3f})"
        )
        assert b_mom > b_rev, (
            f"Momentum b ({b_mom:.3f}) should exceed mean_rev b ({b_rev:.3f})"
        )
        assert n_mom == 100
        assert n_rev == 70

    def test_kelly_fractions_differ_between_strategies(self):
        """
        The bootstrap Kelly fractions from kelly_sizing_for_strategy() must
        differ between MOMENTUM and MEAN_REV given their different edges.
        """
        store = TransactionsStore(db_url="sqlite:///:memory:")
        _seed_store(store, STRAT_MOMENTUM, n_wins=60, n_losses=40,
                    win_pct=0.10, loss_pct=-0.05)
        _seed_store(store, STRAT_MEAN_REV, n_wins=20, n_losses=50,
                    win_pct=0.03, loss_pct=-0.05)

        weight_mom, tag_mom = kelly_sizing_for_strategy(
            store, strategy_id=STRAT_MOMENTUM, realized_vol=REALIZED_VOL
        )
        weight_rev, tag_rev = kelly_sizing_for_strategy(
            store, strategy_id=STRAT_MEAN_REV, realized_vol=REALIZED_VOL
        )

        # Both should use the bootstrap path (sufficient trades)
        assert tag_mom.startswith("bootstrap_kelly_5th_pct"), f"MOMENTUM: expected bootstrap, got '{tag_mom}'"
        assert tag_rev.startswith("bootstrap_kelly_5th_pct"), f"MEAN_REV: expected bootstrap, got '{tag_rev}'"

        # Momentum (higher edge) → larger Kelly weight
        assert weight_mom > weight_rev, (
            f"Momentum weight ({weight_mom:.4f}) must exceed mean_rev weight ({weight_rev:.4f}) "
            "given the stronger edge."
        )

    def test_global_aggregate_vs_per_strategy_differ(self):
        """
        When two strategies are mixed in the pool and one has a better edge,
        the global aggregate p/b differs from the per-strategy p/b for the
        better strategy.
        """
        store = TransactionsStore(db_url="sqlite:///:memory:")
        # Mix: 60 wins momentum + 20 wins mean-rev vs 40 losses mom + 50 losses mean-rev
        _seed_store(store, STRAT_MOMENTUM, n_wins=60, n_losses=40,
                    win_pct=0.10, loss_pct=-0.05)
        _seed_store(store, STRAT_MEAN_REV, n_wins=20, n_losses=50,
                    win_pct=0.03, loss_pct=-0.05)

        closed_df = store.closed_trades_df()
        p_global, b_global, _ = estimate_win_rate_and_payoff(closed_df, lookback_trades=300)
        p_mom, b_mom, _ = estimate_win_rate_and_payoff_per_strategy(store, STRAT_MOMENTUM)

        # The per-strategy estimate for momentum must not equal the global average
        # (because the mean_rev drags both p and b down in the aggregate pool)
        assert not math.isclose(p_global, p_mom, rel_tol=0.01), (
            f"Global p ({p_global:.3f}) should differ from per-strategy momentum p ({p_mom:.3f})"
        )


# =============================================================================
# ACCEPTANCE TEST 2: ONE COLD, ONE WARM
# =============================================================================

class TestMixedColdAndWarm:
    """Cold strategy falls back; warm strategy uses bootstrap Kelly."""

    def test_cold_strategy_falls_back_warm_strategy_bootstraps(self):
        store = TransactionsStore(db_url="sqlite:///:memory:")
        # MOMENTUM: warm (60 trades)
        _seed_store(store, STRAT_MOMENTUM, n_wins=36, n_losses=24,
                    win_pct=0.10, loss_pct=-0.05)
        # MEAN_REV: cold (only 10 trades, below threshold)
        _seed_store(store, STRAT_MEAN_REV, n_wins=6, n_losses=4,
                    win_pct=0.03, loss_pct=-0.05)

        weight_warm, tag_warm = kelly_sizing_for_strategy(
            store, strategy_id=STRAT_MOMENTUM, realized_vol=REALIZED_VOL
        )
        weight_cold, tag_cold = kelly_sizing_for_strategy(
            store, strategy_id=STRAT_MEAN_REV, realized_vol=REALIZED_VOL
        )

        # Warm: bootstrap path
        assert tag_warm.startswith("bootstrap_kelly_5th_pct"), (
            f"MOMENTUM (warm) should use bootstrap path; got '{tag_warm}'"
        )
        # Cold: vol-target fallback
        assert tag_cold == "vol_target_fallback", (
            f"MEAN_REV (cold, 10 trades) should fall back; got '{tag_cold}'"
        )
        # Cold weight matches standalone vol-target
        expected_fallback = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0)
        assert math.isclose(weight_cold, expected_fallback, rel_tol=1e-9), (
            f"Cold-start weight ({weight_cold:.4f}) != expected vol-target ({expected_fallback:.4f})"
        )


# =============================================================================
# ACCEPTANCE TEST 3: STRATEGYENGINE DISPATCH
# =============================================================================

class TestStrategyEngineDispatch:
    """StrategyEngine._calculate_kelly_sizing dispatches correctly."""

    def test_no_strategy_id_uses_global_aggregate_path(self):
        """strategy_id=None → global pool point estimate (backward-compatible)."""
        store = TransactionsStore(db_url="sqlite:///:memory:")
        _seed_store(store, STRAT_MOMENTUM, n_wins=60, n_losses=40,
                    win_pct=0.10, loss_pct=-0.05)

        engine = StrategyEngine(transactions_store=store)
        weight, tag = engine._calculate_kelly_sizing(realized_vol=REALIZED_VOL, strategy_id=None)

        assert tag == "aggregate_kelly", (
            f"Expected 'aggregate_kelly' for strategy_id=None, got '{tag}'"
        )
        # Must be positive and <= MAX_POSITION_WEIGHT
        assert 0.0 < weight <= 1.0, f"Weight {weight:.4f} out of bounds [0, 1]"

    def test_strategy_id_uses_bootstrap_path(self):
        """strategy_id provided → per-strategy bootstrap path."""
        store = TransactionsStore(db_url="sqlite:///:memory:")
        _seed_store(store, STRAT_MOMENTUM, n_wins=60, n_losses=40,
                    win_pct=0.10, loss_pct=-0.05)

        engine = StrategyEngine(transactions_store=store)
        weight, tag = engine._calculate_kelly_sizing(
            realized_vol=REALIZED_VOL, strategy_id=STRAT_MOMENTUM
        )

        assert tag.startswith("bootstrap_kelly_5th_pct"), (
            f"Expected bootstrap path with strategy_id='{STRAT_MOMENTUM}', got '{tag}'"
        )
        assert 0.0 < weight <= 1.0, f"Weight {weight:.4f} out of bounds [0, 1]"

    def test_strategy_id_cold_uses_vol_target_fallback(self):
        """strategy_id cold → vol-target fallback from _calculate_kelly_sizing."""
        store = TransactionsStore(db_url="sqlite:///:memory:")  # empty

        engine = StrategyEngine(transactions_store=store)
        weight, tag = engine._calculate_kelly_sizing(
            realized_vol=REALIZED_VOL, strategy_id=STRAT_MOMENTUM
        )

        assert tag == "vol_target_fallback", (
            f"Expected 'vol_target_fallback' for cold strategy, got '{tag}'"
        )
        expected = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0)
        assert math.isclose(weight, expected, rel_tol=1e-9)

    def test_backward_compat_no_regression_in_empty_store(self):
        """
        After Stage 1.7: calling _calculate_kelly_sizing(realized_vol)
        with no strategy_id on an EMPTY store must match pre-Stage-1.7
        behavior: vol-target fallback, tagged 'vol_target_fallback'.
        """
        store = TransactionsStore(db_url="sqlite:///:memory:")
        engine = StrategyEngine(transactions_store=store)

        weight, tag = engine._calculate_kelly_sizing(realized_vol=REALIZED_VOL)
        expected = volatility_target_weight(REALIZED_VOL, target_vol=0.10, max_leverage=2.0)

        assert tag == "vol_target_fallback"
        assert math.isclose(weight, expected, rel_tol=1e-9)
