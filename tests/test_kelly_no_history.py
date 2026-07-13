"""
InvestYo Quant Platform - Kelly Fallback (Insufficient History) Tests
=========================================================================
Verifies that StrategyEngine._calculate_kelly_sizing falls back to
volatility-target-only sizing (no Kelly multiplier) when the transactions
store has fewer than 30 closed trades, and switches to fractional Kelly once
enough history accumulates.

Note: _calculate_kelly_sizing() now returns (weight: float, path_tag: str).
Existing tests unpack the float weight; new tests also check the tag.
"""

import math
import pandas as pd
import pytest

from strategy_engine import StrategyEngine
from transactions_store import TransactionsStore
from sizing.vol_target import volatility_target_weight
from sizing.kelly import kelly_sizing_for_strategy


class _FakeStore:
    """Minimal transactions store returning a fixed closed-trades DataFrame."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def closed_trades_df(self) -> pd.DataFrame:
        return self._df


def _strategy_trades(strategy_id: str, n: int) -> pd.DataFrame:
    """n winning long trades tagged with strategy_id."""
    ts = pd.Timestamp("2024-01-01")
    return pd.DataFrame(
        [
            {
                "entry_price": 100.0,
                "exit_price": 105.0,
                "side": "long",
                "exit_ts": ts + pd.Timedelta(days=i),
                "strategy": strategy_id,
            }
            for i in range(n)
        ]
    )


@pytest.fixture
def empty_store() -> TransactionsStore:
    return TransactionsStore(db_url="sqlite:///:memory:")


def test_kelly_disabled_with_no_trades_scales_in_to_zero(empty_store):
    """WS3 cold-start scale-in: with 0 closed trades the vol-target fallback is
    ramped in by min(1, n/30) = 0, so the weight is ~0 (never the full
    vol-target jump)."""
    engine = StrategyEngine(transactions_store=empty_store)
    realized_vol = 0.20

    sizing, tag = engine._calculate_kelly_sizing(realized_vol=realized_vol)

    assert math.isclose(sizing, 0.0, abs_tol=1e-12)
    # Path tag records the scale-in factor and trade count for auditability.
    assert tag == "vol_target_fallback(scalein=0.00,n=0)"


def test_kelly_fewer_than_30_trades_scales_in_by_n_over_30(empty_store):
    """WS3 cold-start scale-in: 15 closed trades -> vol-target fallback weight is
    exactly (15/30) = 0.5x the un-scaled fallback weight."""
    now = pd.Timestamp.now("UTC")
    for i in range(15):  # below the 30-trade minimum
        trade_id = empty_store.record_trade(
            symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0
        )
        empty_store.close_trade(trade_id, exit_ts=now, exit_price=105.0)

    engine = StrategyEngine(transactions_store=empty_store)
    sizing, tag = engine._calculate_kelly_sizing(realized_vol=0.20)

    unscaled = volatility_target_weight(0.20, target_vol=0.10, max_leverage=2.0)
    assert math.isclose(sizing, 0.5 * unscaled, rel_tol=1e-9)  # 15/30 = 0.5
    assert tag == "vol_target_fallback(scalein=0.50,n=15)"


def test_kelly_enabled_once_history_sufficient(empty_store):
    """>=30 closed trades with a real edge -> fractional Kelly takes over,
    no longer matching the pure volatility-target weight."""
    now = pd.Timestamp.now("UTC")
    # 18 wins @ +10%, 12 losses @ -5% -> p=0.6, b=2.0
    for i in range(18):
        trade_id = empty_store.record_trade(
            symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0
        )
        empty_store.close_trade(trade_id, exit_ts=now + pd.Timedelta(days=i), exit_price=110.0)
    for i in range(12):
        trade_id = empty_store.record_trade(
            symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0
        )
        empty_store.close_trade(trade_id, exit_ts=now + pd.Timedelta(days=18 + i), exit_price=95.0)

    engine = StrategyEngine(transactions_store=empty_store)
    sizing, tag = engine._calculate_kelly_sizing(realized_vol=0.20)

    # p=0.6, b=2.0 -> full Kelly = (0.6*2 - 0.4)/2 = 0.4; half-Kelly = 0.2; cap=0.20 -> 0.20
    assert math.isclose(sizing, 0.20, rel_tol=1e-6)
    # Confirms this is NOT the pure vol-target fallback value (0.5), i.e. Kelly is active.
    vol_target_only = volatility_target_weight(0.20, target_vol=0.10, max_leverage=2.0)
    assert not math.isclose(sizing, vol_target_only, rel_tol=1e-6)
    # Path tag must indicate the aggregate Kelly path (strategy_id=None).
    assert tag == "aggregate_kelly"


def test_per_strategy_scale_in_zero_at_no_trades():
    """WS3 primary slot: kelly_sizing_for_strategy with 0 trades for the
    strategy -> vol-target fallback scaled by 0/30 = 0.0."""
    store = _FakeStore(_strategy_trades("other-strat", 40))  # none for our id
    weight, tag = kelly_sizing_for_strategy(
        store, strategy_id="my-strat", realized_vol=0.20, target_vol=0.10, max_leverage=2.0
    )
    assert math.isclose(weight, 0.0, abs_tol=1e-12)
    assert tag == "vol_target_fallback(scalein=0.00,n=0)"


def test_per_strategy_scale_in_half_at_15_trades():
    """WS3 primary slot: 15 per-strategy trades -> fallback weight is 0.5x the
    un-scaled vol-target weight (15/30)."""
    store = _FakeStore(_strategy_trades("my-strat", 15))
    weight, tag = kelly_sizing_for_strategy(
        store, strategy_id="my-strat", realized_vol=0.20, target_vol=0.10, max_leverage=2.0
    )
    unscaled = volatility_target_weight(0.20, target_vol=0.10, max_leverage=2.0)
    assert math.isclose(weight, 0.5 * unscaled, rel_tol=1e-9)
    assert tag == "vol_target_fallback(scalein=0.50,n=15)"


def test_kelly_no_realized_vol_and_no_history_returns_zero(empty_store):
    """No trade history AND no realized_vol available -> sizing weight is 0.0,
    never fabricated leverage."""
    engine = StrategyEngine(transactions_store=empty_store)
    sizing, tag = engine._calculate_kelly_sizing(realized_vol=None)
    assert sizing == 0.0
    assert tag == "cold_start_no_vol"


def test_max_position_weight_clamps_vol_target_fallback(empty_store):
    """A very low realized_vol would otherwise hit MAX_LEVERAGE (2.0x) via the
    vol-target fallback; settings.MAX_POSITION_WEIGHT (1.0) must clamp it.

    Uses 30 all-winning trades so the aggregate payoff ratio b is undefined
    (NaN) -> the vol-target fallback path is taken with n_trades=30, making the
    WS3 scale-in factor 1.0 so the MAX_POSITION_WEIGHT clamp is the binding
    constraint (not the scale-in)."""
    now = pd.Timestamp.now("UTC")
    for i in range(30):  # all winners -> b undefined -> vol-target fallback, n=30
        trade_id = empty_store.record_trade(
            symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0
        )
        empty_store.close_trade(trade_id, exit_ts=now + pd.Timedelta(days=i), exit_price=110.0)

    engine = StrategyEngine(transactions_store=empty_store)
    low_vol = 0.01  # target_vol(0.10)/0.01 = 10.0, capped by MAX_LEVERAGE to 2.0

    uncapped_vol_target = volatility_target_weight(low_vol, target_vol=0.10, max_leverage=2.0)
    assert uncapped_vol_target == 2.0  # confirms the raw function would exceed 1.0

    sizing, tag = engine._calculate_kelly_sizing(realized_vol=low_vol)
    assert sizing == 1.0  # clamped by settings.MAX_POSITION_WEIGHT, not 2.0
    # Scale-in is 1.0 at n=30, so the clamp (not the ramp) is what binds.
    assert tag == "vol_target_fallback(scalein=1.00,n=30)"
