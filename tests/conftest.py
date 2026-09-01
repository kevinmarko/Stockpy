"""
tests/conftest.py
===================
Shared pytest fixtures for the tests/ package. Kept deliberately small and
opt-in (no test-suite-wide autouse fixtures here) -- each fixture below is
requested explicitly by the files that need it, either directly as a test
parameter or wrapped in a local `@pytest.fixture(autouse=True)` shim scoped
to just that file/class. This avoids silently changing behavior for the
~150 other test files that never asked for it.
"""

from __future__ import annotations

from typing import AsyncIterator, Callable, Optional
from unittest import mock

import pytest

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderStatus,
    PositionSnapshot,
    TradeUpdateEvent,
)


# ---------------------------------------------------------------------------
# Shared in-memory MockBroker
# ---------------------------------------------------------------------------

class MockBroker(BrokerBase):
    """Shared in-memory ``BrokerBase`` stub for unit tests.

    A faithful *superset* of the three ad-hoc per-file mocks this codebase
    grew (``tests/test_order_manager_idempotency.py``,
    ``tests/test_kill_switch.py``, ``tests/test_reconciliation.py``). It
    performs no network I/O whatsoever, so tests that use it need no ALPACA
    credentials and never touch a real broker.

    Behaviour
    ---------
    * Every submitted :class:`OrderIntent` is appended to ``self.submitted``
      (and ``submit_count`` is a read-only alias for ``len(self.submitted)``).
    * ``submit_order`` returns a configurable result. By default it returns
      an ``ACCEPTED`` result with ``broker_order_id="mock-<n>"``; pass
      ``submit_result=fn(intent, n) -> OrderResult`` to override (e.g. to
      simulate a transient ``ERROR`` on the first call for retry tests).
    * ``get_open_positions`` / ``get_account`` / ``get_orders`` return the
      values injected at construction (sensible defaults otherwise).
    * ``cancel_order`` records the id in ``self.canceled`` and returns True.
    * ``stream_trade_updates`` is an empty async generator (yields nothing).

    This class is intentionally *not* wired into the three legacy test files
    (each keeps its own local mock); it is provided so new tests — and, later,
    those files — can share one faithful stub. Import it directly
    (``from tests.conftest import MockBroker``) or request the ``mock_broker``
    fixture.
    """

    def __init__(
        self,
        *,
        positions: Optional[list[PositionSnapshot]] = None,
        account: Optional[AccountSnapshot] = None,
        orders: Optional[list[OrderResult]] = None,
        submit_result: Optional[Callable[[OrderIntent, int], OrderResult]] = None,
    ) -> None:
        self.submitted: list[OrderIntent] = []
        self.canceled: list[str] = []
        self._positions: list[PositionSnapshot] = list(positions) if positions else []
        self._account: AccountSnapshot = account or AccountSnapshot(
            equity=100_000.0, cash=100_000.0, buying_power=200_000.0
        )
        self._orders: list[OrderResult] = list(orders) if orders else []
        self._submit_result = submit_result

    @property
    def submit_count(self) -> int:
        """Number of ``submit_order`` calls received (alias for len(submitted))."""
        return len(self.submitted)

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.submitted.append(intent)
        if self._submit_result is not None:
            return self._submit_result(intent, len(self.submitted))
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id=f"mock-{len(self.submitted)}",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        self.canceled.append(broker_order_id)
        return True

    async def get_open_positions(self) -> list[PositionSnapshot]:
        return list(self._positions)

    async def get_account(self) -> AccountSnapshot:
        return self._account

    async def get_orders(
        self, status: Optional[str] = None, limit: int = 100
    ) -> list[OrderResult]:
        return list(self._orders)

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        # Empty async generator — yields nothing, completes immediately.
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def mock_broker() -> MockBroker:
    """A fresh, network-free :class:`MockBroker` with default state."""
    return MockBroker()


@pytest.fixture
def disable_historical_store():
    """Disable settings.HISTORICAL_STORE_ENABLED for the duration of a test.

    Several engines (MacroEngine.compute_hmm_risk_on_probability,
    ProcessingEngine.calculate_fundamental_metrics, main_orchestrator's
    run_pipeline) route reads/writes through the real, on-disk
    HistoricalStore whenever this setting is left at its production
    default (True) -- the well-documented "HISTORICAL_STORE_ENABLED trap"
    (see CLAUDE.md and the item #7a pollution-leak root-cause). Request
    this fixture directly for a single test:

        def test_foo(self, engine, disable_historical_store):
            ...

    or wrap it in a local autouse fixture for an entire file/class whose
    tests all need it:

        @pytest.fixture(autouse=True)
        def _auto_disable_historical_store(disable_historical_store):
            pass
    """
    with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
        yield
