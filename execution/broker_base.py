"""
execution/broker_base.py
========================
Abstract base class (BrokerBase) defining the minimal async interface every
concrete broker adapter must satisfy.  Strategy and order-management code
should type-annotate against BrokerBase — never against a concrete adapter —
so swapping paper ↔ live ↔ mock requires only a single DI change in the
orchestrator.

Design notes
------------
* All methods are **async** so the orchestrator can await them inside an event
  loop without blocking the analysis pipeline.
* ``stream_trade_updates`` is an async generator that the caller must async-for
  over; it is the only long-running method and is expected to run in a
  background task.
* Concrete adapters MUST NOT fabricate state on error — raise or return a
  clearly typed failure result (e.g. OrderResult with status="error").
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Optional


class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"         # broker acknowledged
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    ERROR = "error"               # local error before broker contact


@dataclass
class OrderIntent:
    """Caller-supplied order specification.

    ``client_order_id`` is deterministically derived by
    ``order_manager.make_client_order_id`` — callers should leave it ``None``
    and let the manager fill it in so idempotency is guaranteed.
    """
    strategy_id: str
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    time_in_force: str = "day"
    # Populated by order_manager before submission; leave None at construction.
    client_order_id: Optional[str] = None
    # Multi-leg options support (spread / condor legs as list of dicts).
    # Each dict: {"symbol": str, "ratio_qty": float, "side": OrderSide}
    legs: list[dict] = field(default_factory=list)
    dry_run: bool = False


@dataclass
class OrderResult:
    """Normalised result returned by every broker after a submit_order call."""
    client_order_id: str
    broker_order_id: Optional[str]       # None if dry-run or pre-submission error
    status: OrderStatus
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class AccountSnapshot:
    """Minimal account state; expand as needed."""
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"


@dataclass
class PositionSnapshot:
    symbol: str
    qty: float           # positive = long, negative = short
    avg_entry_price: float
    market_value: float
    unrealized_pl: float


@dataclass
class TradeUpdateEvent:
    """Single event from the broker's trade-update stream."""
    event_type: str              # "fill", "partial_fill", "canceled", "rejected", …
    broker_order_id: str
    client_order_id: Optional[str]
    symbol: str
    side: OrderSide
    filled_qty: float
    filled_avg_price: Optional[float]
    timestamp: datetime


class BrokerBase(ABC):
    """
    Async interface every broker adapter must implement.

    Concrete subclasses:
      * AlpacaBroker  — paper / live via alpaca-py TradingClient
      * MockBroker    — in-memory stub for unit tests

    NEVER call concrete broker methods directly from strategy or orchestrator
    code — always go through this interface + order_manager.
    """

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Submit an order to the broker.

        Idempotency (via client_order_id) is enforced by order_manager
        before reaching this method; adapters should still propagate the
        client_order_id to the broker so duplicate detection works at the
        broker level too.

        Must NOT raise on expected broker rejections (e.g. insufficient
        funds, market closed) — encode them in OrderResult.status instead.
        MAY raise on unrecoverable local errors (network timeout after
        all retries, auth failure).
        """

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order by its broker-assigned ID.

        Returns True on success, False if the order was already terminal.
        Raises on auth / network errors.
        """

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_open_positions(self) -> list[PositionSnapshot]:
        """Return all currently open positions."""

    @abstractmethod
    async def get_account(self) -> AccountSnapshot:
        """Return current account equity / cash / buying power."""

    @abstractmethod
    async def get_orders(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[OrderResult]:
        """Return recent orders.  ``status`` filters by OrderStatus value string."""

    # ------------------------------------------------------------------
    # Streaming (long-running background task)
    # ------------------------------------------------------------------

    @abstractmethod
    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        """Async generator yielding TradeUpdateEvents from the broker stream.

        Callers must run this inside a background asyncio.Task and cancel it
        on shutdown.  Concrete implementations must handle reconnection and
        MUST NOT swallow errors silently — log CRITICAL and re-raise.

        Usage::

            async for event in broker.stream_trade_updates():
                handle(event)
        """
        # Make mypy happy with the generator return type.
        yield  # type: ignore[misc]
