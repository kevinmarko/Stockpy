"""
tests/test_order_manager_rate_limit.py
=======================================
Verifies OrderManager's LeakyBucketQueue gate is applied per REAL broker
call, not once per intent.

Covers the fix from a single gate check in submit_order_with_idempotency
(before _submit_with_retry) to a gate check inside _submit_with_retry,
immediately before each broker.submit_order attempt:
- A dry-run intent never touches the broker, so it must never consume a
  rate-limit token either (the old placement charged one regardless).
- A retried intent makes multiple real broker calls; each one must be
  individually gated (the old placement checked the bucket once for the
  whole intent, undercounting real broker traffic during a retry storm).
- A low-priority (BUY) intent that finds the bucket exhausted must be shed
  immediately, before ever calling the broker.
- A high-priority (SELL) intent must wait for a token rather than being
  shed, and must not block the event loop while waiting
  (execution/leaky_bucket_queue.py's await_or_shed, not wait_or_shed).
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from execution.broker_base import (
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from execution.leaky_bucket_queue import LeakyBucketQueue
from execution.order_manager import OrderManager

_FIXED_TS = datetime(2024, 1, 15, 10, 0, 0)


class CountingBroker(BrokerBase):
    """Broker stub that always succeeds, counting real calls."""

    def __init__(self) -> None:
        self.call_count = 0

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.call_count += 1
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id=f"mock-{self.call_count}",
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
        yield


class AlwaysErrorBroker(CountingBroker):
    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.call_count += 1
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id=None,
            status=OrderStatus.ERROR,
            error_message="always fails",
        )


def _buy_intent(symbol: str = "AAPL") -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strategy", symbol=symbol, side=OrderSide.BUY,
        qty=1.0, order_type=OrderType.MARKET,
    )


def _sell_intent(symbol: str = "AAPL") -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strategy", symbol=symbol, side=OrderSide.SELL,
        qty=1.0, order_type=OrderType.MARKET,
    )


def test_dry_run_consumes_no_token():
    broker = CountingBroker()
    om = OrderManager(broker, dry_run=True)
    om._queue = LeakyBucketQueue(capacity=200, refill_rate=3.33)
    tokens_before = om._queue.bucket.get_token_count()

    result = asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=_FIXED_TS))

    assert result.status == OrderStatus.ACCEPTED
    assert broker.call_count == 0
    assert om._queue.bucket.get_token_count() == pytest.approx(tokens_before, abs=0.05)


def test_low_priority_sheds_before_reaching_broker_when_exhausted():
    broker = CountingBroker()
    om = OrderManager(broker, dry_run=False)
    om._queue = LeakyBucketQueue(capacity=10, refill_rate=0.0)
    for _ in range(9):  # drain to 90% utilized -> over the 80% shed threshold
        om._queue.bucket.consume(1)

    result = asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=_FIXED_TS))

    assert result.status == OrderStatus.ERROR
    assert "Rate-limit" in (result.error_message or "")
    assert broker.call_count == 0, "shed BUY must never reach the broker"


def test_high_priority_waits_for_token_instead_of_being_shed():
    broker = CountingBroker()
    om = OrderManager(broker, dry_run=False)
    om._queue = LeakyBucketQueue(capacity=1, refill_rate=20.0)  # refills fast
    om._queue.bucket.consume(1)  # drain the single token

    result = asyncio.run(om.submit_order_with_idempotency(_sell_intent(), timestamp=_FIXED_TS))

    assert result.status == OrderStatus.ACCEPTED
    assert broker.call_count == 1, "SELL should wait for a token, then reach the broker"


def test_high_priority_wait_does_not_block_event_loop():
    """The wait for a token must yield the event loop (asyncio.sleep), not
    block it (time.sleep) -- a concurrent coroutine must keep progressing
    while a SELL order waits for capacity."""
    broker = CountingBroker()
    om = OrderManager(broker, dry_run=False)
    om._queue = LeakyBucketQueue(capacity=1, refill_rate=20.0)
    om._queue.bucket.consume(1)

    progress: list[int] = []

    async def other_coro():
        for i in range(5):
            progress.append(i)
            await asyncio.sleep(0.02)

    async def main():
        other = asyncio.create_task(other_coro())
        result = await om.submit_order_with_idempotency(_sell_intent(), timestamp=_FIXED_TS)
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(main())
    assert result.status == OrderStatus.ACCEPTED
    assert len(progress) >= 2, f"event loop was blocked -- other coroutine only ran {len(progress)} times"


def test_retry_gates_each_real_broker_call_separately():
    """Each retry attempt is a real broker call and must consume its own
    token -- the old placement gated the whole intent once, regardless of
    how many actual broker calls the retry loop made."""
    broker = AlwaysErrorBroker()
    om = OrderManager(broker, dry_run=False, max_retries=2, retry_delay_seconds=0.0)
    om._queue = LeakyBucketQueue(capacity=200, refill_rate=3.33)
    tokens_before = om._queue.bucket.get_token_count()

    asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=_FIXED_TS))

    assert broker.call_count == 3, "initial attempt + 2 retries = 3 real broker calls"
    tokens_after = om._queue.bucket.get_token_count()
    # 3 real broker calls -> 3 tokens consumed (allow small refill drift from wall-clock).
    assert tokens_before - tokens_after == pytest.approx(3.0, abs=0.1)
