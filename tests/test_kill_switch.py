"""
tests/test_kill_switch.py
==========================
Unit tests for execution/kill_switch.py and its integration with OrderManager.

Coverage
--------
* GlobalKillSwitch lifecycle: activate, deactivate, reason, idempotency.
* OrderManager raises KillSwitchActiveError BEFORE broker contact.
* Deactivating re-enables order submission.
* Kill-switch check precedes dedup (a known coid cannot bypass it).
* Dry-run does NOT bypass the kill switch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    TradeUpdateEvent,
)
from execution.kill_switch import GlobalKillSwitch, KillSwitchActiveError
from execution.order_manager import OrderManager


# ---------------------------------------------------------------------------
# Minimal broker stub
# ---------------------------------------------------------------------------

class MockBroker(BrokerBase):
    """Stub broker that records submit calls and always returns ACCEPTED."""

    def __init__(self):
        self.submit_count = 0

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        self.submit_count += 1
        return OrderResult(
            client_order_id=intent.client_order_id or "mock-id",
            broker_order_id="brk-001",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, client_order_id: str) -> bool:
        return True

    async def get_open_positions(self) -> list[PositionSnapshot]:
        return []

    async def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(equity=100_000.0, cash=50_000.0, buying_power=50_000.0)

    async def get_orders(self) -> list[OrderResult]:
        return []

    async def stream_trade_updates(self) -> AsyncGenerator[TradeUpdateEvent, None]:
        return
        yield  # make it an async generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_ks(tmp_path: Path) -> GlobalKillSwitch:
    """A GlobalKillSwitch pointing at a temp directory sentinel file."""
    return GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")


@pytest.fixture()
def broker() -> MockBroker:
    return MockBroker()


def _buy_intent(symbol: str = "AAPL") -> OrderIntent:
    return OrderIntent(
        strategy_id="test",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
    )


# ---------------------------------------------------------------------------
# GlobalKillSwitch unit tests
# ---------------------------------------------------------------------------

class TestGlobalKillSwitch:
    def test_initially_inactive(self, tmp_ks: GlobalKillSwitch):
        assert not tmp_ks.is_active()

    def test_activate_sets_active(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate(reason="circuit breaker")
        assert tmp_ks.is_active()

    def test_deactivate_removes_sentinel(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate()
        tmp_ks.deactivate()
        assert not tmp_ks.is_active()

    def test_reason_stored_and_retrieved(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate(reason="manual stop")
        assert "manual stop" in tmp_ks.reason()

    def test_reason_empty_when_inactive(self, tmp_ks: GlobalKillSwitch):
        assert tmp_ks.reason() == ""

    def test_activate_idempotent(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.activate(reason="first")
        tmp_ks.activate(reason="second")  # should not crash
        assert tmp_ks.is_active()

    def test_deactivate_idempotent(self, tmp_ks: GlobalKillSwitch):
        tmp_ks.deactivate()  # called when not active — should not crash
        assert not tmp_ks.is_active()


# ---------------------------------------------------------------------------
# OrderManager + KillSwitch integration
# ---------------------------------------------------------------------------

class TestOrderManagerKillSwitch:
    def test_blocks_order_when_active(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        tmp_ks.activate(reason="test block")
        om = OrderManager(broker, kill_switch=tmp_ks)
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(om.submit_order_with_idempotency(_buy_intent()))
        assert broker.submit_count == 0

    def test_allows_order_when_inactive(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        om = OrderManager(broker, kill_switch=tmp_ks)
        result = asyncio.run(
            om.submit_order_with_idempotency(_buy_intent(), timestamp=datetime.now(timezone.utc))
        )
        assert result.status == OrderStatus.ACCEPTED
        assert broker.submit_count == 1

    def test_deactivate_re_enables_orders(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        tmp_ks.activate()
        tmp_ks.deactivate()
        om = OrderManager(broker, kill_switch=tmp_ks)
        result = asyncio.run(
            om.submit_order_with_idempotency(_buy_intent(), timestamp=datetime.now(timezone.utc))
        )
        assert result.status == OrderStatus.ACCEPTED

    def test_kill_switch_checked_before_dedup(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        """Even a previously submitted coid cannot bypass an active kill switch."""
        om = OrderManager(broker, kill_switch=tmp_ks)
        ts = datetime.now(timezone.utc)
        # Submit once successfully
        asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=ts))
        # Now activate kill switch
        tmp_ks.activate(reason="activated mid-test")
        # Attempt the same order again — should raise, not return the cached ACCEPTED
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(om.submit_order_with_idempotency(_buy_intent(), timestamp=ts))

    def test_dry_run_does_not_bypass_kill_switch(self, tmp_ks: GlobalKillSwitch, broker: MockBroker):
        """dry_run=True still raises KillSwitchActiveError; kill switch is not a 'safety bypass'."""
        tmp_ks.activate(reason="test dry-run block")
        om = OrderManager(broker, kill_switch=tmp_ks, dry_run=True)
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(om.submit_order_with_idempotency(_buy_intent()))
        assert broker.submit_count == 0


# ---------------------------------------------------------------------------
# activate() -> observability.alerts.send_alert wiring (Phase O3)
# ---------------------------------------------------------------------------

class TestKillSwitchAlertDispatch:
    """``GlobalKillSwitch.activate()`` must fire a CRITICAL alert via
    ``observability.alerts.send_alert`` out-of-band from the ``logger.critical``
    call, so an operator relying only on Discord/Slack/email (not tailing
    logs) is still notified — the platform's single highest-value
    observability gap per docs/OBSERVABILITY_PLAN.md.
    """

    def test_activate_calls_send_alert_critical(self, tmp_ks: GlobalKillSwitch):
        from unittest import mock
        with mock.patch("observability.alerts.send_alert") as m_alert:
            tmp_ks.activate(reason="circuit breaker tripped")
        assert m_alert.called
        args, kwargs = m_alert.call_args
        assert args[0] == "CRITICAL"
        assert "circuit breaker tripped" in args[1]
        assert kwargs.get("dedup_key") == "kill_switch_activate"

    def test_deactivate_does_not_call_send_alert(self, tmp_ks: GlobalKillSwitch):
        """Deactivation is a recovery action, not an incident — no alert expected."""
        from unittest import mock
        tmp_ks.activate(reason="setup")
        with mock.patch("observability.alerts.send_alert") as m_alert:
            tmp_ks.deactivate()
        assert not m_alert.called

    def test_raising_send_alert_does_not_prevent_activation(self, tmp_ks: GlobalKillSwitch):
        """A broken alert dispatch must never stop the kill switch from activating —
        the safety-critical action (writing the sentinel file) must always succeed."""
        from unittest import mock
        with mock.patch(
            "observability.alerts.send_alert", side_effect=RuntimeError("webhook down")
        ):
            tmp_ks.activate(reason="test")  # must not raise
        assert tmp_ks.is_active()

    def test_repeated_activate_dedup_suppresses_after_first(self, tmp_ks: GlobalKillSwitch):
        """activate() is idempotent and may be called repeatedly by a watchdog;
        the real (non-mocked) send_alert's dedup_key must collapse repeat
        activations within the TTL window to a single dispatched alert.

        ``_active_channels`` is pinned to ``["console"]`` so this test is
        deterministic regardless of whatever Discord/Slack/email settings
        happen to be configured in the environment running the suite.
        """
        from observability.alerts import reset_dedup_state
        reset_dedup_state()
        calls: list[str] = []

        def fake_console(level, ts, message):
            calls.append(message)

        from unittest import mock
        with mock.patch("observability.alerts._active_channels", return_value=["console"]):
            with mock.patch("observability.alerts._send_console", fake_console):
                tmp_ks.activate(reason="first")
                tmp_ks.activate(reason="second")  # same dedup_key, within window — suppressed
        assert len(calls) == 1
        reset_dedup_state()
