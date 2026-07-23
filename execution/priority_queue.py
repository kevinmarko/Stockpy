"""
execution/priority_queue.py
============================
Opt-in leaky-bucket priority queue for order SUBMISSION ORDERING/PACING only
(``settings.EXECUTION_PRIORITY_QUEUE_ENABLED``, default False).

This module controls **which OrderIntent goes to
OrderManager.submit_order_with_idempotency next**, and paces submissions to a
configurable rate. It does NOT duplicate, replace, or bypass:

* ``execution/risk_gate.py``'s ``PreTradeRiskGate.max_order_rate_check`` — the
  hard cap on order submissions per minute. That remains the sole
  authorization gate; this queue can only ever slow submissions down or
  reorder them, never let more through than the risk gate would otherwise
  allow.
* ``execution/kill_switch.py`` — the global halt authority.

URGENT-priority intents (risk-reducing: SELL/TRIM/stop-loss/exit) always
drain before NORMAL-priority intents (new BUY entries), regardless of
insertion order — the intent behind "prioritizes risk-reducing executions
... during heavy load" from the original roadmap item. Within the same
priority tier, ordering is FIFO by insertion sequence.

Disabled (default) mode is a pure FIFO pass-through with zero pacing delay,
so the queue is a behavior-preserving no-op unless explicitly enabled.
"""

from __future__ import annotations

import heapq
import itertools
import time
from typing import Generic, List, Optional, TypeVar

from execution.broker_base import OrderIntent, OrderPriority

T = TypeVar("T")

# URGENT must sort before NORMAL in the heap (lower value = higher priority).
_PRIORITY_RANK = {
    OrderPriority.URGENT: 0,
    OrderPriority.NORMAL: 1,
}


class LeakyBucketPriorityQueue(Generic[T]):
    """Sync, in-process priority queue with a leaky-bucket admission pace.

    Not thread-safe by design — intended to be drained sequentially inside a
    single caller's existing loop (e.g.
    ``main_orchestrator._execute_broker_orders``), not shared across threads
    or a separate event loop.

    Parameters
    ----------
    leak_rate_per_sec:
        Maximum admissions per second when draining via ``wait_for_slot``.
        ``<= 0`` disables pacing entirely (every ``wait_for_slot`` call
        returns immediately) — used for the disabled/pass-through mode.
    """

    def __init__(self, leak_rate_per_sec: float = 2.0) -> None:
        self._leak_rate = leak_rate_per_sec
        self._heap: List[tuple] = []
        self._counter = itertools.count()  # stable FIFO tiebreaker within a tier
        self._last_admitted_at: Optional[float] = None

    def push(self, item: T, priority: OrderPriority) -> None:
        """Enqueue ``item`` under ``priority``. FIFO within the same tier."""
        rank = _PRIORITY_RANK.get(priority, _PRIORITY_RANK[OrderPriority.NORMAL])
        heapq.heappush(self._heap, (rank, next(self._counter), item))

    def __len__(self) -> int:
        return len(self._heap)

    def pop(self) -> T:
        """Pop and return the highest-priority (then oldest) item. Raises
        ``IndexError`` on an empty queue, matching ``heapq``/list semantics."""
        _, _, item = heapq.heappop(self._heap)
        return item

    async def wait_for_slot(self) -> None:
        """Block (via ``asyncio.sleep``) until the leaky bucket has capacity
        for one more admission. A non-positive ``leak_rate_per_sec`` disables
        pacing — returns immediately every time."""
        import asyncio

        if self._leak_rate <= 0:
            return
        min_interval = 1.0 / self._leak_rate
        now = time.monotonic()
        if self._last_admitted_at is not None:
            elapsed = now - self._last_admitted_at
            remaining = min_interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_admitted_at = time.monotonic()

    async def drain_one(self) -> T:
        """Wait for a pacing slot, then pop and return the next item.
        Convenience combinator for the common "pace then submit" loop shape."""
        await self.wait_for_slot()
        return self.pop()


def classify_priority(intent: OrderIntent) -> OrderPriority:
    """Default classification: SELL/TRIM-flavored intents are URGENT
    (risk-reducing), everything else is NORMAL. Callers that already know an
    intent's semantic role (e.g. main_orchestrator's existing SELL/TRIM/BUY
    branching) should set ``OrderIntent.priority`` directly rather than
    relying on this heuristic, which only inspects ``side``."""
    from execution.broker_base import OrderSide

    if intent.side == OrderSide.SELL:
        return OrderPriority.URGENT
    return OrderPriority.NORMAL
