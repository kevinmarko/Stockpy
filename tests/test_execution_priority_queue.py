"""
tests/test_execution_priority_queue.py
========================================
Unit tests for the opt-in leaky-bucket priority queue in
``execution/priority_queue.py`` (``settings.EXECUTION_PRIORITY_QUEUE_ENABLED``).

Proves:
* URGENT always dequeues before NORMAL, regardless of insertion order.
* FIFO ordering is preserved within the same priority tier.
* Leak-rate pacing actually delays admission (via injected ``time.monotonic``).
* A non-positive leak rate disables pacing entirely (immediate pass-through) --
  this is the "disabled mode" shape used when the feature flag is off.
"""

from __future__ import annotations

import asyncio
import time
from unittest import mock

import pytest

from execution.broker_base import OrderIntent, OrderPriority, OrderSide, OrderType
from execution.priority_queue import LeakyBucketPriorityQueue, classify_priority


def _intent(symbol: str, side: OrderSide = OrderSide.BUY) -> OrderIntent:
    return OrderIntent(strategy_id="test", symbol=symbol, side=side, qty=1.0, order_type=OrderType.MARKET)


class TestOrderingByPriority:
    def test_urgent_always_before_normal_regardless_of_insertion_order(self):
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue(leak_rate_per_sec=-1)  # pacing disabled
        q.push("normal-1", OrderPriority.NORMAL)
        q.push("normal-2", OrderPriority.NORMAL)
        q.push("urgent-1", OrderPriority.URGENT)
        q.push("normal-3", OrderPriority.NORMAL)
        q.push("urgent-2", OrderPriority.URGENT)

        popped = [q.pop() for _ in range(5)]
        # Both URGENT items first (FIFO among themselves), then both NORMAL groups.
        assert popped[:2] == ["urgent-1", "urgent-2"]
        assert popped[2:] == ["normal-1", "normal-2", "normal-3"]

    def test_fifo_within_same_tier(self):
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue(leak_rate_per_sec=-1)
        for i in range(5):
            q.push(f"item-{i}", OrderPriority.NORMAL)
        popped = [q.pop() for _ in range(5)]
        assert popped == [f"item-{i}" for i in range(5)]

    def test_len_reflects_queue_size(self):
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue()
        assert len(q) == 0
        q.push("a", OrderPriority.NORMAL)
        q.push("b", OrderPriority.URGENT)
        assert len(q) == 2
        q.pop()
        assert len(q) == 1

    def test_pop_empty_raises_index_error(self):
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue()
        with pytest.raises(IndexError):
            q.pop()


class TestLeakRatePacing:
    def test_disabled_pacing_never_sleeps(self):
        """leak_rate_per_sec <= 0 -> wait_for_slot returns immediately, every time."""
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue(leak_rate_per_sec=0)

        async def _run():
            with mock.patch("asyncio.sleep", side_effect=AssertionError("must not sleep when disabled")):
                await q.wait_for_slot()
                await q.wait_for_slot()
                await q.wait_for_slot()

        asyncio.run(_run())  # no AssertionError raised == no sleep calls

    def test_enabled_pacing_enforces_minimum_interval(self):
        """A positive leak rate must space consecutive admissions by >= 1/rate
        seconds -- proven via a real (small) rate and wall-clock timing."""
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue(leak_rate_per_sec=20.0)  # 50ms min interval

        async def _run():
            start = time.monotonic()
            await q.wait_for_slot()
            await q.wait_for_slot()
            await q.wait_for_slot()
            return time.monotonic() - start

        elapsed = asyncio.run(_run())
        # 3 admissions at 20/sec minimum spacing -> at least ~2 * 0.05s = 0.1s
        # for the 2nd and 3rd calls (first is immediate).
        assert elapsed >= 0.08, f"pacing was not enforced (elapsed={elapsed:.4f}s)"

    def test_drain_one_pops_after_waiting(self):
        q: LeakyBucketPriorityQueue = LeakyBucketPriorityQueue(leak_rate_per_sec=-1)
        q.push("only-item", OrderPriority.NORMAL)
        result = asyncio.run(q.drain_one())
        assert result == "only-item"
        assert len(q) == 0


class TestClassifyPriority:
    def test_sell_is_urgent(self):
        assert classify_priority(_intent("AAPL", OrderSide.SELL)) == OrderPriority.URGENT

    def test_buy_is_normal(self):
        assert classify_priority(_intent("AAPL", OrderSide.BUY)) == OrderPriority.NORMAL


class TestOrderIntentDefaultPriority:
    def test_default_priority_is_normal(self):
        """Every existing caller that never sets `priority` gets NORMAL --
        preserves today's undifferentiated submission order when the queue
        feature is disabled (the default)."""
        intent = OrderIntent(strategy_id="s", symbol="AAPL", side=OrderSide.BUY, qty=1.0)
        assert intent.priority == OrderPriority.NORMAL
