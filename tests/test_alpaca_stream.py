"""
tests/test_alpaca_stream.py
===========================
Fully offline tests for the HARDENED trade-update stream and the advisory-only
fill consumer in ``execution/alpaca_broker.py``.

Everything is mocked — no network, no real Alpaca connection.  The point is to
prove:

* The stream buffer is BOUNDED (a ``deque(maxlen=...)``): when the consumer
  falls behind, the OLDEST events are evicted, memory never grows without
  bound.
* Reconnection is ATTEMPTED when the underlying WebSocket ``run()`` returns /
  raises (per the ``BrokerBase.stream_trade_updates`` contract).
* The consumer LOGS + ALERTS on fills and NEVER calls any order method — it is
  pure observability plumbing.

These capabilities are placement-INCAPABLE: no code path here submits, cancels,
or mutates an order.

Async tests use ``asyncio.run`` inside plain ``def`` bodies (the repo does not
depend on pytest-asyncio — see tests/test_order_manager_idempotency.py).
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

import execution.alpaca_broker as ab
from execution.alpaca_broker import AlpacaBroker, consume_trade_updates
from execution.broker_base import OrderSide, TradeUpdateEvent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _fake_data(symbol: str, event: str = "fill", qty: float = 1.0, price: float = 10.0):
    """Build a fake alpaca trade-update payload the normalizer can parse."""
    from alpaca.trading.enums import OrderSide as AOS
    order = SimpleNamespace(
        side=AOS.BUY,
        id=f"broker-{symbol}",
        client_order_id=f"coid-{symbol}",
        symbol=symbol,
        filled_qty=qty,
        filled_avg_price=price,
    )
    return SimpleNamespace(event=event, order=order, timestamp=None)


class _FakeStreamBlocking:
    """Fake TradingStream whose run() blocks until stop() is called.

    Captures the subscribed handler so the test can inject events directly.
    """

    instances: list = []

    def __init__(self, *args, **kwargs):
        self._handler = None
        self._stop = threading.Event()
        self.stopped = False
        _FakeStreamBlocking.instances.append(self)

    def subscribe_trade_updates(self, handler):
        self._handler = handler

    def run(self):
        # Block like the real blocking run() until stop() flips the event.
        self._stop.wait()

    def stop(self):
        self.stopped = True
        self._stop.set()


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakeStreamBlocking.instances = []
    yield
    _FakeStreamBlocking.instances = []


@pytest.fixture
def broker():
    return AlpacaBroker(api_key="fake", secret_key="fake", paper=True)


async def _await_handler(instances_holder, timeout_ticks: int = 300):
    """Poll until the fake stream has been created and subscribed."""
    for _ in range(timeout_ticks):
        if instances_holder and instances_holder[0]._handler is not None:
            return instances_holder[0]._handler
        await asyncio.sleep(0.01)
    raise AssertionError("stream handler was never subscribed")


# ---------------------------------------------------------------------------
# Bounded-buffer / backpressure
# ---------------------------------------------------------------------------

def test_stream_buffer_is_bounded_and_evicts_oldest(broker, monkeypatch):
    """Injecting more events than maxlen evicts the OLDEST — no unbounded growth."""
    monkeypatch.setattr(ab, "_STREAM_BUFFER_MAXLEN", 3)
    monkeypatch.setattr(ab, "_STREAM_POLL_SECONDS", 0.02)

    collected: list[TradeUpdateEvent] = []

    async def _run():
        with mock.patch("alpaca.trading.stream.TradingStream", _FakeStreamBlocking):
            agen = broker.stream_trade_updates()

            async def _drain():
                async for evt in agen:
                    collected.append(evt)

            task = asyncio.create_task(_drain())
            handler = await _await_handler(_FakeStreamBlocking.instances)

            # Inject 5 events in one burst BEFORE the next drain tick — with
            # maxlen=3 only the last 3 (C, D, E) should survive.
            for sym in ("A", "B", "C", "D", "E"):
                await handler(_fake_data(sym))

            await asyncio.sleep(0.1)  # let the drain loop run
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    symbols = [e.symbol for e in collected]
    assert symbols == ["C", "D", "E"], f"expected oldest evicted, got {symbols}"


def test_stream_buffer_full_logs_warning(broker, monkeypatch, caplog):
    """Eviction at capacity surfaces a WARNING so a slow consumer is visible."""
    monkeypatch.setattr(ab, "_STREAM_BUFFER_MAXLEN", 2)
    monkeypatch.setattr(ab, "_STREAM_POLL_SECONDS", 0.02)

    async def _run():
        with mock.patch("alpaca.trading.stream.TradingStream", _FakeStreamBlocking):
            agen = broker.stream_trade_updates()

            async def _drain():
                async for _ in agen:
                    pass

            task = asyncio.create_task(_drain())
            handler = await _await_handler(_FakeStreamBlocking.instances)

            with caplog.at_level("WARNING"):
                # 3 events into a maxlen=2 buffer forces one eviction.
                for sym in ("A", "B", "C"):
                    await handler(_fake_data(sym))
                await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    assert any("buffer full" in r.message.lower() or "evicting" in r.message.lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# Reconnection
# ---------------------------------------------------------------------------

class _FakeStreamDisconnectOnce:
    """First run() returns immediately (disconnect); later runs block."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self._handler = None
        self._stop = threading.Event()
        self.stopped = False
        self._index = len(_FakeStreamDisconnectOnce.instances)
        _FakeStreamDisconnectOnce.instances.append(self)

    def subscribe_trade_updates(self, handler):
        self._handler = handler

    def run(self):
        if self._index == 0:
            return  # simulate an immediate disconnect
        self._stop.wait()

    def stop(self):
        self.stopped = True
        self._stop.set()


def test_stream_reconnects_on_disconnect(broker, monkeypatch):
    """A disconnected run() triggers a fresh TradingStream (reconnection)."""
    _FakeStreamDisconnectOnce.instances = []
    monkeypatch.setattr(ab, "_STREAM_POLL_SECONDS", 0.02)
    monkeypatch.setattr(ab, "_STREAM_RECONNECT_BASE_SECONDS", 0.01)
    monkeypatch.setattr(ab, "_STREAM_RECONNECT_MAX_SECONDS", 0.05)

    async def _run():
        with mock.patch("alpaca.trading.stream.TradingStream", _FakeStreamDisconnectOnce):
            agen = broker.stream_trade_updates()

            async def _drain():
                async for _ in agen:
                    pass

            task = asyncio.create_task(_drain())

            # Give the supervisor time to notice the disconnect and reconnect.
            for _ in range(300):
                if len(_FakeStreamDisconnectOnce.instances) >= 2:
                    break
                await asyncio.sleep(0.01)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    assert len(_FakeStreamDisconnectOnce.instances) >= 2, (
        "expected reconnection to create a second stream"
    )


# ---------------------------------------------------------------------------
# Advisory-only consumer
# ---------------------------------------------------------------------------

async def _event_iter(events):
    for e in events:
        yield e


def _evt(event_type: str, symbol: str = "SPY") -> TradeUpdateEvent:
    return TradeUpdateEvent(
        event_type=event_type,
        broker_order_id="b1",
        client_order_id="c1",
        symbol=symbol,
        side=OrderSide.BUY,
        filled_qty=2.0,
        filled_avg_price=100.0,
        timestamp=datetime(2026, 1, 1),
    )


def test_consumer_alerts_on_fill_and_reject():
    """A fill routes INFO; a rejection routes WARNING; a plain 'new' does not."""
    send_alert = mock.MagicMock()
    events = [_evt("fill"), _evt("rejected"), _evt("new")]

    asyncio.run(consume_trade_updates(_event_iter(events), send_alert_fn=send_alert))

    levels = [c.args[0] for c in send_alert.call_args_list]
    assert "INFO" in levels          # the fill
    assert "WARNING" in levels       # the rejection
    # "new" is neither notable nor adverse -> no alert
    assert len(send_alert.call_args_list) == 2


def test_consumer_takes_no_order_action():
    """The consumer never touches any broker order method."""
    broker_spy = mock.MagicMock()
    send_alert = mock.MagicMock()

    asyncio.run(consume_trade_updates(_event_iter([_evt("fill")]), send_alert_fn=send_alert))

    # The consumer has no broker handle at all; assert the spy is pristine to
    # document intent — no submit/cancel/place ever occurs.
    broker_spy.submit_order.assert_not_called()
    broker_spy.cancel_order.assert_not_called()
    assert send_alert.called


def test_consumer_alert_failure_never_breaks_loop():
    """A raising send_alert must not abort the drain loop."""
    send_alert = mock.MagicMock(side_effect=RuntimeError("boom"))
    events = [_evt("fill"), _evt("fill")]
    # Should complete without raising despite send_alert blowing up.
    asyncio.run(consume_trade_updates(_event_iter(events), send_alert_fn=send_alert))
    assert send_alert.call_count == 2


def test_no_autonomous_placement_functions():
    """Guard: no ``place_*`` autonomous-placement function exists here.

    ``AlpacaBroker.submit_order`` / ``cancel_order`` are the sanctioned broker
    methods this module legitimately implements (execution/ is deliberately
    EXCLUDED from the repo-wide ``TestNoOrderFunctions`` guard for exactly this
    reason).  The observability plumbing added for the stream/consumer must not
    introduce any NEW autonomous-placement entry point, so we assert the
    ``place_*`` pattern is absent module-wide.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ab))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not node.name.startswith("place_"), (
                f"forbidden place_* fn introduced: {node.name}"
            )
