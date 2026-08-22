"""tests/test_order_manager_execution_audit_wiring.py
======================================================
Regression coverage for the execution-audit-store wiring fix:
``execution/order_manager.py::OrderManager`` now best-effort persists every
real fill into ``data/execution_audit_store.py::ExecutionAuditStore`` (the
durable table backing ``execution/sec_rule_606_reporter.py``'s SEC Rule 606
reporting), which was previously never called from any production order path
-- ``GET /pilots/execution/sec-606/report`` always read an empty table and
returned an honest all-zero report, but the whole feature was disconnected
end-to-end from real fills.

Sync test functions driving async methods via asyncio.run() -- matches this
repo's established pattern (tests/test_fmp_paper_broker.py); no
pytest-asyncio/anyio plugin is registered here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from execution.fmp_paper_broker import FMPPaperBroker
from execution.kill_switch import GlobalKillSwitch
from execution.order_manager import OrderManager
from execution.sec_rule_606_reporter import SecRule606Reporter
from data.execution_audit_store import ExecutionAuditStore


_FIXED_TS = datetime(2024, 1, 15, 10, 0, 0)


def _intent(**overrides) -> OrderIntent:
    defaults = {
        "strategy_id": "test_strategy",
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "qty": 10.0,
        "order_type": OrderType.MARKET,
    }
    defaults.update(overrides)
    return OrderIntent(**defaults)


class FilledMockBroker(BrokerBase):
    """In-memory broker stub that fills synchronously, mirroring
    FMPPaperBroker's real "fill-or-fail immediately" contract -- unlike
    tests/test_order_manager_idempotency.py::MockBroker, which only returns
    ACCEPTED with no fill (deliberately, so this file's "no phantom audit
    record" test can use it unmodified)."""

    def __init__(self) -> None:
        self.submitted: list[OrderIntent] = []

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.submitted.append(intent)
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id=f"mock-{len(self.submitted)}",
            status=OrderStatus.FILLED,
            filled_qty=intent.qty,
            filled_avg_price=150.0,
            submitted_at=_FIXED_TS,
            filled_at=_FIXED_TS,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def get_open_positions(self):
        return []

    async def get_account(self):
        return AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)

    async def get_orders(self, status=None, limit=100):
        return []

    async def stream_trade_updates(self):
        return
        yield  # pragma: no cover - makes this an async generator


class AcceptedOnlyMockBroker(BrokerBase):
    """Broker stub that only ever ACCEPTs with zero fill -- e.g. a real
    broker's initial ack before an async fill event arrives later via
    stream_trade_updates."""

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
        return AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)

    async def get_orders(self, status=None, limit=100):
        return []

    async def stream_trade_updates(self):
        return
        yield  # pragma: no cover


def test_real_fill_creates_execution_audit_record(tmp_path):
    """A real fill routed through OrderManager must land exactly one row in
    the injected ExecutionAuditStore, with the right symbol/side/fill_price/
    executed_shares/venue."""
    store = ExecutionAuditStore(sqlite_path=str(tmp_path / "audit.db"))
    broker = FilledMockBroker()
    om = OrderManager(broker, dry_run=False, audit_store=store)

    result = asyncio.run(
        om.submit_order_with_idempotency(_intent(symbol="AAPL", qty=10.0), timestamp=_FIXED_TS)
    )
    assert result.status == OrderStatus.FILLED

    records = store.get_all_records()
    assert len(records) == 1
    rec = records[0]
    assert rec["symbol"] == "AAPL"
    assert rec["side"] == "buy"
    assert rec["fill_price"] == 150.0
    assert rec["executed_shares"] == 10.0
    assert rec["venue"] == "FILLEDMOCKBROKER"


def test_non_fill_result_does_not_create_audit_record(tmp_path):
    """A bare ACCEPTED result with zero fill (e.g. a real broker's initial
    ack before an async fill event) must not create a phantom audit row --
    only a genuine fill (filled_qty > 0) is recorded."""
    store = ExecutionAuditStore(sqlite_path=str(tmp_path / "audit.db"))
    broker = AcceptedOnlyMockBroker()
    om = OrderManager(broker, dry_run=False, audit_store=store)

    result = asyncio.run(
        om.submit_order_with_idempotency(_intent(), timestamp=_FIXED_TS)
    )
    assert result.status == OrderStatus.ACCEPTED
    assert store.count() == 0


def test_dry_run_does_not_create_audit_record(tmp_path):
    """dry_run=True never reaches the broker and must never create an audit
    record either."""
    store = ExecutionAuditStore(sqlite_path=str(tmp_path / "audit.db"))
    broker = FilledMockBroker()
    om = OrderManager(broker, dry_run=True, audit_store=store)

    result = asyncio.run(
        om.submit_order_with_idempotency(_intent(), timestamp=_FIXED_TS)
    )
    assert result.status == OrderStatus.ACCEPTED
    assert len(broker.submitted) == 0
    assert store.count() == 0


def test_paper_fill_produces_nonempty_sec_606_report(tmp_path):
    """Flagship regression: a real (paper) order fill must result in a
    non-empty audit record AND a non-all-zero SEC Rule 606 report -- closing
    the gap where SecRule606Reporter always read an empty table."""
    store = ExecutionAuditStore(sqlite_path=str(tmp_path / "audit.db"))
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    kill_switch = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    om = OrderManager(broker, dry_run=False, kill_switch=kill_switch, audit_store=store)

    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0, "marketCap": 2e12}]):
        result = asyncio.run(
            om.submit_order_with_idempotency(_intent(qty=10.0), timestamp=_FIXED_TS)
        )
    assert result.status == OrderStatus.FILLED

    reporter = SecRule606Reporter(audit_store=store)
    report = reporter.generate_report_for_date_range(
        start_date=datetime(2000, 1, 1), end_date=datetime(2100, 1, 1)
    )

    assert report["summary"]["total_orders"] == 1
    assert report["summary"]["total_notional"] > 0
