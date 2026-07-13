"""
tests/test_reconciliation.py
==============================
Verifies the state-reconciliation logic in OrderManager.

Uses a mock broker and a mock TransactionsStore to inject controlled drift
scenarios without any real network I/O or database writes.

Tests
-----
- test_no_drift_report_ok             — broker == internal → ReconciliationReport.ok
- test_broker_has_extra_position      — broker holds what internal doesn't → drift flagged
- test_internal_has_extra_position    — internal shows open trade broker doesn't → drift
- test_quantity_mismatch              — same symbol but different qty → drift
- test_report_has_drift_property      — has_drift / ok properties work correctly
- test_drift_description_format       — drift item description contains key fields
- test_broker_error_captured          — broker raises → report.error set, no exception
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import AsyncIterator
from unittest.mock import MagicMock

import pandas as pd
import pytest

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    PositionSnapshot,
)
from execution.order_manager import OrderManager, ReconciliationReport


# ---------------------------------------------------------------------------
# Mock broker: configurable open positions
# ---------------------------------------------------------------------------

class PositionMockBroker(BrokerBase):
    """Returns a fixed list of positions; everything else is no-op."""

    def __init__(self, positions: list[PositionSnapshot]) -> None:
        self._positions = positions

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        return OrderResult(
            client_order_id="",
            broker_order_id="mock",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def get_open_positions(self) -> list[PositionSnapshot]:
        return list(self._positions)

    async def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=200_000.0)

    async def get_orders(self, status=None, limit=100) -> list[OrderResult]:
        return []

    async def stream_trade_updates(self) -> AsyncIterator:
        return
        yield


class ErrorBroker(PositionMockBroker):
    """Raises on get_open_positions to test error capture."""

    async def get_open_positions(self):
        raise RuntimeError("Simulated broker API error")


# ---------------------------------------------------------------------------
# Mock TransactionsStore: returns a fixed open_trades_df
# ---------------------------------------------------------------------------

def _mock_ts(symbol_qty: dict[str, float]):
    """Build a mock TransactionsStore with the given open positions."""
    ts = MagicMock()
    if symbol_qty:
        records = [
            {"symbol": sym, "shares": qty, "exit_ts": None}
            for sym, qty in symbol_qty.items()
        ]
        ts.open_trades_df.return_value = pd.DataFrame(records)
    else:
        ts.open_trades_df.return_value = pd.DataFrame()
    return ts


def _pos(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_entry_price=100.0,
        market_value=qty * 100.0,
        unrealized_pl=0.0,
    )


# ---------------------------------------------------------------------------
# Helper to run reconcile synchronously
# ---------------------------------------------------------------------------

def _reconcile(
    broker_positions: list[PositionSnapshot],
    internal_positions: dict[str, float],
) -> ReconciliationReport:
    broker = PositionMockBroker(broker_positions)
    ts = _mock_ts(internal_positions)
    om = OrderManager(broker, dry_run=True)
    return asyncio.run(om.reconcile_state(ts))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_drift_report_ok():
    """Broker and internal agree → report.ok is True."""
    report = _reconcile(
        broker_positions=[_pos("AAPL", 10.0)],
        internal_positions={"AAPL": 10.0},
    )
    assert report.ok, f"Expected OK but got drift: {report.drift_items}"
    assert not report.has_drift
    assert report.error is None


def test_broker_has_extra_position():
    """Broker holds MSFT that internal knows nothing about → drift."""
    report = _reconcile(
        broker_positions=[_pos("AAPL", 5.0), _pos("MSFT", 3.0)],
        internal_positions={"AAPL": 5.0},
    )
    assert report.has_drift
    symbols_with_drift = {d.symbol for d in report.drift_items}
    assert "MSFT" in symbols_with_drift
    # AAPL should be clean
    assert "AAPL" not in symbols_with_drift


def test_internal_has_extra_position():
    """Internal shows TSLA open but broker has flat → drift."""
    report = _reconcile(
        broker_positions=[],
        internal_positions={"TSLA": 2.0},
    )
    assert report.has_drift
    assert any(d.symbol == "TSLA" for d in report.drift_items)
    tsla_item = next(d for d in report.drift_items if d.symbol == "TSLA")
    assert tsla_item.broker_qty == pytest.approx(0.0)
    assert tsla_item.internal_qty == pytest.approx(2.0)


def test_quantity_mismatch():
    """Broker holds 10 shares but internal shows 7 → drift."""
    report = _reconcile(
        broker_positions=[_pos("SPY", 10.0)],
        internal_positions={"SPY": 7.0},
    )
    assert report.has_drift
    item = next(d for d in report.drift_items if d.symbol == "SPY")
    assert item.broker_qty == pytest.approx(10.0)
    assert item.internal_qty == pytest.approx(7.0)


def test_report_has_drift_property():
    """has_drift and ok are logical inverses."""
    clean = _reconcile([], {})
    assert clean.ok
    assert not clean.has_drift

    dirty = _reconcile([_pos("X", 1.0)], {})
    assert dirty.has_drift
    assert not dirty.ok


def test_drift_description_format():
    """DriftItem.description contains broker qty, internal qty, and delta."""
    report = _reconcile(
        broker_positions=[_pos("NVDA", 5.0)],
        internal_positions={"NVDA": 3.0},
    )
    assert report.has_drift
    desc = report.drift_items[0].description
    assert "broker=" in desc
    assert "internal=" in desc
    assert "delta=" in desc


def test_broker_error_captured():
    """When broker.get_open_positions() raises, report.error is set, not exception."""
    broker = ErrorBroker([])
    ts = _mock_ts({})
    om = OrderManager(broker, dry_run=True)

    report = asyncio.run(om.reconcile_state(ts))

    assert report.error is not None
    assert "Simulated broker API error" in report.error
    assert not report.has_drift  # empty drift list; error is the indicator


def test_empty_both_sides_ok():
    """No broker positions, no internal positions → clean."""
    report = _reconcile([], {})
    assert report.ok
    assert report.broker_positions_count == 0
    assert report.internal_positions_count == 0


def test_drift_routes_through_multichannel_alert():
    """Drift dispatches through observability.alerts.send_alert at CRITICAL.

    (Full coverage of the unified-alerting wiring lives in
    tests/test_execution_alerts.py; this is a minimal in-place guard so a
    regression here fails the reconciliation suite too.)"""
    from unittest import mock

    broker = PositionMockBroker([_pos("MSFT", 3.0)])
    ts = _mock_ts({})  # MSFT drift
    om = OrderManager(broker, dry_run=True)

    with mock.patch("observability.alerts.send_alert") as m_alert:
        report = asyncio.run(om.reconcile_state(ts))

    assert report.has_drift
    assert m_alert.called
    assert m_alert.call_args[0][0] == "CRITICAL"


def test_raising_alert_path_does_not_break_reconcile():
    """A raising send_alert must not set report.error or raise (dead-letter)."""
    from unittest import mock

    broker = PositionMockBroker([_pos("MSFT", 3.0)])
    ts = _mock_ts({})
    om = OrderManager(broker, dry_run=True)

    with mock.patch(
        "observability.alerts.send_alert", side_effect=RuntimeError("boom")
    ):
        report = asyncio.run(om.reconcile_state(ts))

    assert report.has_drift
    assert report.error is None
