"""
tests/test_kill_switch.py
==========================
Unit tests for execution/kill_switch.py and its integration with OrderManager.

Coverage
--------
* GlobalKillSwitch lifecycle: activate, deactivate, reason, idempotency.
* OrderManager raises KillSwitchActiveError BEFORE broker contact.
* Deactivating re-enables order submission.
* Kill-switch check precedes dedup (a known coid cannot bypass it).
* Dry-run does NOT bypass the kill switch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    TradeUpdateEvent,
)
from execution.kill_switch import GlobalKillSwitch, KillSwitchActiveError
from execution.order_manager import OrderManager


# ---------------------------------------------------------------------------
# Minimal broker stub
# ---------------------------------------------------------------------------

class MockBroker(BrokerBase):
    """Stub broker that records submit calls and always returns ACCEPTED."""

    def __init__(self):
        self.submit_count = 0

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.submit_count += 1
        return OrderResult(
            client_order_id=intent.client_order_id or "mock-id",
            broker_order_id="brk-001",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, client_order_id: str) -> bool:
        return True

    async def get_open_positions(self) -> list[PositionSnapshot]:
        return []

    async def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(equity=100_000.0, cash=50_000.0, buying_power=50_000.0)

    async def get_orders(self) -> list[OrderResult]:
        return []

    async def stream_trade_updates(self) -> AsyncGenerator[TradeUpdateEvent, None]:
        return
        yield  # make it an async generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_ks(tmp_path: Path) -> GlobalKillSwitch:
    """A GlobalKillSwitch pointing at a temp directory sentinel file."""
    return GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")


@pytest.fixture()
def broker() -> MockBroker:
    return MockBroker()


def _buy_intent(symbol: str = "AAPL") -> OrderIntent:
    return OrderIntent(
        strategy_id="test",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
    )


# ---------------------------------------------------------------------------
# GlobalKillSwitch unit tests
# ---------------------------------------------------------------------------

class TestGlobalKillSwitch:
    def test_initially_inactive(self, tmp_ks: GlobalKillSwitch):
        assert not tmp_ks.is_active()

    def test_activate_sets_active(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate(reason="circuit breaker")
        assert tmp_ks.is_active()

    def test_deactivate_removes_sentinel(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate()
        tmp_ks.deactivate()
        assert not tmp_ks.is_active()

    def test_reason_stored_and_retrieved(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate(reason="manual stop")
        assert "manual stop" in tmp_ks.reason()

    def test_reason_empty_when_inactive(self, tmp_ks: GlobalKillSwitch):
        assert tmp_ks.reason() == ""

    def test_activate_idempotent(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate(reason="first")
        tmp_ks.activate(reason="second")  # should not crash
        assert tmp_ks.is_active()

    def test_deactivate_idempotent(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.deactivate()  # called when not active — should not crash
        assert not tmp_ks.is_active()


# ---------------------------------------------------------------------------
# OrderManager + KillSwitch integration
# ---------------------------------------------------------------------------

class TestOrderManagerKillSwitch:
    def test_blocks_order_when_active(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        tmp_ks.activate(reason="test block")
        om = OrderManager(broker, kill_switch=tmp_ks)
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(om.submit_order_with_idempotency(_buy_intent()))
        assert broker.submit_count == 0

    def test_allows_order_when_inactive(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        om = OrderManager(broker, kill_switch=tmp_ks)
        result = asyncio.run(
            om.submit_order_with_idempotency(_buy_intent(), timestamp=datetime.now(timezone.utc))
        )
        assert result.status == OrderStatus.ACCEPTED
        assert broker.submit_count == 1

    def test_deactivate_re_enables_orders(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        tmp_ks.activate()
        tmp_ks.deactivate()
        om = OrderManager(broker, kill_switch=tmp_ks)
        result = asyncio.run(
            om.submit_order_with_idempotency(_buy_intent(), timestamp=datetime.now(timezone.utc))
        )
        assert result.status == OrderStatus.ACCEPTED

    def test_kill_switch_checked_before_dedup(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        """Even a previously submitted coid cannot bypass an active kill switch."""
        om = OrderManager(broker, kill_switch=tmp_ks)
        ts = datetime.now(timezone.utc)
        # Submit once successfully
        asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=ts))
        # Now activate kill switch
        tmp_ks.activate(reason="activated mid-test")
        # Attempt the same order again — should raise, not return the cached ACCEPTED
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=ts))

    def test_dry_run_does_not_bypass_kill_switch(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        """dry_run=True still raises KillSwitchActiveError; kill switch is not a 'safety bypass'."""
        tmp_ks.activate(reason="test dry-run block")
        om = OrderManager(broker, kill_switch=tmp_ks, dry_run=True)
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(om.submit_order_with_idempotency(_buy_intent()))
        assert broker.submit_count == 0
