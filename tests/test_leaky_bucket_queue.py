"""
tests/test_leaky_bucket_queue.py
================================
Unit tests for execution/leaky_bucket_queue.py

Tests:
- TokenBucket.consume() depletes tokens correctly
- TokenBucket refills over time
- should_shed_load() triggers at >80% utilization
- wait_or_shed(priority=0) returns False when over limit
- wait_or_shed(priority=1) returns True (waits for token)
- LeakyBucketQueue created with correct defaults
"""
from __future__ import annotations

import time
import pytest
from execution.leaky_bucket_queue import TokenBucket, LeakyBucketQueue


def test_token_bucket_initial_full():
    bucket = TokenBucket.create(capacity=10, refill_rate=1.0)
    assert bucket.get_token_count() == pytest.approx(10.0, abs=0.1)


def test_token_bucket_consume_reduces_tokens():
    bucket = TokenBucket.create(capacity=10, refill_rate=1.0)
    assert bucket.consume(3) is True
    remaining = bucket.get_token_count()
    assert remaining == pytest.approx(7.0, abs=0.1)


def test_token_bucket_consume_fails_when_empty():
    bucket = TokenBucket.create(capacity=5, refill_rate=1.0)
    # Drain completely
    for _ in range(5):
        bucket.consume(1)
    assert bucket.consume(1) is False


def test_token_bucket_refills_over_time():
    bucket = TokenBucket.create(capacity=10, refill_rate=10.0)  # 10 tokens/sec
    # Drain 5 tokens
    for _ in range(5):
        bucket.consume(1)
    # Wait for ~0.5 s → should have refilled ~5 tokens
    time.sleep(0.5)
    remaining = bucket.get_token_count()
    assert remaining >= 4.5, f"Expected >=4.5 tokens after refill, got {remaining}"


def test_token_bucket_does_not_exceed_capacity():
    bucket = TokenBucket.create(capacity=5, refill_rate=100.0)
    # Wait long enough for overflow
    time.sleep(0.2)
    assert bucket.get_token_count() <= 5.0


def test_should_shed_load_when_over_80_percent():
    # Create a bucket and drain past 80%
    queue = LeakyBucketQueue(capacity=10, refill_rate=0.01)  # near-zero refill
    # Drain 9 out of 10 tokens (90% utilized)
    for _ in range(9):
        queue.bucket.consume(1)
    assert queue.should_shed_load() is True


def test_should_not_shed_load_when_under_80_percent():
    queue = LeakyBucketQueue(capacity=10, refill_rate=0.01)
    # Only drain 5 tokens (50% utilized)
    for _ in range(5):
        queue.bucket.consume(1)
    assert queue.should_shed_load() is False


def test_wait_or_shed_returns_false_for_low_priority_under_load():
    queue = LeakyBucketQueue(capacity=10, refill_rate=0.01)
    # Drain 9 tokens → >80% utilized
    for _ in range(9):
        queue.bucket.consume(1)
    result = queue.wait_or_shed(priority=0)
    assert result is False


def test_wait_or_shed_returns_true_for_fresh_bucket():
    queue = LeakyBucketQueue(capacity=10, refill_rate=100.0)
    result = queue.wait_or_shed(priority=0)
    assert result is True


def test_leaky_bucket_queue_default_capacity():
    queue = LeakyBucketQueue()
    assert queue.bucket.capacity == 200
    assert queue.load_shed_threshold == pytest.approx(0.8)
