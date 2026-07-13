"""
tests/test_execution_alerts.py
==============================
Verifies the unified-alerting wiring added for the execution layer:

1. Reconciliation drift routes through the hardened multi-channel dispatcher
   (``observability.alerts.send_alert``) at CRITICAL severity, carrying the
   drift detail — IN ADDITION to the legacy ``ALERT_WEBHOOK_URL`` POST.
2. The legacy webhook still fires (back-compat preserved).
3. Dead-letter safety: a ``send_alert`` that raises does NOT break
   ``reconcile_state`` (the reconciliation report is still returned).
4. The end-of-cycle daily summary (``observability.alerts.send_daily_summary``)
   is invoked at the end of a successful ``_main_body_impl`` cycle, and a
   raising summary does NOT crash the cycle.

All network / SDK I/O is mocked; nothing hits a real broker, webhook, or DB
row that matters. No safety gate is exercised or weakened here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest import mock
from unittest.mock import MagicMock

import pandas as pd
import pytest

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderStatus,
    PositionSnapshot,
)
from execution.order_manager import OrderManager


# ---------------------------------------------------------------------------
# Local broker + store mocks (defined here so this file does not depend on any
# shared conftest fixture that may not exist on the base branch).
# ---------------------------------------------------------------------------

class _PositionMockBroker(BrokerBase):
    """Returns a fixed list of positions; every other method is a no-op."""

    def __init__(self, positions: list[PositionSnapshot]) -> None:
        self._positions = positions

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        return OrderResult(client_order_id="", broker_order_id="mock", status=OrderStatus.ACCEPTED)

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


def _pos(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_entry_price=100.0,
        market_value=qty * 100.0,
        unrealized_pl=0.0,
    )


def _mock_ts(symbol_qty: dict[str, float]):
    ts = MagicMock()
    if symbol_qty:
        records = [
            {"symbol": s, "shares": q, "exit_ts": None} for s, q in symbol_qty.items()
        ]
        ts.open_trades_df.return_value = pd.DataFrame(records)
    else:
        ts.open_trades_df.return_value = pd.DataFrame()
    return ts


# ---------------------------------------------------------------------------
# 1 + 2: drift routes through send_alert(CRITICAL) AND the legacy webhook
# ---------------------------------------------------------------------------

def test_drift_routes_through_multichannel_send_alert():
    """A reconciliation drift invokes observability.alerts.send_alert with
    CRITICAL and the drift symbol in the message/extra."""
    broker = _PositionMockBroker([_pos("AAPL", 5.0), _pos("MSFT", 3.0)])
    ts = _mock_ts({"AAPL": 5.0})  # MSFT drift
    om = OrderManager(broker, dry_run=True)  # no webhook URL configured

    with mock.patch("observability.alerts.send_alert") as m_alert:
        report = asyncio.run(om.reconcile_state(ts))

    assert report.has_drift
    assert m_alert.called, "multi-channel send_alert must fire on drift"
    args, kwargs = m_alert.call_args
    assert args[0] == "CRITICAL"
    message = args[1]
    assert "RECONCILIATION DRIFT" in message
    assert "MSFT" in message
    extra = kwargs.get("extra", {})
    assert extra.get("type") == "reconciliation_drift"
    drift_symbols = {d["symbol"] for d in extra.get("drift", [])}
    assert "MSFT" in drift_symbols


def test_legacy_webhook_still_fires_alongside_send_alert():
    """When ALERT_WEBHOOK_URL is set, the legacy urllib POST still fires in
    addition to the multi-channel dispatcher."""
    broker = _PositionMockBroker([_pos("SPY", 10.0)])
    ts = _mock_ts({"SPY": 7.0})  # qty drift
    om = OrderManager(broker, dry_run=True, alert_webhook_url="https://hooks.example/webhook")

    with mock.patch("observability.alerts.send_alert") as m_alert, \
         mock.patch("urllib.request.urlopen") as m_urlopen:
        report = asyncio.run(om.reconcile_state(ts))

    assert report.has_drift
    assert m_alert.called, "multi-channel path must still fire"
    assert m_urlopen.called, "legacy webhook POST must still fire"
    # The webhook payload carries the same drift message text.
    posted_req = m_urlopen.call_args[0][0]
    assert posted_req.full_url == "https://hooks.example/webhook"


def test_no_drift_dispatches_no_alert():
    """A clean reconciliation neither raises nor dispatches any alert."""
    broker = _PositionMockBroker([_pos("AAPL", 5.0)])
    ts = _mock_ts({"AAPL": 5.0})
    om = OrderManager(broker, dry_run=True, alert_webhook_url="https://hooks.example/webhook")

    with mock.patch("observability.alerts.send_alert") as m_alert, \
         mock.patch("urllib.request.urlopen") as m_urlopen:
        report = asyncio.run(om.reconcile_state(ts))

    assert report.ok
    assert not m_alert.called
    assert not m_urlopen.called


# ---------------------------------------------------------------------------
# 3: dead-letter safety — a raising send_alert must not break reconcile_state
# ---------------------------------------------------------------------------

def test_raising_send_alert_does_not_break_reconcile():
    """If the multi-channel dispatcher raises, reconcile_state still returns a
    valid report (drift is still recorded) and the legacy webhook still fires."""
    broker = _PositionMockBroker([_pos("NVDA", 4.0)])
    ts = _mock_ts({})  # NVDA drift (broker holds, internal flat)
    om = OrderManager(broker, dry_run=True, alert_webhook_url="https://hooks.example/webhook")

    with mock.patch("observability.alerts.send_alert", side_effect=RuntimeError("boom")), \
         mock.patch("urllib.request.urlopen") as m_urlopen:
        report = asyncio.run(om.reconcile_state(ts))

    assert report.has_drift
    assert report.error is None, "a raising alert path must not set report.error"
    assert any(d.symbol == "NVDA" for d in report.drift_items)
    # Isolation: the legacy webhook still fires even though send_alert raised.
    assert m_urlopen.called


def test_raising_webhook_does_not_break_reconcile():
    """A raising legacy webhook must also not break reconcile_state, and must
    not suppress the multi-channel dispatch that runs first."""
    broker = _PositionMockBroker([_pos("TSLA", 2.0)])
    ts = _mock_ts({})
    om = OrderManager(broker, dry_run=True, alert_webhook_url="https://hooks.example/webhook")

    with mock.patch("observability.alerts.send_alert") as m_alert, \
         mock.patch("urllib.request.urlopen", side_effect=RuntimeError("net down")):
        report = asyncio.run(om.reconcile_state(ts))

    assert report.has_drift
    assert report.error is None
    assert m_alert.called


# ---------------------------------------------------------------------------
# 4: end-of-cycle daily summary wiring in main_orchestrator._main_body_impl
# ---------------------------------------------------------------------------

class _NoopRunner:
    """Stand-in for AsyncPipelineRunner: constructs from a step list, runs no-op."""

    def __init__(self, steps):
        self._steps = steps

    async def run(self, ctx, progress=None):
        return None


def _run_main_body_impl():
    import main_orchestrator as mo
    return asyncio.run(mo._main_body_impl(True))


def test_daily_summary_invoked_at_end_of_cycle():
    """A successful _main_body_impl cycle invokes send_daily_summary once."""
    with mock.patch("pipeline.runner.AsyncPipelineRunner", _NoopRunner), \
         mock.patch("pipeline.production_steps.AsyncDataFetchStep", MagicMock), \
         mock.patch("pipeline.production_steps.RunPipelineStep", MagicMock), \
         mock.patch("pipeline.production_steps.BrokerExecutionStep", MagicMock), \
         mock.patch("pipeline.production_steps.StateSnapshotStep", MagicMock), \
         mock.patch("observability.alerts.send_daily_summary") as m_summary:
        _run_main_body_impl()

    assert m_summary.called, "send_daily_summary must fire at end of a successful cycle"
    pnl_summary, warnings = m_summary.call_args[0]
    assert isinstance(pnl_summary, dict)
    assert isinstance(warnings, list)


def test_raising_daily_summary_does_not_crash_cycle():
    """A send_daily_summary that raises must NOT fail the pipeline cycle."""
    with mock.patch("pipeline.runner.AsyncPipelineRunner", _NoopRunner), \
         mock.patch("pipeline.production_steps.AsyncDataFetchStep", MagicMock), \
         mock.patch("pipeline.production_steps.RunPipelineStep", MagicMock), \
         mock.patch("pipeline.production_steps.BrokerExecutionStep", MagicMock), \
         mock.patch("pipeline.production_steps.StateSnapshotStep", MagicMock), \
         mock.patch("observability.alerts.send_daily_summary", side_effect=RuntimeError("summary boom")):
        # Must complete without raising.
        _run_main_body_impl()


# ---------------------------------------------------------------------------
# _build_daily_summary unit behavior
# ---------------------------------------------------------------------------

def test_build_daily_summary_empty_when_no_closed_trades():
    """No closed trades → empty pnl_summary; empty dashboard → a warning."""
    import main_orchestrator as mo

    ctx = MagicMock()
    ctx.macro_dto = None
    ctx.dashboard_df = None

    empty_store = MagicMock()
    empty_store.closed_trades_df.return_value = pd.DataFrame()
    with mock.patch("transactions_store.TransactionsStore", return_value=empty_store):
        pnl_summary, warnings = mo._build_daily_summary(ctx)

    assert pnl_summary == {}
    assert any("empty dashboard" in w for w in warnings)


def test_build_daily_summary_aggregates_todays_realized_pnl():
    """Closed trades dated today are aggregated by strategy with correct sign."""
    import main_orchestrator as mo

    today = pd.Timestamp(datetime.now(timezone.utc).date())
    closed = pd.DataFrame(
        [
            # long win: (110-100)*10 = +100 under "alpha"
            {"strategy": "alpha", "side": "long", "entry_price": 100.0,
             "exit_price": 110.0, "shares": 10.0, "exit_ts": today},
            # short win: (90-100)*5*-1 = +50 under "beta"
            {"strategy": "beta", "side": "short", "entry_price": 100.0,
             "exit_price": 90.0, "shares": 5.0, "exit_ts": today},
            # stale trade (yesterday) must be excluded
            {"strategy": "alpha", "side": "long", "entry_price": 100.0,
             "exit_price": 999.0, "shares": 1.0, "exit_ts": today - pd.Timedelta(days=1)},
        ]
    )
    store = MagicMock()
    store.closed_trades_df.return_value = closed

    ctx = MagicMock()
    ctx.macro_dto = None
    df = MagicMock()
    df.empty = False
    ctx.dashboard_df = df

    with mock.patch("transactions_store.TransactionsStore", return_value=store):
        pnl_summary, warnings = mo._build_daily_summary(ctx)

    assert pnl_summary["alpha"] == pytest.approx(100.0)
    assert pnl_summary["beta"] == pytest.approx(50.0)
