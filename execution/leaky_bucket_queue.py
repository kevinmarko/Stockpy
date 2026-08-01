"""
execution/leaky_bucket_queue.py
===============================
Implements a Token Bucket (often conflated with leaky bucket) queue for API rate limiting
and load shedding. It tracks token usage and provides backpressure/load-shedding logic
when volume exceeds 80% of the exchange limit.
"""
from __future__ import annotations

import time
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger(__name__)

@dataclass
class TokenBucket:
    capacity: int
    refill_rate: float # tokens per second
    _tokens: float
    _last_refill: float

    @classmethod
    def create(cls, capacity: int, refill_rate: float) -> "TokenBucket":
        return cls(
            capacity=capacity,
            refill_rate=refill_rate,
            _tokens=float(capacity),
            _last_refill=time.monotonic(),
        )
    
    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self.refill_rate
        self._tokens = min(float(self.capacity), self._tokens + new_tokens)
        self._last_refill = now

    def get_token_count(self) -> float:
        self._refill()
        return self._tokens

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class LeakyBucketQueue:
    """Queue wrapper that enforces Token Bucket rate limits and load shedding."""
    
    def __init__(self, capacity: int = 200, refill_rate: float = 10.0):
        # Default Alpaca rate limit is ~200 requests/minute depending on endpoint
        self.bucket = TokenBucket.create(capacity=capacity, refill_rate=refill_rate)
        self.queue_depth = 0
        self.load_shed_threshold = 0.8  # 80% of capacity

    def should_shed_load(self) -> bool:
        """Returns True if volume > 80% of exchange limit, triggering load shedding."""
        tokens_available = self.bucket.get_token_count()
        utilization = 1.0 - (tokens_available / self.bucket.capacity)
        return utilization > self.load_shed_threshold

    def wait_or_shed(self, priority: int = 0) -> bool:
        """
        Consume a token. If load shedding is active, low priority tasks (priority=0)
        are shed (returns False). High priority tasks (priority>0 like stop-loss/sell-to-close)
        will wait for a token.

        Synchronous, blocking variant — safe to call from plain sync code.
        Callers running inside an asyncio event loop (e.g. the async order
        submission path in execution/order_manager.py) MUST use
        ``await_or_shed`` instead: this method's ``time.sleep`` spin-wait
        would otherwise block the whole event loop for the duration of the
        wait, stalling every other coroutine sharing it.
        """
        # If we are heavily utilized and this is a low-priority request (e.g. balance check)
        if self.should_shed_load() and priority == 0:
            logger.warning("LeakyBucketQueue: Shedding load for low priority request.")
            return False

        # Consume a token (spin-wait if high priority and exhausted)
        while not self.bucket.consume(1):
            if priority == 0:
                return False
            time.sleep(0.1)

        return True

    async def await_or_shed(self, priority: int = 0) -> bool:
        """Async counterpart of ``wait_or_shed`` — identical semantics, but
        spin-waits via ``asyncio.sleep`` instead of ``time.sleep`` so a
        high-priority wait yields the event loop instead of blocking it."""
        if self.should_shed_load() and priority == 0:
            logger.warning("LeakyBucketQueue: Shedding load for low priority request.")
            return False

        while not self.bucket.consume(1):
            if priority == 0:
                return False
            await asyncio.sleep(0.1)

        return True
