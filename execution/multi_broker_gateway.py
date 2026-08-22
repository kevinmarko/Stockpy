"""
execution/multi_broker_gateway.py
=================================
Unified Multi-Broker Gateway & Automated Circuit-Breaker Failover Engine.

Provides an enterprise-grade, high-availability broker abstraction supporting:
1. Multi-Broker Adapters:
   - Alpaca (Paper/Live)
   - Interactive Brokers (Simulated / TWS Gateway)
   - Tradier (Simulated / Equity & Options)
   - Robinhood (Simulated / Fractional & Crypto)
   - FMP Paper (Simulated / Paper Ledger)
   - Extensible Custom Broker Adapters (inheriting BrokerBase / BaseBrokerAdapter)

2. Broker Health & Latency Heartbeat Monitor:
   - Round-trip latency tracking (EMA, P95, rolling window)
   - Connection state management (CONNECTED, DEGRADED, FAILING, DISCONNECTED, MAINTENANCE)
   - Rolling error rate monitoring and health status snapshots

3. Automated Circuit Breaker & Failover Engine:
   - Priority hierarchy routing (configurable primary and fallback chain)
   - Configurable circuit breaker thresholds (consecutive failures >= 3, latency > 500ms, error rate > 50%)
   - Automated routing failover to fallback brokers upon primary degradation/failure
   - Manual operator override toggle, forced trip, and circuit reset controls
   - Complete multi-venue routing audit trail and execution telemetry

AST Safety: Strict (stdlib, numpy, pandas only, no heavy engine imports).
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import enum
import logging
import math
import random
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

# AST Safety: Optional numpy/pandas acceleration with pure-stdlib fallback
try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderPriority,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    TradeUpdateEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Constants
# ---------------------------------------------------------------------------

class BrokerType(str, enum.Enum):
    ALPACA = "alpaca"
    INTERACTIVE_BROKERS = "interactive_brokers"
    TRADIER = "tradier"
    ROBINHOOD = "robinhood"
    FMP_PAPER = "fmp_paper"
    CUSTOM = "custom"


class ConnectionState(str, enum.Enum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    FAILING = "failing"
    DISCONNECTED = "disconnected"
    MAINTENANCE = "maintenance"


class CircuitState(str, enum.Enum):
    CLOSED = "closed"        # Normal operational state, traffic allowed
    OPEN = "open"            # Circuit tripped, traffic diverted to fallback
    HALF_OPEN = "half_open"  # Recovery probe mode, testing canary traffic


class FailoverMode(str, enum.Enum):
    AUTO = "auto"          # Automated circuit breaker failover enabled
    MANUAL = "manual"      # Strict manual operator routing only
    DISABLED = "disabled"  # Failover disabled, fail fast on primary


class FailoverTriggerReason(str, enum.Enum):
    CONSECUTIVE_FAILURES = "consecutive_failures"
    HIGH_LATENCY = "high_latency"
    HIGH_ERROR_RATE = "high_error_rate"
    CONNECTION_LOST = "connection_lost"
    MANUAL_OVERRIDE = "manual_override"
    MANUAL_TRIP = "manual_trip"
    TIMEOUT = "timeout"
    EXCEPTION = "exception"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MultiBrokerGatewayError(Exception):
    """Base exception for MultiBrokerGateway operations."""
    pass


class BrokerNotRegisteredError(MultiBrokerGatewayError):
    """Raised when an operation targets an unregistered broker."""
    pass


class NoHealthyBrokerError(MultiBrokerGatewayError):
    """Raised when all candidate brokers in hierarchy are unavailable."""
    pass


class CircuitBreakerOpenError(MultiBrokerGatewayError):
    """Raised when attempting execution on an OPEN circuit broker."""
    pass


class OrderRoutingFailedError(MultiBrokerGatewayError):
    """Raised when order submission fails across all attempted brokers."""
    pass


# ---------------------------------------------------------------------------
# Configurations & Data Models
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreakerConfig:
    """Configurable thresholds for automated broker circuit breaker & failover."""
    max_consecutive_failures: int = 3
    latency_threshold_ms: float = 500.0
    error_rate_threshold: float = 0.50
    min_requests_for_error_rate: int = 5
    half_open_probe_successes: int = 2
    cooldown_seconds: float = 30.0


@dataclass
class BrokerMetrics:
    """Real-time performance and reliability metrics for a broker."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    latency_history: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    error_history: deque[bool] = field(default_factory=lambda: deque(maxlen=100))
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None
    last_heartbeat_time: Optional[datetime] = None
    last_request_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None

    def record_success(self, latency_ms: float) -> None:
        """Record a successful execution or heartbeat."""
        now = datetime.now(timezone.utc)
        self.total_requests += 1
        self.successful_requests += 1
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.last_latency_ms = max(0.0, float(latency_ms))
        self.min_latency_ms = min(self.min_latency_ms, self.last_latency_ms)
        self.max_latency_ms = max(self.max_latency_ms, self.last_latency_ms)
        self.latency_history.append(self.last_latency_ms)
        self.error_history.append(False)
        self.avg_latency_ms = sum(self.latency_history) / len(self.latency_history)
        self.p95_latency_ms = self._calculate_percentile(95.0)
        self.last_request_time = now
        self.last_success_time = now

    def record_failure(self, error_msg: str, latency_ms: float = 0.0) -> None:
        """Record a failed execution or heartbeat."""
        now = datetime.now(timezone.utc)
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        if latency_ms > 0:
            self.last_latency_ms = float(latency_ms)
            self.latency_history.append(self.last_latency_ms)
            self.avg_latency_ms = sum(self.latency_history) / len(self.latency_history)
            self.p95_latency_ms = self._calculate_percentile(95.0)
        self.error_history.append(True)
        self.last_error = str(error_msg)
        self.last_error_time = now
        self.last_request_time = now

    def record_heartbeat(self, latency_ms: float, success: bool, error_msg: Optional[str] = None) -> None:
        """Record a heartbeat probe result."""
        self.last_heartbeat_time = datetime.now(timezone.utc)
        if success:
            self.record_success(latency_ms)
        else:
            self.record_failure(error_msg or "Heartbeat probe failed", latency_ms)

    @property
    def rolling_error_rate(self) -> float:
        """Compute rolling error rate over the bounded window."""
        if not self.error_history:
            return 0.0
        return sum(1 for e in self.error_history if e) / float(len(self.error_history))

    def _calculate_percentile(self, percentile: float) -> float:
        if not self.latency_history:
            return 0.0
        samples = list(self.latency_history)
        if len(samples) == 1:
            return samples[0]
        if np is not None:
            try:
                return float(np.percentile(samples, percentile))
            except Exception:
                pass
        sorted_samples = sorted(samples)
        k = (len(sorted_samples) - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_samples[int(k)])
        d0 = sorted_samples[int(f)] * (c - k)
        d1 = sorted_samples[int(c)] * (k - f)
        return float(d0 + d1)


@dataclass
class BrokerHealthStatus:
    """Comprehensive health and readiness snapshot for a broker adapter."""
    broker_id: str
    broker_type: str
    connection_state: ConnectionState
    circuit_state: CircuitState
    is_healthy: bool
    is_routable: bool
    latency_ms: float
    avg_latency_ms: float
    p95_latency_ms: float
    error_rate: float
    consecutive_failures: int
    last_heartbeat: Optional[datetime]
    last_error: Optional[str]
    status_message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_id": self.broker_id,
            "broker_type": self.broker_type,
            "connection_state": self.connection_state.value if isinstance(self.connection_state, enum.Enum) else str(self.connection_state),
            "circuit_state": self.circuit_state.value if isinstance(self.circuit_state, enum.Enum) else str(self.circuit_state),
            "is_healthy": self.is_healthy,
            "is_routable": self.is_routable,
            "latency_ms": round(self.latency_ms, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "consecutive_failures": self.consecutive_failures,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_error": self.last_error,
            "status_message": self.status_message,
        }


@dataclass
class RoutingAttempt:
    """Record of an individual order routing attempt to a specific broker."""
    broker_id: str
    timestamp: datetime
    latency_ms: float
    success: bool
    error: Optional[str] = None
    order_result: Optional[OrderResult] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_id": self.broker_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "latency_ms": round(self.latency_ms, 2),
            "success": self.success,
            "error": self.error,
            "order_result": self.order_result.to_dict() if hasattr(self.order_result, "to_dict") else None,
        }


@dataclass
class RoutingAuditTrail:
    """Full execution and failover trail for an order intent."""
    client_order_id: str
    symbol: str
    side: OrderSide
    qty: float
    primary_broker_id: str
    executed_broker_id: Optional[str]
    was_failover: bool
    total_latency_ms: float
    final_status: OrderStatus
    attempts: list[RoutingAttempt] = field(default_factory=list)
    failover_reason: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value if isinstance(self.side, enum.Enum) else str(self.side),
            "qty": self.qty,
            "primary_broker_id": self.primary_broker_id,
            "executed_broker_id": self.executed_broker_id,
            "was_failover": self.was_failover,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "final_status": self.final_status.value if isinstance(self.final_status, enum.Enum) else str(self.final_status),
            "attempts": [a.to_dict() for a in self.attempts],
            "failover_reason": self.failover_reason,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class GatewayStatusSnapshot:
    """Gateway-wide aggregate state and telemetry."""
    active_broker_id: Optional[str]
    manual_override_broker_id: Optional[str]
    priority_hierarchy: list[str]
    brokers: dict[str, BrokerHealthStatus]
    total_orders_routed: int
    total_failovers: int
    last_failover_time: Optional[datetime]
    last_failover_reason: Optional[str]
    recent_routing_audits: list[RoutingAuditTrail]

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_broker_id": self.active_broker_id,
            "manual_override_broker_id": self.manual_override_broker_id,
            "priority_hierarchy": list(self.priority_hierarchy),
            "brokers": {k: v.to_dict() for k, v in self.brokers.items()},
            "total_orders_routed": self.total_orders_routed,
            "total_failovers": self.total_failovers,
            "last_failover_time": self.last_failover_time.isoformat() if self.last_failover_time else None,
            "last_failover_reason": self.last_failover_reason,
            "recent_routing_audits": [a.to_dict() for a in self.recent_routing_audits],
        }


# ---------------------------------------------------------------------------
# Circuit Breaker Implementation
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Per-broker Circuit Breaker state machine.
    Transitions:
      CLOSED -> OPEN (consecutive failures >= threshold OR error rate >= threshold OR latency > threshold)
      OPEN -> HALF_OPEN (cooldown timer expires)
      HALF_OPEN -> CLOSED (consecutive canary probe successes >= threshold)
      HALF_OPEN -> OPEN (any failure during probe)
    """

    def __init__(self, broker_id: str, config: Optional[CircuitBreakerConfig] = None) -> None:
        self.broker_id = broker_id
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.tripped_at: Optional[datetime] = None
        self.trip_reason: Optional[str] = None
        self.half_open_successes: int = 0
        # Consecutive CLOSED-state successes whose latency exceeded
        # config.latency_threshold_ms. Tracked here (not on BrokerMetrics) so
        # the latency trip condition is fully self-contained in
        # record_success/record_failure -- every existing call site already
        # passes latency_ms and needs no change. Reset by any success within
        # threshold, any failure, or an explicit reset().
        self._consecutive_latency_breaches: int = 0

    def can_execute(self) -> bool:
        """Check if traffic is allowed to execute through this circuit breaker."""
        now = datetime.now(timezone.utc)
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self.tripped_at is not None:
                elapsed = (now - self.tripped_at).total_seconds()
                if elapsed >= self.config.cooldown_seconds:
                    logger.info(
                        "Circuit breaker for '%s' cooldown elapsed (%.1fs >= %.1fs); entering HALF_OPEN",
                        self.broker_id, elapsed, self.config.cooldown_seconds
                    )
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_successes = 0
                    return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False

    def record_success(self, latency_ms: float) -> None:
        """Record success and update state machine."""
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_successes += 1
            logger.info(
                "Circuit breaker '%s' probe success (%d/%d)",
                self.broker_id, self.half_open_successes, self.config.half_open_probe_successes
            )
            if self.half_open_successes >= self.config.half_open_probe_successes:
                self.reset(reason="half_open_probes_passed")
        elif self.state == CircuitState.CLOSED:
            if latency_ms > self.config.latency_threshold_ms:
                self._consecutive_latency_breaches += 1
                logger.warning(
                    "Broker '%s' latency %.1fms exceeds threshold %.1fms (%d consecutive)",
                    self.broker_id, latency_ms, self.config.latency_threshold_ms,
                    self._consecutive_latency_breaches,
                )
                # Latency is one of the three documented OR conditions for
                # tripping (see class docstring) -- a broker that is
                # consistently slow but never errors and never crosses the
                # error-rate threshold must still trip. Reuses
                # max_consecutive_failures as the count (the established "N
                # consecutive bad signals of any kind" threshold) rather than
                # a fresh config field with an arbitrary default.
                if self._consecutive_latency_breaches >= self.config.max_consecutive_failures:
                    self.trip(
                        reason=(
                            f"Latency {latency_ms:.1f}ms exceeded threshold "
                            f"{self.config.latency_threshold_ms:.1f}ms for "
                            f"{self._consecutive_latency_breaches} consecutive requests"
                        )
                    )
            else:
                self._consecutive_latency_breaches = 0

    def record_failure(
        self,
        error_msg: str,
        latency_ms: float = 0.0,
        consecutive_failures: int = 1,
        rolling_error_rate: float = 0.0,
        total_requests: int = 0,
    ) -> None:
        """Record failure and check trip conditions."""
        # A failure breaks the "consecutive slow-but-healthy" streak; errors
        # already trip via their own consecutive-failure/error-rate path
        # below, so a mixed slow/erroring broker doesn't double-count.
        self._consecutive_latency_breaches = 0

        if self.state == CircuitState.HALF_OPEN:
            self.trip(
                reason=f"Probe failure in HALF_OPEN state: {error_msg}"
            )
            return

        if self.state == CircuitState.CLOSED:
            if consecutive_failures >= self.config.max_consecutive_failures:
                self.trip(
                    reason=(
                        f"Consecutive failures threshold reached "
                        f"({consecutive_failures}/{self.config.max_consecutive_failures}): {error_msg}"
                    )
                )
                return

            if (
                total_requests >= self.config.min_requests_for_error_rate
                and rolling_error_rate >= self.config.error_rate_threshold
            ):
                self.trip(
                    reason=(
                        f"Error rate {rolling_error_rate:.1%} exceeded threshold "
                        f"{self.config.error_rate_threshold:.1%}"
                    )
                )
                return

    def trip(self, reason: str) -> None:
        """Trip circuit breaker to OPEN state."""
        self.state = CircuitState.OPEN
        self.tripped_at = datetime.now(timezone.utc)
        self.trip_reason = reason
        self.half_open_successes = 0
        logger.error("Circuit breaker TRIPPED for '%s': %s", self.broker_id, reason)

    def reset(self, reason: str = "manual_operator_reset") -> None:
        """Reset circuit breaker to CLOSED state."""
        self.state = CircuitState.CLOSED
        self.tripped_at = None
        self.trip_reason = None
        self.half_open_successes = 0
        self._consecutive_latency_breaches = 0
        logger.info("Circuit breaker RESET for '%s' (reason: %s)", self.broker_id, reason)


# ---------------------------------------------------------------------------
# Base Broker Adapter
# ---------------------------------------------------------------------------

class BaseBrokerAdapter(BrokerBase):
    """
    Base class for all broker adapters in the MultiBrokerGateway.
    Provides standard in-memory simulation, fault injection, heartbeat, and metrics.
    """

    def __init__(
        self,
        broker_id: str,
        broker_type: Union[BrokerType, str] = BrokerType.CUSTOM,
        name: Optional[str] = None,
        simulated: bool = True,
        config: Optional[dict] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        self.broker_id = broker_id
        self.broker_type = broker_type.value if isinstance(broker_type, enum.Enum) else str(broker_type)
        self.name = name or f"{self.broker_type.capitalize()} Broker ({self.broker_id})"
        self.simulated = simulated
        self.config = config or {}

        self.connection_state = ConnectionState.CONNECTED
        self.metrics = BrokerMetrics()
        self.circuit_breaker = CircuitBreaker(self.broker_id, circuit_breaker_config)

        # In-memory simulation state
        self._positions: dict[str, PositionSnapshot] = {}
        self._account = AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)
        self._orders: dict[str, OrderResult] = {}
        self._trade_update_queue: asyncio.Queue[TradeUpdateEvent] = asyncio.Queue()

        # Simulation & Fault Injection Controls
        self._simulated_latency_ms: float = 5.0
        self._failure_rate: float = 0.0
        self._forced_error: Optional[str] = None
        self._connected: bool = True
        self._submit_hook: Optional[Callable[[OrderIntent], Optional[OrderResult]]] = None

    # --- Fault Injection & Simulation Controls ---

    def set_simulated_latency(self, latency_ms: float) -> None:
        """Set simulated network round-trip latency in milliseconds."""
        self._simulated_latency_ms = max(0.0, float(latency_ms))

    def set_failure_rate(self, failure_rate: float) -> None:
        """Set stochastic failure rate (0.0 to 1.0) for order submissions and pings."""
        self._failure_rate = max(0.0, min(1.0, float(failure_rate)))

    def set_forced_error(self, error_message: Optional[str]) -> None:
        """Force all future calls on this adapter to fail with error_message."""
        self._forced_error = error_message

    def set_connected(self, connected: bool) -> None:
        """Toggle connection state."""
        self._connected = connected
        self.connection_state = ConnectionState.CONNECTED if connected else ConnectionState.DISCONNECTED

    def set_account_balances(self, equity: float, cash: float, buying_power: float, currency: str = "USD") -> None:
        """Set simulated account balances."""
        self._account = AccountSnapshot(
            equity=float(equity), cash=float(cash), buying_power=float(buying_power), currency=currency
        )

    def inject_position(
        self, symbol: str, qty: float, avg_price: float, market_value: Optional[float] = None, unrealized_pl: float = 0.0
    ) -> None:
        """Inject or update a position in simulated storage."""
        mv = market_value if market_value is not None else qty * avg_price
        self._positions[symbol.upper()] = PositionSnapshot(
            symbol=symbol.upper(),
            qty=float(qty),
            avg_entry_price=float(avg_price),
            market_value=float(mv),
            unrealized_pl=float(unrealized_pl),
        )

    def set_submit_hook(self, hook: Optional[Callable[[OrderIntent], Optional[OrderResult]]]) -> None:
        """Custom hook to intercept or override submit_order."""
        self._submit_hook = hook

    # --- Heartbeat & Health Check ---

    async def ping(self) -> float:
        """
        Execute heartbeat ping and return round-trip latency in milliseconds.
        Raises ConnectionError or RuntimeError on failure.
        """
        if not self._connected or self.connection_state == ConnectionState.DISCONNECTED:
            raise ConnectionError(f"Broker '{self.broker_id}' is disconnected")

        if self._forced_error is not None:
            raise RuntimeError(f"Broker '{self.broker_id}' forced error: {self._forced_error}")

        if self._failure_rate > 0.0 and random.random() < self._failure_rate:
            raise RuntimeError(f"Broker '{self.broker_id}' simulated transient ping failure")

        if self._simulated_latency_ms > 0:
            await asyncio.sleep(self._simulated_latency_ms / 1000.0)

        return float(self._simulated_latency_ms)

    def get_health_status(self) -> BrokerHealthStatus:
        """Get instant health snapshot for this broker."""
        can_exec = self.circuit_breaker.can_execute()
        is_connected = self._connected and self.connection_state != ConnectionState.DISCONNECTED
        is_healthy = is_connected and can_exec and self.metrics.consecutive_failures == 0
        is_routable = is_connected and can_exec

        msg = "Healthy"
        if not is_connected:
            msg = "Disconnected"
        elif self.circuit_breaker.state == CircuitState.OPEN:
            msg = f"Circuit OPEN: {self.circuit_breaker.trip_reason}"
        elif self.circuit_breaker.state == CircuitState.HALF_OPEN:
            msg = f"Circuit HALF_OPEN (probe {self.circuit_breaker.half_open_successes}/{self.circuit_breaker.config.half_open_probe_successes})"
        elif self.metrics.last_latency_ms > self.circuit_breaker.config.latency_threshold_ms:
            msg = f"Degraded Latency ({self.metrics.last_latency_ms:.1f}ms > {self.circuit_breaker.config.latency_threshold_ms:.1f}ms)"
        elif self.metrics.consecutive_failures > 0:
            msg = f"Warning: {self.metrics.consecutive_failures} consecutive failure(s)"

        return BrokerHealthStatus(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            connection_state=self.connection_state,
            circuit_state=self.circuit_breaker.state,
            is_healthy=is_healthy,
            is_routable=is_routable,
            latency_ms=self.metrics.last_latency_ms,
            avg_latency_ms=self.metrics.avg_latency_ms,
            p95_latency_ms=self.metrics.p95_latency_ms,
            error_rate=self.metrics.rolling_error_rate,
            consecutive_failures=self.metrics.consecutive_failures,
            last_heartbeat=self.metrics.last_heartbeat_time,
            last_error=self.metrics.last_error,
            status_message=msg,
        )

    # --- BrokerBase Implementation ---

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Submit order to broker with in-memory execution and state updates."""
        client_order_id = intent.client_order_id or f"coid-{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()

        # Check connection & forced error
        if not self._connected or self.connection_state == ConnectionState.DISCONNECTED:
            lat_ms = (time.perf_counter() - t0) * 1000.0
            err_msg = f"Broker '{self.broker_id}' is disconnected"
            self.metrics.record_failure(err_msg, lat_ms)
            self.circuit_breaker.record_failure(
                err_msg, lat_ms, self.metrics.consecutive_failures, self.metrics.rolling_error_rate, self.metrics.total_requests
            )
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message=err_msg,
            )

        if self._forced_error is not None:
            lat_ms = (time.perf_counter() - t0) * 1000.0
            err_msg = f"Broker '{self.broker_id}' forced error: {self._forced_error}"
            self.metrics.record_failure(err_msg, lat_ms)
            self.circuit_breaker.record_failure(
                err_msg, lat_ms, self.metrics.consecutive_failures, self.metrics.rolling_error_rate, self.metrics.total_requests
            )
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message=err_msg,
            )

        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            lat_ms = (time.perf_counter() - t0) * 1000.0
            err_msg = f"Circuit breaker OPEN for broker '{self.broker_id}': {self.circuit_breaker.trip_reason}"
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message=err_msg,
            )

        # Apply simulated latency
        if self._simulated_latency_ms > 0:
            await asyncio.sleep(self._simulated_latency_ms / 1000.0)

        # Custom hook interception
        if self._submit_hook is not None:
            hook_res = self._submit_hook(intent)
            if hook_res is not None:
                lat_ms = (time.perf_counter() - t0) * 1000.0
                if hook_res.status == OrderStatus.ERROR:
                    self.metrics.record_failure(hook_res.error_message or "Hook error", lat_ms)
                    self.circuit_breaker.record_failure(
                        hook_res.error_message or "Hook error", lat_ms, self.metrics.consecutive_failures, self.metrics.rolling_error_rate, self.metrics.total_requests
                    )
                else:
                    self.metrics.record_success(lat_ms)
                    self.circuit_breaker.record_success(lat_ms)
                return hook_res

        # Stochastic failure
        if self._failure_rate > 0.0 and random.random() < self._failure_rate:
            lat_ms = (time.perf_counter() - t0) * 1000.0
            err_msg = f"Broker '{self.broker_id}' transient network/gateway failure"
            self.metrics.record_failure(err_msg, lat_ms)
            self.circuit_breaker.record_failure(
                err_msg, lat_ms, self.metrics.consecutive_failures, self.metrics.rolling_error_rate, self.metrics.total_requests
            )
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message=err_msg,
            )

        # Dry run path
        if intent.dry_run:
            lat_ms = (time.perf_counter() - t0) * 1000.0
            self.metrics.record_success(lat_ms)
            self.circuit_breaker.record_success(lat_ms)
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ACCEPTED,
                submitted_at=datetime.now(timezone.utc),
            )

        # Fill calculation
        fill_price = float(intent.limit_price if intent.limit_price is not None else 100.0)
        broker_order_id = f"{self.broker_id}-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        # Update in-memory positions
        sym = intent.symbol.upper()
        current_pos = self._positions.get(sym)
        fill_qty = float(intent.qty)
        signed_qty = fill_qty if intent.side == OrderSide.BUY else -fill_qty

        if current_pos is None:
            new_qty = signed_qty
            new_avg = fill_price
        else:
            new_qty = current_pos.qty + signed_qty
            if abs(new_qty) > 1e-6:
                if (current_pos.qty > 0 and signed_qty > 0) or (current_pos.qty < 0 and signed_qty < 0):
                    total_cost = (current_pos.qty * current_pos.avg_entry_price) + (signed_qty * fill_price)
                    new_avg = total_cost / new_qty
                else:
                    new_avg = current_pos.avg_entry_price
            else:
                new_qty = 0.0
                new_avg = 0.0

        mv = new_qty * fill_price
        unrealized = (fill_price - new_avg) * new_qty if abs(new_qty) > 1e-6 else 0.0

        if abs(new_qty) > 1e-6:
            self._positions[sym] = PositionSnapshot(
                symbol=sym,
                qty=new_qty,
                avg_entry_price=new_avg,
                market_value=mv,
                unrealized_pl=unrealized,
            )
        elif sym in self._positions:
            del self._positions[sym]

        # Update account cash & equity
        cash_delta = -signed_qty * fill_price
        self._account.cash += cash_delta
        self._account.buying_power = max(0.0, self._account.cash * 2.0)
        total_mv = sum(p.market_value for p in self._positions.values())
        self._account.equity = self._account.cash + total_mv

        order_res = OrderResult(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status=OrderStatus.FILLED,
            filled_qty=fill_qty,
            filled_avg_price=fill_price,
            submitted_at=now,
            filled_at=now,
        )
        self._orders[broker_order_id] = order_res

        # Push to stream
        evt = TradeUpdateEvent(
            event_type="fill",
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            filled_qty=fill_qty,
            filled_avg_price=fill_price,
            timestamp=now,
        )
        self._trade_update_queue.put_nowait(evt)

        # Record metrics & circuit breaker success
        lat_ms = (time.perf_counter() - t0) * 1000.0
        self.metrics.record_success(lat_ms)
        self.circuit_breaker.record_success(lat_ms)
        return order_res

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an order by broker-assigned ID."""
        if not self._connected or self._forced_error is not None:
            return False
        if broker_order_id in self._orders:
            ord_obj = self._orders[broker_order_id]
            if ord_obj.status in (OrderStatus.ACCEPTED, OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED):
                ord_obj.status = OrderStatus.CANCELED
                return True
        return False

    async def get_open_positions(self) -> list[PositionSnapshot]:
        """Return all open positions."""
        if not self._connected or self._forced_error is not None:
            raise ConnectionError(f"Broker '{self.broker_id}' is unavailable")
        return list(self._positions.values())

    async def get_account(self) -> AccountSnapshot:
        """Return account equity / cash / buying power."""
        if not self._connected or self._forced_error is not None:
            raise ConnectionError(f"Broker '{self.broker_id}' is unavailable")
        return self._account

    async def get_orders(self, status: Optional[str] = None, limit: int = 100) -> list[OrderResult]:
        """Return recent orders."""
        if not self._connected or self._forced_error is not None:
            raise ConnectionError(f"Broker '{self.broker_id}' is unavailable")
        ords = list(self._orders.values())
        if status is not None:
            ords = [o for o in ords if o.status.value == status or o.status == status]
        ords.sort(key=lambda x: x.submitted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return ords[:limit]

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        """Async generator yielding trade update events."""
        while True:
            evt = await self._trade_update_queue.get()
            yield evt


# ---------------------------------------------------------------------------
# Concrete Broker Adapters
# ---------------------------------------------------------------------------

class AlpacaBrokerAdapter(BaseBrokerAdapter):
    """Alpaca paper/live broker adapter with options and equity execution."""
    def __init__(
        self,
        broker_id: str = "alpaca",
        name: str = "Alpaca Trading",
        simulated: bool = True,
        config: Optional[dict] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        super().__init__(
            broker_id=broker_id,
            broker_type=BrokerType.ALPACA,
            name=name,
            simulated=simulated,
            config=config,
            circuit_breaker_config=circuit_breaker_config,
        )


class InteractiveBrokersAdapter(BaseBrokerAdapter):
    """Interactive Brokers (IBKR) simulated TWS/Gateway execution adapter."""
    def __init__(
        self,
        broker_id: str = "interactive_brokers",
        name: str = "Interactive Brokers (TWS Gateway)",
        simulated: bool = True,
        config: Optional[dict] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        super().__init__(
            broker_id=broker_id,
            broker_type=BrokerType.INTERACTIVE_BROKERS,
            name=name,
            simulated=simulated,
            config=config,
            circuit_breaker_config=circuit_breaker_config,
        )
        self._simulated_latency_ms = 8.0  # realistic IBKR TWS bridge latency


class TradierBrokerAdapter(BaseBrokerAdapter):
    """Tradier simulated broker adapter with equity and multi-leg options capabilities."""
    def __init__(
        self,
        broker_id: str = "tradier",
        name: str = "Tradier Brokerage",
        simulated: bool = True,
        config: Optional[dict] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        super().__init__(
            broker_id=broker_id,
            broker_type=BrokerType.TRADIER,
            name=name,
            simulated=simulated,
            config=config,
            circuit_breaker_config=circuit_breaker_config,
        )
        self._simulated_latency_ms = 12.0


class RobinhoodBrokerAdapter(BaseBrokerAdapter):
    """Robinhood simulated broker adapter supporting fractional orders and fast executions."""
    def __init__(
        self,
        broker_id: str = "robinhood",
        name: str = "Robinhood Financial",
        simulated: bool = True,
        config: Optional[dict] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        super().__init__(
            broker_id=broker_id,
            broker_type=BrokerType.ROBINHOOD,
            name=name,
            simulated=simulated,
            config=config,
            circuit_breaker_config=circuit_breaker_config,
        )
        self._simulated_latency_ms = 15.0


class FMPPaperBrokerAdapter(BaseBrokerAdapter):
    """FMP Paper simulated broker adapter backed by local paper ledger."""
    def __init__(
        self,
        broker_id: str = "fmp_paper",
        name: str = "FMP Paper Ledger",
        simulated: bool = True,
        config: Optional[dict] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        super().__init__(
            broker_id=broker_id,
            broker_type=BrokerType.FMP_PAPER,
            name=name,
            simulated=simulated,
            config=config,
            circuit_breaker_config=circuit_breaker_config,
        )
        self._simulated_latency_ms = 2.0


# ---------------------------------------------------------------------------
# Broker Health & Heartbeat Monitor
# ---------------------------------------------------------------------------

class BrokerHeartbeatMonitor:
    """
    Heartbeat and health monitor for registered multi-broker adapters.
    Measures round-trip latency, connection state, error rate, and checks circuit thresholds.
    """

    def __init__(self, gateway: "MultiBrokerGateway", probe_timeout_seconds: float = 2.0) -> None:
        self.gateway = gateway
        self.probe_timeout_seconds = probe_timeout_seconds
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

    async def ping_broker(self, broker_id: str) -> float:
        """
        Execute heartbeat ping against a specific registered broker adapter.
        Updates metrics and evaluates circuit breaker state.
        """
        adapter = self.gateway.get_broker(broker_id)
        if adapter is None:
            raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered in gateway")

        t0 = time.perf_counter()
        try:
            latency_ms = await asyncio.wait_for(
                adapter.ping(), timeout=self.probe_timeout_seconds
            )
            lat_ms = float(latency_ms) if latency_ms > 0 else (time.perf_counter() - t0) * 1000.0
            adapter.metrics.record_heartbeat(lat_ms, success=True)
            adapter.circuit_breaker.record_success(lat_ms)

            if lat_ms > adapter.circuit_breaker.config.latency_threshold_ms:
                adapter.connection_state = ConnectionState.DEGRADED
            else:
                adapter.connection_state = ConnectionState.CONNECTED

            return lat_ms
        except Exception as exc:
            lat_ms = (time.perf_counter() - t0) * 1000.0
            err_msg = str(exc)
            adapter.metrics.record_heartbeat(lat_ms, success=False, error_msg=err_msg)
            adapter.circuit_breaker.record_failure(
                err_msg,
                lat_ms,
                adapter.metrics.consecutive_failures,
                adapter.metrics.rolling_error_rate,
                adapter.metrics.total_requests,
            )
            adapter.connection_state = ConnectionState.FAILING
            logger.warning("Heartbeat failed for broker '%s': %s (%.1fms)", broker_id, err_msg, lat_ms)
            return lat_ms

    async def probe_all_brokers(self) -> dict[str, BrokerHealthStatus]:
        """Run a concurrent heartbeat probe cycle across all registered brokers."""
        tasks = {b_id: self.ping_broker(b_id) for b_id in self.gateway.list_brokers()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        health_map: dict[str, BrokerHealthStatus] = {}
        for b_id in self.gateway.list_brokers():
            adapter = self.gateway.get_broker(b_id)
            if adapter is not None:
                health_map[b_id] = adapter.get_health_status()
        return health_map

    async def start(self, interval_seconds: float = 5.0) -> None:
        """Start background periodic heartbeat loop."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._run_loop(interval_seconds))

    async def stop(self) -> None:
        """Stop background periodic heartbeat loop."""
        self._running = False
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def _run_loop(self, interval_seconds: float) -> None:
        while self._running:
            try:
                await self.probe_all_brokers()
            except Exception as e:
                logger.error("Error in broker heartbeat loop: %s", e)
            await asyncio.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# MultiBrokerGateway Core Engine
# ---------------------------------------------------------------------------

class MultiBrokerGateway(BrokerBase):
    """
    Unified Multi-Broker Gateway with Automated Circuit Breaker & Failover Engine.
    Inherits from BrokerBase for drop-in compatibility with OrderManager and strategies.
    """

    def __init__(
        self,
        priority_hierarchy: Optional[Sequence[str]] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        failover_mode: FailoverMode = FailoverMode.AUTO,
    ) -> None:
        self._brokers: dict[str, BaseBrokerAdapter] = {}
        self._priority_hierarchy: list[str] = list(priority_hierarchy) if priority_hierarchy else []
        self._circuit_breaker_config = circuit_breaker_config or CircuitBreakerConfig()
        self._failover_mode = failover_mode

        # Operator Overrides & Telemetry
        self._manual_override_broker_id: Optional[str] = None
        self._routing_audits: deque[RoutingAuditTrail] = deque(maxlen=500)
        self._total_orders_routed: int = 0
        self._total_failovers: int = 0
        self._last_failover_time: Optional[datetime] = None
        self._last_failover_reason: Optional[str] = None

        # Heartbeat Monitor
        self.heartbeat_monitor = BrokerHeartbeatMonitor(self)

    _default_gateway: Optional["MultiBrokerGateway"] = None

    # ------------------------------------------------------------------
    # Factory Helpers & Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_default_gateway(cls) -> "MultiBrokerGateway":
        """Retrieve or initialize the default singleton MultiBrokerGateway instance."""
        if cls._default_gateway is None:
            cls._default_gateway = cls.create_default(simulated=True)
        return cls._default_gateway

    @classmethod
    def set_default_gateway(cls, gateway: Optional["MultiBrokerGateway"]) -> None:
        """Set or reset the default MultiBrokerGateway instance (useful in tests)."""
        cls._default_gateway = gateway

    @classmethod
    def create_default(
        cls,
        simulated: bool = True,
        primary: str = "alpaca",
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> "MultiBrokerGateway":
        """
        Create and initialize a standard MultiBrokerGateway with all supported adapters:
        Alpaca, Interactive Brokers, Tradier, Robinhood, and FMP Paper.
        """
        default_hierarchy = [
            "alpaca",
            "interactive_brokers",
            "tradier",
            "robinhood",
            "fmp_paper",
        ]
        if primary in default_hierarchy and default_hierarchy[0] != primary:
            default_hierarchy.remove(primary)
            default_hierarchy.insert(0, primary)

        gateway = cls(
            priority_hierarchy=default_hierarchy,
            circuit_breaker_config=circuit_breaker_config,
            failover_mode=FailoverMode.AUTO,
        )

        gateway.register_broker(AlpacaBrokerAdapter(simulated=simulated, circuit_breaker_config=circuit_breaker_config))
        gateway.register_broker(InteractiveBrokersAdapter(simulated=simulated, circuit_breaker_config=circuit_breaker_config))
        gateway.register_broker(TradierBrokerAdapter(simulated=simulated, circuit_breaker_config=circuit_breaker_config))
        gateway.register_broker(RobinhoodBrokerAdapter(simulated=simulated, circuit_breaker_config=circuit_breaker_config))
        gateway.register_broker(FMPPaperBrokerAdapter(simulated=simulated, circuit_breaker_config=circuit_breaker_config))

        return gateway

    # ------------------------------------------------------------------
    # Broker Registration & Management
    # ------------------------------------------------------------------

    def register_broker(self, adapter: BaseBrokerAdapter, priority_index: Optional[int] = None) -> None:
        """Register a broker adapter and optionally position it in the priority hierarchy."""
        broker_id = adapter.broker_id
        self._brokers[broker_id] = adapter

        if broker_id not in self._priority_hierarchy:
            if priority_index is not None:
                self._priority_hierarchy.insert(priority_index, broker_id)
            else:
                self._priority_hierarchy.append(broker_id)
        elif priority_index is not None:
            self._priority_hierarchy.remove(broker_id)
            self._priority_hierarchy.insert(priority_index, broker_id)

        logger.info("Registered broker '%s' (%s) into gateway", broker_id, adapter.name)

    def deregister_broker(self, broker_id: str) -> None:
        """Deregister a broker adapter from the gateway."""
        if broker_id in self._brokers:
            del self._brokers[broker_id]
        if broker_id in self._priority_hierarchy:
            self._priority_hierarchy.remove(broker_id)
        if self._manual_override_broker_id == broker_id:
            self._manual_override_broker_id = None
        logger.info("Deregistered broker '%s' from gateway", broker_id)

    def get_broker(self, broker_id: str) -> Optional[BaseBrokerAdapter]:
        """Retrieve a registered broker adapter by ID."""
        return self._brokers.get(broker_id)

    def list_brokers(self) -> list[str]:
        """List all registered broker IDs."""
        return list(self._brokers.keys())

    # ------------------------------------------------------------------
    # Failover & Priority Hierarchy Management
    # ------------------------------------------------------------------

    def set_priority_hierarchy(self, priority_list: Sequence[str]) -> None:
        """Update broker priority hierarchy for automated failover."""
        valid_brokers = [b for b in priority_list if b in self._brokers]
        missing = [b for b in priority_list if b not in self._brokers]
        if missing:
            logger.warning("Brokers not registered in gateway ignored in hierarchy: %s", missing)
        self._priority_hierarchy = valid_brokers
        logger.info("Updated priority hierarchy: %s", self._priority_hierarchy)

    def get_priority_hierarchy(self) -> list[str]:
        """Get the current priority hierarchy."""
        return list(self._priority_hierarchy)

    def set_manual_override(self, broker_id: Optional[str]) -> None:
        """
        Manually force all order execution through a specific broker.
        Pass None to clear manual override and resume automated hierarchy routing.
        """
        if broker_id is not None and broker_id not in self._brokers:
            raise BrokerNotRegisteredError(f"Cannot override to unregistered broker '{broker_id}'")
        self._manual_override_broker_id = broker_id
        logger.warning("Manual operator override set to: '%s'", broker_id)

    def clear_manual_override(self) -> None:
        """Clear manual operator override and return to automated hierarchy routing."""
        self._manual_override_broker_id = None
        logger.info("Manual operator override cleared. Resumed automated priority hierarchy.")

    def get_manual_override(self) -> Optional[str]:
        """Get currently active manual override broker ID (if any)."""
        return self._manual_override_broker_id

    def set_failover_mode(self, mode: FailoverMode) -> None:
        """Set failover mode (AUTO, MANUAL, or DISABLED)."""
        self._failover_mode = mode

    def get_failover_mode(self) -> FailoverMode:
        """Get current failover mode."""
        return self._failover_mode

    # ------------------------------------------------------------------
    # Circuit Breaker Operator Controls
    # ------------------------------------------------------------------

    def trip_circuit_breaker(self, broker_id: str, reason: str = "manual_operator_trip") -> None:
        """Manually trip a broker's circuit breaker to OPEN."""
        adapter = self.get_broker(broker_id)
        if adapter is None:
            raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")
        adapter.circuit_breaker.trip(reason=reason)

    def reset_circuit_breaker(self, broker_id: Optional[str] = None, reason: str = "manual_operator_reset") -> None:
        """
        Reset circuit breaker to CLOSED state.
        If broker_id is None, resets circuit breakers across all registered brokers.
        """
        if broker_id is not None:
            adapter = self.get_broker(broker_id)
            if adapter is None:
                raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")
            adapter.circuit_breaker.reset(reason=reason)
            adapter.metrics.consecutive_failures = 0
        else:
            for adapter in self._brokers.values():
                adapter.circuit_breaker.reset(reason=reason)
                adapter.metrics.consecutive_failures = 0

    def get_circuit_state(self, broker_id: str) -> CircuitState:
        """Get current circuit breaker state for a broker."""
        adapter = self.get_broker(broker_id)
        if adapter is None:
            raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")
        return adapter.circuit_breaker.state

    # ------------------------------------------------------------------
    # Active Broker Resolution
    # ------------------------------------------------------------------

    def resolve_active_broker(self) -> BaseBrokerAdapter:
        """
        Resolve the currently active broker according to manual override,
        circuit breaker states, connection states, and priority hierarchy.
        """
        # 1. Manual operator override (highest precedence)
        if self._manual_override_broker_id is not None:
            adapter = self.get_broker(self._manual_override_broker_id)
            if adapter is not None:
                return adapter

        # 2. Automated hierarchy evaluation
        candidates = self.get_candidate_routing_sequence()
        if candidates:
            return candidates[0]

        # 3. Fallback to any connected broker if hierarchy is exhausted
        for adapter in self._brokers.values():
            if adapter.connection_state != ConnectionState.DISCONNECTED and adapter._connected:
                return adapter

        raise NoHealthyBrokerError("No healthy brokers available in gateway")

    def get_candidate_routing_sequence(self) -> list[BaseBrokerAdapter]:
        """
        Return the ordered sequence of candidate brokers for order routing.
        Filters out OPEN circuits (unless in HALF_OPEN probe mode) and disconnected brokers.
        """
        candidates: list[BaseBrokerAdapter] = []

        # In strict MANUAL mode, only route to manual override if set and healthy
        if self._failover_mode == FailoverMode.MANUAL:
            if self._manual_override_broker_id is not None:
                adapter = self.get_broker(self._manual_override_broker_id)
                if adapter is not None and adapter._connected and adapter.circuit_breaker.can_execute():
                    return [adapter]
            return []

        # If manual override is engaged in AUTO / other mode
        if self._manual_override_broker_id is not None:
            adapter = self.get_broker(self._manual_override_broker_id)
            if adapter is not None:
                candidates.append(adapter)
            if self._failover_mode != FailoverMode.AUTO:
                return candidates

        # Evaluate priority hierarchy
        for b_id in self._priority_hierarchy:
            adapter = self.get_broker(b_id)
            if adapter is None:
                continue
            if not adapter._connected or adapter.connection_state == ConnectionState.DISCONNECTED:
                continue
            if not adapter.circuit_breaker.can_execute():
                continue
            if adapter not in candidates:
                candidates.append(adapter)

        return candidates

    def get_healthy_brokers(self) -> list[BaseBrokerAdapter]:
        """Return list of all currently healthy and routable brokers."""
        healthy: list[BaseBrokerAdapter] = []
        for adapter in self._brokers.values():
            if (
                adapter._connected
                and adapter.connection_state != ConnectionState.DISCONNECTED
                and adapter.circuit_breaker.can_execute()
            ):
                healthy.append(adapter)
        return healthy

    # ------------------------------------------------------------------
    # BrokerBase Implementation & Order Execution with Automated Failover
    # ------------------------------------------------------------------

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        """
        Submit order to the active broker with automated circuit breaker failover.
        If the primary broker fails or trips, automatically diverts execution to the next fallback broker.
        """
        client_order_id = intent.client_order_id or f"m-coid-{uuid.uuid4().hex[:12]}"
        intent.client_order_id = client_order_id

        candidates = self.get_candidate_routing_sequence()
        if not candidates:
            err_msg = "No healthy or routable brokers available in failover hierarchy"
            logger.error("Order %s failed: %s", client_order_id, err_msg)
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message=err_msg,
            )

        primary_broker = candidates[0]
        audit_trail = RoutingAuditTrail(
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            primary_broker_id=primary_broker.broker_id,
            executed_broker_id=None,
            was_failover=False,
            total_latency_ms=0.0,
            final_status=OrderStatus.ERROR,
        )

        total_t0 = time.perf_counter()
        final_result: Optional[OrderResult] = None

        for idx, candidate in enumerate(candidates):
            is_failover_attempt = (idx > 0)
            t_cand = time.perf_counter()

            if is_failover_attempt:
                logger.warning(
                    "[FAILOVER] Diverting order %s (attempt %d) from primary '%s' to fallback '%s'",
                    client_order_id, idx + 1, primary_broker.broker_id, candidate.broker_id
                )

            try:
                res = await candidate.submit_order(intent)
                cand_lat = (time.perf_counter() - t_cand) * 1000.0

                attempt = RoutingAttempt(
                    broker_id=candidate.broker_id,
                    timestamp=datetime.now(timezone.utc),
                    latency_ms=cand_lat,
                    success=(res.status != OrderStatus.ERROR),
                    error=res.error_message,
                    order_result=res,
                )
                audit_trail.attempts.append(attempt)

                if res.status != OrderStatus.ERROR:
                    # Successful execution
                    final_result = res
                    audit_trail.executed_broker_id = candidate.broker_id
                    audit_trail.final_status = res.status
                    if is_failover_attempt:
                        audit_trail.was_failover = True
                        audit_trail.failover_reason = f"Primary failed; executed on fallback '{candidate.broker_id}'"
                        self._total_failovers += 1
                        self._last_failover_time = datetime.now(timezone.utc)
                        self._last_failover_reason = audit_trail.failover_reason

                    self._total_orders_routed += 1
                    audit_trail.total_latency_ms = (time.perf_counter() - total_t0) * 1000.0
                    self._routing_audits.append(audit_trail)
                    return res
                else:
                    # Broker returned an error status
                    logger.warning(
                        "Order %s execution failed on broker '%s': %s",
                        client_order_id, candidate.broker_id, res.error_message
                    )
                    final_result = res

                    # If failover is disabled or manual, do not attempt subsequent fallbacks
                    if self._failover_mode in (FailoverMode.DISABLED, FailoverMode.MANUAL):
                        break

            except Exception as exc:
                cand_lat = (time.perf_counter() - t_cand) * 1000.0
                err_str = f"Exception during submission: {exc}"
                logger.exception("Exception on broker '%s' for order %s", candidate.broker_id, client_order_id)
                candidate.metrics.record_failure(err_str, cand_lat)
                candidate.circuit_breaker.record_failure(
                    err_str, cand_lat, candidate.metrics.consecutive_failures, candidate.metrics.rolling_error_rate, candidate.metrics.total_requests
                )

                attempt = RoutingAttempt(
                    broker_id=candidate.broker_id,
                    timestamp=datetime.now(timezone.utc),
                    latency_ms=cand_lat,
                    success=False,
                    error=err_str,
                )
                audit_trail.attempts.append(attempt)

                if self._failover_mode in (FailoverMode.DISABLED, FailoverMode.MANUAL):
                    final_result = OrderResult(
                        client_order_id=client_order_id,
                        broker_order_id=None,
                        status=OrderStatus.ERROR,
                        error_message=err_str,
                    )
                    break

        # All candidate attempts failed
        audit_trail.total_latency_ms = (time.perf_counter() - total_t0) * 1000.0
        audit_trail.final_status = OrderStatus.ERROR
        self._routing_audits.append(audit_trail)

        if final_result is not None:
            return final_result

        return OrderResult(
            client_order_id=client_order_id,
            broker_order_id=None,
            status=OrderStatus.ERROR,
            error_message="Order execution exhausted all candidate brokers without success",
        )

    async def cancel_order(self, broker_order_id: str, broker_id: Optional[str] = None) -> bool:
        """Cancel an order by broker order ID across target or all brokers."""
        if broker_id is not None:
            adapter = self.get_broker(broker_id)
            if adapter is not None:
                return await adapter.cancel_order(broker_order_id)
            return False

        # Attempt cancellation across all registered brokers
        for adapter in self._brokers.values():
            if await adapter.cancel_order(broker_order_id):
                return True
        return False

    async def get_open_positions(self, broker_id: Optional[str] = None, aggregate: bool = False) -> list[PositionSnapshot]:
        """
        Get open positions.
        If broker_id is supplied, queries that broker.
        If aggregate is True, merges positions across all registered brokers.
        Otherwise, returns positions from the active broker.
        """
        if broker_id is not None:
            adapter = self.get_broker(broker_id)
            if adapter is not None:
                return await adapter.get_open_positions()
            raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")

        if aggregate:
            pos_by_sym: dict[str, list[PositionSnapshot]] = {}
            for adapter in self._brokers.values():
                if adapter._connected and adapter.connection_state != ConnectionState.DISCONNECTED:
                    try:
                        b_pos = await adapter.get_open_positions()
                        for p in b_pos:
                            pos_by_sym.setdefault(p.symbol, []).append(p)
                    except Exception as e:
                        logger.warning("Failed to fetch positions from '%s': %s", adapter.broker_id, e)

            merged: list[PositionSnapshot] = []
            for sym, items in pos_by_sym.items():
                total_qty = sum(item.qty for item in items)
                if abs(total_qty) > 1e-6:
                    total_cost = sum(item.qty * item.avg_entry_price for item in items)
                    weighted_avg = total_cost / total_qty
                    total_mv = sum(item.market_value for item in items)
                    total_unrealized = sum(item.unrealized_pl for item in items)
                    merged.append(
                        PositionSnapshot(
                            symbol=sym,
                            qty=total_qty,
                            avg_entry_price=weighted_avg,
                            market_value=total_mv,
                            unrealized_pl=total_unrealized,
                        )
                    )
            return merged

        active_broker = self.resolve_active_broker()
        return await active_broker.get_open_positions()

    async def get_account(self, broker_id: Optional[str] = None, aggregate: bool = False) -> AccountSnapshot:
        """
        Get account snapshot.
        If broker_id is supplied, queries that broker.
        If aggregate is True, sums equity, cash, and buying power across all connected brokers.
        Otherwise, returns account from the active broker.
        """
        if broker_id is not None:
            adapter = self.get_broker(broker_id)
            if adapter is not None:
                return await adapter.get_account()
            raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")

        if aggregate:
            total_equity = 0.0
            total_cash = 0.0
            total_bp = 0.0
            currency = "USD"
            for adapter in self._brokers.values():
                if adapter._connected and adapter.connection_state != ConnectionState.DISCONNECTED:
                    try:
                        acc = await adapter.get_account()
                        total_equity += acc.equity
                        total_cash += acc.cash
                        total_bp += acc.buying_power
                        currency = acc.currency
                    except Exception as e:
                        logger.warning("Failed to fetch account from '%s': %s", adapter.broker_id, e)
            return AccountSnapshot(equity=total_equity, cash=total_cash, buying_power=total_bp, currency=currency)

        active_broker = self.resolve_active_broker()
        return await active_broker.get_account()

    async def get_orders(self, status: Optional[str] = None, limit: int = 100, broker_id: Optional[str] = None) -> list[OrderResult]:
        """
        Get recent orders.
        If broker_id is provided, queries that broker.
        Otherwise, consolidates recent orders across all registered brokers sorted by timestamp.
        """
        if broker_id is not None:
            adapter = self.get_broker(broker_id)
            if adapter is not None:
                return await adapter.get_orders(status=status, limit=limit)
            raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")

        all_orders: list[OrderResult] = []
        for adapter in self._brokers.values():
            if adapter._connected and adapter.connection_state != ConnectionState.DISCONNECTED:
                try:
                    ords = await adapter.get_orders(status=status, limit=limit)
                    all_orders.extend(ords)
                except Exception as e:
                    logger.warning("Failed to fetch orders from '%s': %s", adapter.broker_id, e)

        all_orders.sort(key=lambda x: x.submitted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return all_orders[:limit]

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        """
        Multiplex trade update streams from all registered brokers into a single unified stream.
        """
        combined_queue: asyncio.Queue[TradeUpdateEvent] = asyncio.Queue()

        async def _forward(adapter: BaseBrokerAdapter) -> None:
            try:
                async for evt in adapter.stream_trade_updates():
                    await combined_queue.put(evt)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("Error in trade stream for '%s': %s", adapter.broker_id, e)

        tasks = [asyncio.create_task(_forward(a)) for a in self._brokers.values()]
        try:
            while True:
                evt = await combined_queue.get()
                yield evt
        finally:
            for t in tasks:
                t.cancel()

    # ------------------------------------------------------------------
    # Health, Diagnostics & Snapshots
    # ------------------------------------------------------------------

    async def check_health(self, broker_id: Optional[str] = None) -> Union[BrokerHealthStatus, dict[str, BrokerHealthStatus]]:
        """Check health status for a specific broker or all registered brokers."""
        if broker_id is not None:
            adapter = self.get_broker(broker_id)
            if adapter is None:
                raise BrokerNotRegisteredError(f"Broker '{broker_id}' is not registered")
            return adapter.get_health_status()
        return {b_id: a.get_health_status() for b_id, a in self._brokers.items()}

    async def ping_broker(self, broker_id: str) -> float:
        """Run an on-demand heartbeat ping against a specific broker."""
        return await self.heartbeat_monitor.ping_broker(broker_id)

    async def run_heartbeat_cycle(self) -> dict[str, BrokerHealthStatus]:
        """Run an on-demand heartbeat cycle across all registered brokers."""
        return await self.heartbeat_monitor.probe_all_brokers()

    def get_gateway_status(self) -> GatewayStatusSnapshot:
        """Generate a complete telemetry snapshot of the gateway."""
        try:
            active = self.resolve_active_broker().broker_id
        except Exception:
            active = None

        return GatewayStatusSnapshot(
            active_broker_id=active,
            manual_override_broker_id=self._manual_override_broker_id,
            priority_hierarchy=list(self._priority_hierarchy),
            brokers={b_id: a.get_health_status() for b_id, a in self._brokers.items()},
            total_orders_routed=self._total_orders_routed,
            total_failovers=self._total_failovers,
            last_failover_time=self._last_failover_time,
            last_failover_reason=self._last_failover_reason,
            recent_routing_audits=list(self._routing_audits),
        )

    def get_status_snapshot(self) -> GatewayStatusSnapshot:
        """Alias for get_gateway_status() returning full telemetry snapshot."""
        return self.get_gateway_status()

    def get_routing_audits(self, limit: int = 50) -> list[RoutingAuditTrail]:
        """Retrieve recent routing audit trails."""
        audits = list(self._routing_audits)
        return audits[-limit:]
