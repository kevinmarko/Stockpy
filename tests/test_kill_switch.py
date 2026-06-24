"""
tests/test_kill_switch.py
=========================
Unit tests for ``execution/kill_switch.py``.

Tests:
* Activate / deactivate file lifecycle
* is_active() reflects file presence
* reason() returns stored text
* OrderManager.submit_order_with_idempotency raises KillSwitchActiveError when active
* Removing the file re-enables order submission
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

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
# Mock broker — accepts all orders in dry-run style
# ---------------------------------------------------------------------------

class MockBroker(BrokerBase):
    def __init__(self) -> None:
        self.submitted: list[OrderIntent] = []

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.submitted.append(intent)
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id="mock-broker-id",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def get_open_positions(self) -> list[PositionSnapshot]:
        return []

    async def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)

    async def get_orders(self, status=None, limit=100) -> list[OrderResult]:
        return []

    async def stream_trade_updates(self):
        return
        yield  # make it an async generator


# ---------------------------------------------------------------------------
# Kill-switch file lifecycle
# ---------------------------------------------------------------------------

class TestGlobalKillSwitch:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._sentinel = pathlib.Path(self._tmp.name) / "KILL_SWITCH"
        self.ks = GlobalKillSwitch(sentinel_file=self._sentinel)

    def teardown_method(self):
        self._tmp.cleanup()

    def test_initially_inactive(self):
        assert not self.ks.is_active()

    def test_activate_creates_file(self):
        self.ks.activate(reason="test")
        assert self._sentinel.exists()
        assert self.ks.is_active()

    def test_reason_stored_in_file(self):
        self.ks.activate(reason="market crash detected")
        assert "market crash detected" in self.ks.reason()

    def test_deactivate_removes_file(self):
        self.ks.activate(reason="test")
        self.ks.deactivate()
        assert not self._sentinel.exists()
        assert not self.ks.is_active()

    def test_deactivate_when_not_active_is_idempotent(self):
        # Should not raise
        self.ks.deactivate()
        assert not self.ks.is_active()

    def test_activate_is_atomic(self):
        """Activating twice should leave a single valid file, not corrupt it."""
        self.ks.activate(reason="first")
        self.ks.activate(reason="second")
        assert self.ks.is_active()
        reason = self.ks.reason()
        assert "second" in reason

    def test_reason_returns_empty_when_inactive(self):
        assert self.ks.reason() == ""


# ---------------------------------------------------------------------------
# OrderManager integration: kill switch blocks before broker contact
# ---------------------------------------------------------------------------

class TestKillSwitchBlocksOrders:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._sentinel = pathlib.Path(self._tmp.name) / "KILL_SWITCH"
        self.ks = GlobalKillSwitch(sentinel_file=self._sentinel)
        self.broker = MockBroker()
        self.om = OrderManager(self.broker, kill_switch=self.ks)

    def teardown_method(self):
        self._tmp.cleanup()

    def test_order_passes_when_kill_switch_inactive(self):
        intent = OrderIntent(
            strategy_id="test", symbol="SPY", side=OrderSide.BUY,
            qty=1.0, order_type=OrderType.MARKET,
        )
        result = asyncio.get_event_loop().run_until_complete(
            self.om.submit_order_with_idempotency(intent)
        )
        assert result.status == OrderStatus.ACCEPTED
        assert len(self.broker.submitted) == 1

    def test_order_blocked_when_kill_switch_active(self):
        self.ks.activate(reason="circuit breaker triggered")
        intent = OrderIntent(
            strategy_id="test", symbol="SPY", side=OrderSide.BUY,
            qty=1.0, order_type=OrderType.MARKET,
        )
        with pytest.raises(KillSwitchActiveError, match="circuit breaker triggered"):
            asyncio.get_event_loop().run_until_complete(
                self.om.submit_order_with_idempotency(intent)
            )
        # Broker must NOT have been called
        assert len(self.broker.submitted) == 0

    def test_deactivating_kill_switch_re_enables_orders(self):
        self.ks.activate(reason="test")
        self.ks.deactivate()
        intent = OrderIntent(
            strategy_id="test", symbol="SPY", side=OrderSide.BUY,
            qty=1.0, order_type=OrderType.MARKET,
        )
        result = asyncio.get_event_loop().run_until_complete(
            self.om.submit_order_with_idempotency(intent)
        )
        assert result.status == OrderStatus.ACCEPTED
        assert len(self.broker.submitted) == 1

    def test_kill_switch_checked_before_idempotency(self):
        """Kill switch must fire even for a previously-accepted order ID,
        to avoid a bypass where a known good coid could slip through."""
        intent = OrderIntent(
            strategy_id="test", symbol="AAPL", side=OrderSide.BUY,
            qty=1.0, order_type=OrderType.MARKET,
        )
        # First submit succeeds
        asyncio.get_event_loop().run_until_complete(
            self.om.submit_order_with_idempotency(intent)
        )
        # Activate kill switch
        self.ks.activate(reason="halt")
        # Same intent again → kill switch raises BEFORE any dedup check
        with pytest.raises(KillSwitchActiveError):
            asyncio.get_event_loop().run_until_complete(
                self.om.submit_order_with_idempotency(intent)
            )


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

def test_dry_run_does_not_submit_to_broker():
    with tempfile.TemporaryDirectory() as tmp:
        sentinel = pathlib.Path(tmp) / "KILL_SWITCH"
        ks = GlobalKillSwitch(sentinel_file=sentinel)
        broker = MockBroker()
        om = OrderManager(broker, dry_run=True, kill_switch=ks)
        intent = OrderIntent(
            strategy_id="test", symbol="TSLA", side=OrderSide.BUY,
            qty=5.0, order_type=OrderType.MARKET,
        )
        result = asyncio.get_event_loop().run_until_complete(
            om.submit_order_with_idempotency(intent)
        )
        assert result.status == OrderStatus.ACCEPTED
        assert result.broker_order_id is None
        # Broker submit was NOT called (dry_run intercepts in _submit_with_retry)
        assert len(broker.submitted) == 0
