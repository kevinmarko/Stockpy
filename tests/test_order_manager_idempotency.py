"""
tests/test_order_manager_idempotency.py
=========================================
Verifies that submitting the same OrderIntent twice via OrderManager results
in exactly ONE order at the broker (idempotency guarantee).

Uses a mock broker (no real network calls needed).

Tests
-----
- test_same_intent_submitted_once         — duplicate call → single broker call
- test_different_symbol_two_orders        — distinct intents → two broker calls
- test_dry_run_no_broker_call             — dry_run=True → zero broker calls
- test_client_order_id_deterministic      — same params → same client_order_id
- test_client_order_id_different_symbols  — different symbol → different ID
- test_retry_on_transient_error           — ERROR on first → retry → success
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.broker_base import (
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from execution.order_manager import OrderManager, make_client_order_id


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

class MockBroker(BrokerBase):
    """In-memory broker stub for idempotency tests."""

    def __init__(self) -> None:
        self.submitted: list[OrderIntent] = []

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.submitted.append(intent)
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id=f"mock-{len(self.submitted)}",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def get_open_positions(self):
        return []

    async def get_account(self):
        from execution.broker_base import AccountSnapshot
        return AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)

    async def get_orders(self, status=None, limit=100):
        return []

    async def stream_trade_updates(self):
        return
        yield  # make it a generator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(symbol: str = "AAPL", qty: float = 1.0) -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strategy",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.MARKET,
    )


_FIXED_TS = datetime(2024, 1, 15, 10, 0, 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_same_intent_submitted_once():
    """Submitting the same intent twice → only one broker call."""
    broker = MockBroker()
    om = OrderManager(broker, dry_run=False)

    intent = _intent("AAPL")
    r1 = asyncio.run(om.submit_order_with_idempotency(intent, timestamp=_FIXED_TS))
    r2 = asyncio.run(om.submit_order_with_idempotency(intent, timestamp=_FIXED_TS))

    assert len(broker.submitted) == 1, (
        f"Expected 1 broker call, got {len(broker.submitted)}"
    )
    assert r1.status == OrderStatus.ACCEPTED
    assert r2.status == OrderStatus.ACCEPTED  # returned from local dedup cache
    # Both calls return the same client_order_id
    assert r1.client_order_id == r2.client_order_id


def test_different_symbol_two_orders():
    """Two intents for different symbols both reach the broker."""
    broker = MockBroker()
    om = OrderManager(broker, dry_run=False)

    r1 = asyncio.run(om.submit_order_with_idempotency(_intent("AAPL"), timestamp=_FIXED_TS))
    r2 = asyncio.run(om.submit_order_with_idempotency(_intent("MSFT"), timestamp=_FIXED_TS))

    assert len(broker.submitted) == 2
    assert r1.client_order_id != r2.client_order_id


def test_dry_run_no_broker_call():
    """dry_run=True → zero network calls, status=ACCEPTED returned."""
    broker = MockBroker()
    om = OrderManager(broker, dry_run=True)

    result = asyncio.run(om.submit_order_with_idempotency(_intent("SPY"), timestamp=_FIXED_TS))

    assert len(broker.submitted) == 0, "dry_run must not reach broker.submit_order"
    assert result.status == OrderStatus.ACCEPTED
    assert result.broker_order_id is None  # no broker round-trip


def test_client_order_id_deterministic():
    """Same inputs always produce the same client_order_id."""
    coid1 = make_client_order_id(
        "strat", "AAPL", "buy", 1.0, timestamp=_FIXED_TS
    )
    coid2 = make_client_order_id(
        "strat", "AAPL", "buy", 1.0, timestamp=_FIXED_TS
    )
    assert coid1 == coid2


def test_client_order_id_different_symbols():
    """Different symbols → different client_order_ids."""
    coid_aapl = make_client_order_id("strat", "AAPL", "buy", 1.0, timestamp=_FIXED_TS)
    coid_msft = make_client_order_id("strat", "MSFT", "buy", 1.0, timestamp=_FIXED_TS)
    assert coid_aapl != coid_msft


def test_retry_on_transient_error():
    """First submission returns ERROR → retry → second attempt succeeds."""

    class FlakyBroker(MockBroker):
        def __init__(self):
            super().__init__()
            self._call_count = 0

        async def submit_order(self, intent: OrderIntent) -> OrderResult:
            self._call_count += 1
            if self._call_count == 1:
                return OrderResult(
                    client_order_id=intent.client_order_id or "",
                    broker_order_id=None,
                    status=OrderStatus.ERROR,
                    error_message="transient network hiccup",
                )
            return await super().submit_order(intent)

    broker = FlakyBroker()
    om = OrderManager(broker, dry_run=False, max_retries=1, retry_delay_seconds=0.0)

    result = asyncio.run(om.submit_order_with_idempotency(_intent("AAPL"), timestamp=_FIXED_TS))

    assert result.status == OrderStatus.ACCEPTED
    assert broker._call_count == 2, "Should have attempted exactly 2 times"
