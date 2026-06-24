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


@pytest.fixture
def empty_store() -> TransactionsStore:
    return TransactionsStore(db_url="sqlite:///:memory:")


def test_kelly_disabled_with_no_trades_falls_back_to_vol_target(empty_store):
    engine = StrategyEngine(transactions_store=empty_store)
    realized_vol = 0.20

    sizing, _tag = engine._calculate_kelly_sizing(realized_vol=realized_vol)

    expected = volatility_target_weight(realized_vol, target_vol=0.10, max_leverage=2.0)
    assert math.isclose(sizing, expected, rel_tol=1e-9)
    assert math.isclose(sizing, 0.5, rel_tol=1e-9)  # 0.10 / 0.20


def test_kelly_disabled_with_fewer_than_30_trades_falls_back(empty_store):
    """<30 closed trades (below MIN_TRADES_REQUIRED) -> still vol-target-only."""
    now = pd.Timestamp.utcnow()
    for i in range(20):  # below the 30-trade minimum
        trade_id = empty_store.record_trade(
            symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0
        )
        empty_store.close_trade(trade_id, exit_ts=now, exit_price=105.0)

    engine = StrategyEngine(transactions_store=empty_store)
    sizing, _tag = engine._calculate_kelly_sizing(realized_vol=0.20)

    expected = volatility_target_weight(0.20, target_vol=0.10, max_leverage=2.0)
    assert math.isclose(sizing, expected, rel_tol=1e-9)


def test_kelly_enabled_once_history_sufficient(empty_store):
    """>=30 closed trades with a real edge -> fractional Kelly takes over,
    no longer matching the pure volatility-target weight."""
    now = pd.Timestamp.utcnow()
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


def test_kelly_no_realized_vol_and_no_history_returns_zero(empty_store):
    """No trade history AND no realized_vol available -> sizing weight is 0.0,
    never fabricated leverage."""
    engine = StrategyEngine(transactions_store=empty_store)
    sizing, tag = engine._calculate_kelly_sizing(realized_vol=None)
    assert sizing == 0.0
    assert tag == "cold_start_no_vol"


def test_max_position_weight_clamps_vol_target_fallback(empty_store):
    """A very low realized_vol would otherwise hit MAX_LEVERAGE (2.0x) via the
    vol-target fallback; settings.MAX_POSITION_WEIGHT (1.0) must clamp it."""
    engine = StrategyEngine(transactions_store=empty_store)
    low_vol = 0.01  # target_vol(0.10)/0.01 = 10.0, capped by MAX_LEVERAGE to 2.0

    uncapped_vol_target = volatility_target_weight(low_vol, target_vol=0.10, max_leverage=2.0)
    assert uncapped_vol_target == 2.0  # confirms the raw function would exceed 1.0

    sizing, _tag = engine._calculate_kelly_sizing(realized_vol=low_vol)
    assert sizing == 1.0  # clamped by settings.MAX_POSITION_WEIGHT, not 2.0
