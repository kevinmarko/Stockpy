"""
tests/test_multi_broker_gateway.py
==================================
Comprehensive offline unit tests for execution/multi_broker_gateway.py.

Verifies:
1. Broker Registration & Priority Hierarchy Management
2. Health Checks, Latency Heartbeat Monitor & Connection States
3. Circuit Breaker State Transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
4. Automated Failover Routing & Multi-Venue Cascading
5. Manual Operator Override Toggles & Resets
6. Order Execution Lifecycle, Multi-Leg Options, Dry-Run, and Cancellations
7. Position and Account State Aggregation
8. Trade Update Multiplexed Streaming
9. Latency Percentiles, Degraded State & Fault Injection
"""

import asyncio
from datetime import datetime, timezone
import pytest

from execution.broker_base import (
    AccountSnapshot,
    OrderIntent,
    OrderPriority,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    TradeUpdateEvent,
)
from execution.multi_broker_gateway import (
    AlpacaBrokerAdapter,
    BaseBrokerAdapter,
    BrokerHealthStatus,
    BrokerMetrics,
    BrokerNotRegisteredError,
    BrokerType,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    ConnectionState,
    FailoverMode,
    FMPPaperBrokerAdapter,
    GatewayStatusSnapshot,
    InteractiveBrokersAdapter,
    MultiBrokerGateway,
    MultiBrokerGatewayError,
    NoHealthyBrokerError,
    RobinhoodBrokerAdapter,
    RoutingAuditTrail,
    TradierBrokerAdapter,
)


# ---------------------------------------------------------------------------
# Test Helpers & Controllable Mock Adapters
# ---------------------------------------------------------------------------

class MockBrokerAdapter(BaseBrokerAdapter):
    """Controllable mock broker adapter for deterministic unit testing."""
    __test__ = False

    def __init__(self, broker_id: str, name: str = "Test Mock", latency_ms: float = 0.0) -> None:
        super().__init__(
            broker_id=broker_id,
            broker_type=BrokerType.CUSTOM,
            name=name,
            simulated=True,
            circuit_breaker_config=CircuitBreakerConfig(
                max_consecutive_failures=3,
                latency_threshold_ms=50.0,
                cooldown_seconds=0.1,  # Fast cooldown for unit tests
                half_open_probe_successes=2,
            ),
        )
        self.set_simulated_latency(latency_ms)


# ---------------------------------------------------------------------------
# 1. Broker Registration & Lifecycle Tests
# ---------------------------------------------------------------------------

def test_gateway_initialization_and_registration():
    gateway = MultiBrokerGateway()
    assert gateway.list_brokers() == []
    assert gateway.get_priority_hierarchy() == []

    b1 = MockBrokerAdapter("broker_a")
    b2 = MockBrokerAdapter("broker_b")

    gateway.register_broker(b1)
    gateway.register_broker(b2)

    assert gateway.list_brokers() == ["broker_a", "broker_b"]
    assert gateway.get_priority_hierarchy() == ["broker_a", "broker_b"]
    assert gateway.get_broker("broker_a") is b1
    assert gateway.get_broker("broker_b") is b2
    assert gateway.get_broker("non_existent") is None


def test_gateway_registration_with_priority_index():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("broker_a")
    b2 = MockBrokerAdapter("broker_b")
    b3 = MockBrokerAdapter("broker_c")

    gateway.register_broker(b1)
    gateway.register_broker(b2)
    # Insert b3 at the very top (index 0)
    gateway.register_broker(b3, priority_index=0)

    assert gateway.get_priority_hierarchy() == ["broker_c", "broker_a", "broker_b"]


def test_gateway_deregistration():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("broker_a")
    b2 = MockBrokerAdapter("broker_b")

    gateway.register_broker(b1)
    gateway.register_broker(b2)
    gateway.set_manual_override("broker_a")

    gateway.deregister_broker("broker_a")
    assert gateway.list_brokers() == ["broker_b"]
    assert gateway.get_priority_hierarchy() == ["broker_b"]
    assert gateway.get_manual_override() is None


def test_gateway_create_default_factory():
    gateway = MultiBrokerGateway.create_default(simulated=True, primary="alpaca")
    brokers = gateway.list_brokers()

    assert "alpaca" in brokers
    assert "interactive_brokers" in brokers
    assert "tradier" in brokers
    assert "robinhood" in brokers
    assert "fmp_paper" in brokers

    hierarchy = gateway.get_priority_hierarchy()
    assert hierarchy[0] == "alpaca"
    assert len(hierarchy) == 5


def test_gateway_create_default_with_custom_primary():
    gateway = MultiBrokerGateway.create_default(simulated=True, primary="interactive_brokers")
    hierarchy = gateway.get_priority_hierarchy()
    assert hierarchy[0] == "interactive_brokers"


# ---------------------------------------------------------------------------
# 2. Broker Health, Latency Heartbeat & Metrics Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_broker_metrics_tracking():
    metrics = BrokerMetrics()
    assert metrics.total_requests == 0
    assert metrics.rolling_error_rate == 0.0

    metrics.record_success(latency_ms=10.0)
    metrics.record_success(latency_ms=20.0)
    assert metrics.total_requests == 2
    assert metrics.successful_requests == 2
    assert metrics.failed_requests == 0
    assert metrics.consecutive_failures == 0
    assert metrics.consecutive_successes == 2
    assert metrics.avg_latency_ms == 15.0
    assert metrics.rolling_error_rate == 0.0
    assert metrics.p95_latency_ms > 0.0

    metrics.record_failure(error_msg="Timeout", latency_ms=50.0)
    assert metrics.total_requests == 3
    assert metrics.failed_requests == 1
    assert metrics.consecutive_failures == 1
    assert metrics.consecutive_successes == 0
    assert metrics.last_error == "Timeout"
    assert metrics.rolling_error_rate == pytest.approx(1.0 / 3.0)


@pytest.mark.anyio
async def test_heartbeat_ping_success_and_failure():
    gateway = MultiBrokerGateway()
    adapter = MockBrokerAdapter("broker_a", latency_ms=5.0)
    gateway.register_broker(adapter)

    lat = await gateway.ping_broker("broker_a")
    assert lat >= 0.0
    assert adapter.metrics.successful_requests == 1
    assert adapter.connection_state == ConnectionState.CONNECTED

    # Test ping when forced error is set
    adapter.set_forced_error("Network Timeout")
    lat_err = await gateway.ping_broker("broker_a")
    assert lat_err >= 0.0
    assert adapter.metrics.failed_requests == 1
    assert adapter.connection_state == ConnectionState.FAILING
    assert adapter.metrics.last_error == "Broker 'broker_a' forced error: Network Timeout"


@pytest.mark.anyio
async def test_heartbeat_high_latency_degraded_state():
    gateway = MultiBrokerGateway()
    adapter = MockBrokerAdapter("broker_slow", latency_ms=60.0)
    gateway.register_broker(adapter)

    await gateway.ping_broker("broker_slow")
    assert adapter.connection_state == ConnectionState.DEGRADED
    health = adapter.get_health_status()
    assert "Degraded Latency" in health.status_message


@pytest.mark.anyio
async def test_heartbeat_unregistered_broker():
    gateway = MultiBrokerGateway()
    with pytest.raises(BrokerNotRegisteredError):
        await gateway.ping_broker("unknown_broker")


@pytest.mark.anyio
async def test_run_heartbeat_cycle_concurrent():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1", latency_ms=2.0)
    b2 = MockBrokerAdapter("b2", latency_ms=4.0)
    b3 = MockBrokerAdapter("b3", latency_ms=1.0)
    b3.set_forced_error("Offline")

    gateway.register_broker(b1)
    gateway.register_broker(b2)
    gateway.register_broker(b3)

    health_map = await gateway.run_heartbeat_cycle()
    assert "b1" in health_map
    assert "b2" in health_map
    assert "b3" in health_map

    assert health_map["b1"].is_healthy is True
    assert health_map["b2"].is_healthy is True
    assert health_map["b3"].is_healthy is False


@pytest.mark.anyio
async def test_heartbeat_monitor_background_loop():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1", latency_ms=1.0)
    gateway.register_broker(b1)

    await gateway.heartbeat_monitor.start(interval_seconds=0.05)
    assert gateway.heartbeat_monitor._running is True

    await asyncio.sleep(0.15)
    assert b1.metrics.total_requests >= 2

    await gateway.heartbeat_monitor.stop()
    assert gateway.heartbeat_monitor._running is False


# ---------------------------------------------------------------------------
# 3. Circuit Breaker State Machine Tests
# ---------------------------------------------------------------------------

def test_circuit_breaker_consecutive_failure_trip():
    cfg = CircuitBreakerConfig(max_consecutive_failures=3, cooldown_seconds=10.0)
    cb = CircuitBreaker("test_broker", cfg)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1st and 2nd failures
    cb.record_failure("err 1", consecutive_failures=1)
    assert cb.state == CircuitState.CLOSED
    cb.record_failure("err 2", consecutive_failures=2)
    assert cb.state == CircuitState.CLOSED

    # 3rd failure reaches threshold -> Trips to OPEN
    cb.record_failure("err 3", consecutive_failures=3)
    assert cb.state == CircuitState.OPEN
    assert "Consecutive failures threshold reached" in str(cb.trip_reason)
    assert cb.can_execute() is False


def test_circuit_breaker_error_rate_trip():
    cfg = CircuitBreakerConfig(
        max_consecutive_failures=10,
        error_rate_threshold=0.5,
        min_requests_for_error_rate=4,
    )
    cb = CircuitBreaker("test_broker", cfg)

    # 2 failures out of 4 requests (50% error rate)
    cb.record_failure("err", consecutive_failures=1, rolling_error_rate=0.50, total_requests=4)
    assert cb.state == CircuitState.OPEN
    assert "Error rate 50.0% exceeded threshold" in str(cb.trip_reason)


def test_circuit_breaker_latency_trip():
    """A broker that is consistently slow but never errors and never crosses
    the error-rate threshold must still trip -- the third documented OR
    condition (see CircuitBreaker's class docstring) that record_success
    previously only logged a WARNING for and never actually enforced."""
    cfg = CircuitBreakerConfig(max_consecutive_failures=3, latency_threshold_ms=50.0)
    cb = CircuitBreaker("test_broker", cfg)

    cb.record_success(latency_ms=60.0)
    assert cb.state == CircuitState.CLOSED
    cb.record_success(latency_ms=60.0)
    assert cb.state == CircuitState.CLOSED

    # 3rd consecutive latency breach reaches threshold -> trips to OPEN
    cb.record_success(latency_ms=60.0)
    assert cb.state == CircuitState.OPEN
    assert "Latency" in str(cb.trip_reason)
    assert cb.can_execute() is False


def test_circuit_breaker_latency_streak_reset_by_fast_response():
    """A single fast response resets the consecutive-latency-breach streak
    so the breaker doesn't trip on isolated slow responses interspersed with
    healthy ones."""
    cfg = CircuitBreakerConfig(max_consecutive_failures=3, latency_threshold_ms=50.0)
    cb = CircuitBreaker("test_broker", cfg)

    cb.record_success(latency_ms=60.0)
    cb.record_success(latency_ms=60.0)
    cb.record_success(latency_ms=10.0)  # fast -- resets the streak
    cb.record_success(latency_ms=60.0)
    cb.record_success(latency_ms=60.0)

    assert cb.state == CircuitState.CLOSED
    assert cb._consecutive_latency_breaches == 2


@pytest.mark.anyio
async def test_circuit_breaker_cooldown_and_half_open_recovery():
    cfg = CircuitBreakerConfig(
        max_consecutive_failures=2,
        cooldown_seconds=0.05,
        half_open_probe_successes=2,
    )
    cb = CircuitBreaker("test_broker", cfg)

    # Trip to OPEN
    cb.record_failure("err 1", consecutive_failures=2)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Wait for cooldown to expire
    await asyncio.sleep(0.07)

    # Can execute should transition to HALF_OPEN
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # 1st probe success
    cb.record_success(latency_ms=10.0)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.half_open_successes == 1

    # 2nd probe success -> Transitions back to CLOSED!
    cb.record_success(latency_ms=10.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True


def test_circuit_breaker_half_open_probe_failure_re_trip():
    cfg = CircuitBreakerConfig(cooldown_seconds=0.0, half_open_probe_successes=2)
    cb = CircuitBreaker("test_broker", cfg)
    cb.trip("manual trip")

    # Transitions to HALF_OPEN
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Failure during probe immediately trips back to OPEN
    cb.record_failure("Probe failed")
    assert cb.state == CircuitState.OPEN
    assert "Probe failure in HALF_OPEN state" in str(cb.trip_reason)


def test_circuit_breaker_manual_operator_controls():
    cb = CircuitBreaker("test_broker")
    assert cb.state == CircuitState.CLOSED

    cb.trip("operator maintenance")
    assert cb.state == CircuitState.OPEN
    assert cb.trip_reason == "operator maintenance"

    cb.reset("maintenance complete")
    assert cb.state == CircuitState.CLOSED
    assert cb.trip_reason is None


# ---------------------------------------------------------------------------
# 4. Automated Failover & Order Routing Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_normal_order_execution_on_primary():
    gateway = MultiBrokerGateway()
    primary = MockBrokerAdapter("primary")
    fallback = MockBrokerAdapter("fallback")

    gateway.register_broker(primary)
    gateway.register_broker(fallback)

    intent = OrderIntent(
        strategy_id="test_strat",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=10.0,
        limit_price=150.0,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.FILLED
    assert res.filled_qty == 10.0
    assert res.filled_avg_price == 150.0
    assert "primary-" in str(res.broker_order_id)

    # Telemetry check
    status = gateway.get_gateway_status()
    assert status.total_orders_routed == 1
    assert status.total_failovers == 0

    audits = gateway.get_routing_audits()
    assert len(audits) == 1
    assert audits[0].executed_broker_id == "primary"
    assert audits[0].was_failover is False


@pytest.mark.anyio
async def test_automated_failover_when_primary_errors():
    gateway = MultiBrokerGateway()
    primary = MockBrokerAdapter("primary")
    fallback = MockBrokerAdapter("fallback")

    gateway.register_broker(primary)
    gateway.register_broker(fallback)

    # Make primary fail
    primary.set_forced_error("Broker connectivity loss")

    intent = OrderIntent(
        strategy_id="test_strat",
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=5.0,
        limit_price=120.0,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.FILLED
    assert "fallback-" in str(res.broker_order_id)

    # Verify failover telemetry
    status = gateway.get_gateway_status()
    assert status.total_orders_routed == 1
    assert status.total_failovers == 1
    assert "fallback" in str(status.last_failover_reason)

    audits = gateway.get_routing_audits()
    assert len(audits) == 1
    assert audits[0].primary_broker_id == "primary"
    assert audits[0].executed_broker_id == "fallback"
    assert audits[0].was_failover is True
    assert len(audits[0].attempts) == 2
    assert audits[0].attempts[0].broker_id == "primary"
    assert audits[0].attempts[0].success is False
    assert audits[0].attempts[1].broker_id == "fallback"
    assert audits[0].attempts[1].success is True


@pytest.mark.anyio
async def test_cascading_failover_across_multi_brokers():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")
    b3 = MockBrokerAdapter("b3")

    b1.set_forced_error("b1 down")
    b2.set_forced_error("b2 down")

    gateway.register_broker(b1)
    gateway.register_broker(b2)
    gateway.register_broker(b3)

    intent = OrderIntent(
        strategy_id="strat",
        symbol="MSFT",
        side=OrderSide.SELL,
        qty=2.0,
        limit_price=400.0,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.FILLED
    assert "b3-" in str(res.broker_order_id)

    audits = gateway.get_routing_audits()
    assert audits[-1].executed_broker_id == "b3"
    assert audits[-1].was_failover is True
    assert len(audits[-1].attempts) == 3


@pytest.mark.anyio
async def test_all_brokers_exhausted_failure():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")

    b1.set_forced_error("b1 down")
    b2.set_forced_error("b2 down")

    gateway.register_broker(b1)
    gateway.register_broker(b2)

    intent = OrderIntent(
        strategy_id="strat",
        symbol="TSLA",
        side=OrderSide.BUY,
        qty=1.0,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.ERROR
    assert "b2 down" in str(res.error_message)

    audits = gateway.get_routing_audits()
    assert audits[-1].final_status == OrderStatus.ERROR
    assert audits[-1].executed_broker_id is None


@pytest.mark.anyio
async def test_failover_disabled_mode():
    gateway = MultiBrokerGateway(failover_mode=FailoverMode.DISABLED)
    primary = MockBrokerAdapter("primary")
    fallback = MockBrokerAdapter("fallback")

    primary.set_forced_error("Primary failed")
    gateway.register_broker(primary)
    gateway.register_broker(fallback)

    intent = OrderIntent(
        strategy_id="strat",
        symbol="AMD",
        side=OrderSide.BUY,
        qty=10.0,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.ERROR
    assert "Primary failed" in str(res.error_message)

    audits = gateway.get_routing_audits()
    # With failover disabled, only 1 attempt made
    assert len(audits[-1].attempts) == 1
    assert audits[-1].attempts[0].broker_id == "primary"


# ---------------------------------------------------------------------------
# 5. Manual Operator Override & Priority Hierarchy Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_manual_operator_override_routing():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")
    b3 = MockBrokerAdapter("b3")

    gateway.register_broker(b1)
    gateway.register_broker(b2)
    gateway.register_broker(b3)

    # Force route to b3 directly
    gateway.set_manual_override("b3")
    assert gateway.get_manual_override() == "b3"
    assert gateway.resolve_active_broker().broker_id == "b3"

    intent = OrderIntent(
        strategy_id="strat",
        symbol="AMZN",
        side=OrderSide.BUY,
        qty=15.0,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.FILLED
    assert "b3-" in str(res.broker_order_id)

    # Clear override and verify return to b1
    gateway.clear_manual_override()
    assert gateway.get_manual_override() is None
    assert gateway.resolve_active_broker().broker_id == "b1"


def test_manual_override_unregistered_broker_raises():
    gateway = MultiBrokerGateway()
    with pytest.raises(BrokerNotRegisteredError):
        gateway.set_manual_override("ghost_broker")


def test_set_priority_hierarchy_reordering():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")
    b3 = MockBrokerAdapter("b3")

    gateway.register_broker(b1)
    gateway.register_broker(b2)
    gateway.register_broker(b3)

    gateway.set_priority_hierarchy(["b3", "b2", "b1"])
    assert gateway.get_priority_hierarchy() == ["b3", "b2", "b1"]
    assert gateway.resolve_active_broker().broker_id == "b3"


# ---------------------------------------------------------------------------
# 6. Order Lifecycle, Dry-Run, Multi-Leg & Cancellations Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_dry_run_submission():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    gateway.register_broker(b1)

    intent = OrderIntent(
        strategy_id="strat",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=100.0,
        dry_run=True,
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.ACCEPTED
    assert res.broker_order_id is None

    # Account balances should NOT have changed
    acc = await gateway.get_account()
    assert acc.cash == 100_000.0


@pytest.mark.anyio
async def test_multi_leg_options_order_execution():
    gateway = MultiBrokerGateway()
    tradier = TradierBrokerAdapter(simulated=True)
    gateway.register_broker(tradier)

    # Bull call spread intent
    intent = OrderIntent(
        strategy_id="options_spread_strat",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=2.0,
        limit_price=3.50,
        legs=[
            {"symbol": "AAPL260918C00150000", "side": OrderSide.BUY, "ratio_qty": 1.0},
            {"symbol": "AAPL260918C00160000", "side": OrderSide.SELL, "ratio_qty": 1.0},
        ],
    )

    res = await gateway.submit_order(intent)
    assert res.status == OrderStatus.FILLED
    assert res.filled_qty == 2.0
    assert res.filled_avg_price == 3.50
    assert "tradier-" in str(res.broker_order_id)


@pytest.mark.anyio
async def test_order_cancellation():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    gateway.register_broker(b1)

    # Inject order directly in PENDING state
    b1._orders["test-ord-123"] = OrderResult(
        client_order_id="coid-1",
        broker_order_id="test-ord-123",
        status=OrderStatus.ACCEPTED,
    )

    # Cancel via gateway
    cancelled = await gateway.cancel_order("test-ord-123")
    assert cancelled is True
    assert b1._orders["test-ord-123"].status == OrderStatus.CANCELED

    # Cancel non-existent
    cancelled_fake = await gateway.cancel_order("fake-id")
    assert cancelled_fake is False


@pytest.mark.anyio
async def test_get_orders_filtering():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    gateway.register_broker(b1)

    intent1 = OrderIntent(strategy_id="s1", symbol="AAPL", side=OrderSide.BUY, qty=1.0)
    intent2 = OrderIntent(strategy_id="s2", symbol="MSFT", side=OrderSide.BUY, qty=2.0)

    await gateway.submit_order(intent1)
    await gateway.submit_order(intent2)

    all_orders = await gateway.get_orders()
    assert len(all_orders) == 2

    filled_orders = await gateway.get_orders(status="filled")
    assert len(filled_orders) == 2

    canceled_orders = await gateway.get_orders(status="canceled")
    assert len(canceled_orders) == 0


# ---------------------------------------------------------------------------
# 7. Position & Account Aggregation Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_open_positions_single_and_aggregate():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")

    b1.inject_position(symbol="AAPL", qty=10.0, avg_price=150.0)
    b2.inject_position(symbol="AAPL", qty=20.0, avg_price=165.0)
    b2.inject_position(symbol="GOOG", qty=5.0, avg_price=140.0)

    gateway.register_broker(b1)
    gateway.register_broker(b2)

    # Query active broker b1 positions
    pos_b1 = await gateway.get_open_positions()
    assert len(pos_b1) == 1
    assert pos_b1[0].symbol == "AAPL"
    assert pos_b1[0].qty == 10.0

    # Query aggregated positions across all brokers
    agg_pos = await gateway.get_open_positions(aggregate=True)
    pos_dict = {p.symbol: p for p in agg_pos}

    assert "AAPL" in pos_dict
    assert pos_dict["AAPL"].qty == 30.0
    # Weighted average: (10*150 + 20*165) / 30 = (1500 + 3300) / 30 = 160.0
    assert pos_dict["AAPL"].avg_entry_price == pytest.approx(160.0)

    assert "GOOG" in pos_dict
    assert pos_dict["GOOG"].qty == 5.0


@pytest.mark.anyio
async def test_get_account_single_and_aggregate():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")

    b1.set_account_balances(equity=50_000.0, cash=30_000.0, buying_power=60_000.0)
    b2.set_account_balances(equity=70_000.0, cash=40_000.0, buying_power=80_000.0)

    gateway.register_broker(b1)
    gateway.register_broker(b2)

    # Active broker account (b1)
    acc_b1 = await gateway.get_account()
    assert acc_b1.equity == 50_000.0
    assert acc_b1.cash == 30_000.0

    # Specific broker account (b2)
    acc_b2 = await gateway.get_account(broker_id="b2")
    assert acc_b2.equity == 70_000.0
    assert acc_b2.cash == 40_000.0

    # Aggregated account
    acc_agg = await gateway.get_account(aggregate=True)
    assert acc_agg.equity == 120_000.0
    assert acc_agg.cash == 70_000.0
    assert acc_agg.buying_power == 140_000.0


# ---------------------------------------------------------------------------
# 8. Trade Update Stream Multiplexing Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_stream_trade_updates_multiplexing():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    b2 = MockBrokerAdapter("b2")

    gateway.register_broker(b1)
    gateway.register_broker(b2)

    collected_events: list[TradeUpdateEvent] = []

    async def _consumer():
        async for evt in gateway.stream_trade_updates():
            collected_events.append(evt)
            if len(collected_events) >= 2:
                break

    consumer_task = asyncio.create_task(_consumer())
    await asyncio.sleep(0.01)

    # Submit orders to both brokers
    intent1 = OrderIntent(strategy_id="s1", symbol="AAPL", side=OrderSide.BUY, qty=1.0)
    intent2 = OrderIntent(strategy_id="s2", symbol="MSFT", side=OrderSide.SELL, qty=2.0)

    await b1.submit_order(intent1)
    await b2.submit_order(intent2)

    await asyncio.wait_for(consumer_task, timeout=1.0)

    assert len(collected_events) == 2
    symbols = {e.symbol for e in collected_events}
    assert "AAPL" in symbols
    assert "MSFT" in symbols


# ---------------------------------------------------------------------------
# 9. Concrete Adapter Initialization & Features Tests
# ---------------------------------------------------------------------------

def test_concrete_adapter_instantiations():
    alpaca = AlpacaBrokerAdapter()
    assert alpaca.broker_id == "alpaca"
    assert alpaca.broker_type == "alpaca"

    ibkr = InteractiveBrokersAdapter()
    assert ibkr.broker_id == "interactive_brokers"
    assert ibkr.broker_type == "interactive_brokers"

    tradier = TradierBrokerAdapter()
    assert tradier.broker_id == "tradier"
    assert tradier.broker_type == "tradier"

    rh = RobinhoodBrokerAdapter()
    assert rh.broker_id == "robinhood"
    assert rh.broker_type == "robinhood"

    fmp = FMPPaperBrokerAdapter()
    assert fmp.broker_id == "fmp_paper"
    assert fmp.broker_type == "fmp_paper"


@pytest.mark.anyio
async def test_gateway_health_check_snapshot():
    gateway = MultiBrokerGateway.create_default(simulated=True)
    health = await gateway.check_health()
    assert isinstance(health, dict)
    assert len(health) == 5

    alpaca_health = await gateway.check_health("alpaca")
    assert isinstance(alpaca_health, BrokerHealthStatus)
    assert alpaca_health.broker_id == "alpaca"
    assert alpaca_health.is_healthy is True


@pytest.mark.anyio
async def test_custom_submit_hook():
    gateway = MultiBrokerGateway()
    b1 = MockBrokerAdapter("b1")
    gateway.register_broker(b1)

    # Intercept with custom hook
    def _my_hook(intent: OrderIntent) -> OrderResult:
        return OrderResult(
            client_order_id=intent.client_order_id or "c1",
            broker_order_id="custom-hook-id",
            status=OrderStatus.FILLED,
            filled_qty=intent.qty,
            filled_avg_price=999.0,
        )

    b1.set_submit_hook(_my_hook)

    intent = OrderIntent(strategy_id="s1", symbol="NVDA", side=OrderSide.BUY, qty=5.0)
    res = await gateway.submit_order(intent)
    assert res.broker_order_id == "custom-hook-id"
    assert res.filled_avg_price == 999.0


@pytest.mark.anyio
async def test_concrete_broker_failover_simulation_auto_and_manual():
    """Verify end-to-end failover across concrete adapters:
    Primary (Alpaca/Robinhood) -> Secondary (Tradier/InteractiveBrokers/FMPPaper) in AUTO & MANUAL modes.
    """
    gateway = MultiBrokerGateway(
        priority_hierarchy=["robinhood", "alpaca", "tradier", "interactive_brokers", "fmp_paper"],
        failover_mode=FailoverMode.AUTO,
    )

    rh = RobinhoodBrokerAdapter(simulated=True)
    alpaca = AlpacaBrokerAdapter(simulated=True)
    tradier = TradierBrokerAdapter(simulated=True)
    ibkr = InteractiveBrokersAdapter(simulated=True)
    fmp = FMPPaperBrokerAdapter(simulated=True)

    gateway.register_broker(rh)
    gateway.register_broker(alpaca)
    gateway.register_broker(tradier)
    gateway.register_broker(ibkr)
    gateway.register_broker(fmp)

    # 1. AUTO Mode: Robinhood and Alpaca fail -> should automatically route to Tradier
    rh.set_forced_error("Robinhood 503 API Unavailable")
    alpaca.set_forced_error("Alpaca Rate Limit Exceeded")

    intent1 = OrderIntent(
        strategy_id="swing_momentum",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=10.0,
        limit_price=175.0,
    )
    res1 = await gateway.submit_order(intent1)
    assert res1.status == OrderStatus.FILLED
    assert "tradier-" in str(res1.broker_order_id)

    audit1 = gateway.get_routing_audits()[-1]
    assert audit1.primary_broker_id == "robinhood"
    assert audit1.executed_broker_id == "tradier"
    assert audit1.was_failover is True
    assert len(audit1.attempts) == 3

    # 2. AUTO Mode: Tradier and IBKR also fail -> cascade down to FMPPaper
    tradier.set_forced_error("Tradier Maintenance Window")
    ibkr.set_forced_error("IBKR TWS Gateway Disconnected")

    intent2 = OrderIntent(
        strategy_id="mean_reversion",
        symbol="MSFT",
        side=OrderSide.SELL,
        qty=5.0,
        limit_price=410.0,
    )
    res2 = await gateway.submit_order(intent2)
    assert res2.status == OrderStatus.FILLED
    assert "fmp_paper-" in str(res2.broker_order_id)

    audit2 = gateway.get_routing_audits()[-1]
    assert audit2.executed_broker_id == "fmp_paper"
    assert len(audit2.attempts) == 5

    # 3. MANUAL Mode: Set manual override to InteractiveBrokers after resetting its error
    ibkr.set_forced_error(None)
    gateway.set_failover_mode(FailoverMode.MANUAL)
    gateway.set_manual_override("interactive_brokers")

    intent3 = OrderIntent(
        strategy_id="hedging",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=20.0,
        limit_price=510.0,
    )
    res3 = await gateway.submit_order(intent3)
    assert res3.status == OrderStatus.FILLED
    assert "interactive_brokers-" in str(res3.broker_order_id)

    # 4. MANUAL Mode failure: If manual override broker errors, no auto-failover to other brokers
    ibkr.set_forced_error("IBKR Socket Error")
    intent4 = OrderIntent(
        strategy_id="hedging",
        symbol="QQQ",
        side=OrderSide.BUY,
        qty=15.0,
    )
    res4 = await gateway.submit_order(intent4)
    assert res4.status == OrderStatus.ERROR
    assert "IBKR Socket Error" in str(res4.error_message)


@pytest.mark.anyio
async def test_circuit_breaker_full_transition_cycle():
    """Verify complete circuit breaker lifecycle:
    CLOSED -> OPEN (consecutive failures >= 3 / error rate > 50%) -> HALF_OPEN (cooldown) -> CLOSED (recovery probes).
    """
    cfg = CircuitBreakerConfig(
        max_consecutive_failures=3,
        latency_threshold_ms=500.0,
        error_rate_threshold=0.50,
        min_requests_for_error_rate=4,
        half_open_probe_successes=2,
        cooldown_seconds=0.05,
    )
    cb = CircuitBreaker("cb_test", cfg)

    # Initial state
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1. High latency logging does not immediately trip CLOSED circuit
    cb.record_success(latency_ms=600.0)
    assert cb.state == CircuitState.CLOSED

    # 2. Trip on consecutive failures >= 3
    cb.record_failure("Failure 1", consecutive_failures=1)
    assert cb.state == CircuitState.CLOSED
    cb.record_failure("Failure 2", consecutive_failures=2)
    assert cb.state == CircuitState.CLOSED
    cb.record_failure("Failure 3", consecutive_failures=3)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # 3. Wait for cooldown to transition to HALF_OPEN
    await asyncio.sleep(0.06)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # 4. Recovery probes in HALF_OPEN
    cb.record_success(latency_ms=10.0)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.half_open_successes == 1

    cb.record_success(latency_ms=12.0)
    # Passed required 2 probes -> returns to CLOSED
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

