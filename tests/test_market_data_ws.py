"""
tests/test_market_data_ws.py
=============================
Fully offline tests for the opt-in WebSocket quote-ingestion layer in
``data/market_data_ws.py``.

Everything is mocked — no network, no real Alpaca connection. Mirrors
``tests/test_alpaca_stream.py``'s style (``asyncio.run`` inside plain ``def``
bodies; the repo does not depend on pytest-asyncio).

Proves:
* Default (flag off) reproduces today's REST-only ``CompositeProvider``
  behavior exactly -- the WS store is never even touched.
* A stale WS quote is treated as absent (falls through to REST), never
  masquerades as fresh.
* The streamer's buffer is bounded (backpressure) and it reconnects with
  backoff on disconnect, exactly mirroring ``execution/alpaca_broker.py``'s
  hardened stream contract.
* ``start_market_data_ws_thread`` is a safe no-op (never raises, no thread
  spawned) when disabled, when the active provider isn't Alpaca, or when no
  symbols resolve.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

import data.market_data_ws as mdws
from data.market_data_ws import (
    AlpacaQuoteStreamer,
    _WSQuote,
    _WSQuoteStore,
    get_ws_quote,
    start_market_data_ws_thread,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _fake_quote_payload(symbol: str, bid: float = 100.0, ask: float = 100.2):
    return SimpleNamespace(
        symbol=symbol, bid_price=bid, ask_price=ask, timestamp=datetime.now(timezone.utc)
    )


class _FakeDataStreamBlocking:
    """Fake StockDataStream whose run() blocks until stop() is called."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self._handler = None
        self._symbols = None
        self._stop = threading.Event()
        self.stopped = False
        _FakeDataStreamBlocking.instances.append(self)

    def subscribe_quotes(self, handler, *symbols):
        self._handler = handler
        self._symbols = symbols

    def run(self):
        self._stop.wait()

    def stop(self):
        self.stopped = True
        self._stop.set()


class _FakeDataStreamDisconnectOnce:
    """First run() returns immediately (disconnect); later runs block."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self._handler = None
        self._stop = threading.Event()
        self.stopped = False
        self._index = len(_FakeDataStreamDisconnectOnce.instances)
        _FakeDataStreamDisconnectOnce.instances.append(self)

    def subscribe_quotes(self, handler, *symbols):
        self._handler = handler

    def run(self):
        if self._index == 0:
            return
        self._stop.wait()

    def stop(self):
        self.stopped = True
        self._stop.set()


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakeDataStreamBlocking.instances = []
    _FakeDataStreamDisconnectOnce.instances = []
    mdws._quote_store = None
    yield
    _FakeDataStreamBlocking.instances = []
    _FakeDataStreamDisconnectOnce.instances = []
    mdws._quote_store = None


async def _await_handler(instances_holder, timeout_ticks: int = 300):
    for _ in range(timeout_ticks):
        if instances_holder and instances_holder[0]._handler is not None:
            return instances_holder[0]._handler
        await asyncio.sleep(0.01)
    raise AssertionError("stream handler was never subscribed")


# ---------------------------------------------------------------------------
# _WSQuoteStore staleness
# ---------------------------------------------------------------------------

class TestWSQuoteStore:
    def test_fresh_quote_returned(self):
        store = _WSQuoteStore(stale_seconds=10)
        q = _WSQuote(symbol="AAPL", price=100.0, bid=99.9, ask=100.1, timestamp=datetime.now(timezone.utc))
        store.put(q)
        assert store.get("AAPL") == q
        assert store.get("aapl") == q  # case-insensitive lookup

    def test_stale_quote_returns_none(self):
        store = _WSQuoteStore(stale_seconds=5)
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        q = _WSQuote(symbol="AAPL", price=100.0, bid=99.9, ask=100.1, timestamp=old_ts)
        store.put(q)
        assert store.get("AAPL") is None

    def test_absent_symbol_returns_none(self):
        store = _WSQuoteStore()
        assert store.get("NOPE") is None

    def test_get_ws_quote_none_when_no_store_initialized(self):
        mdws._quote_store = None
        assert get_ws_quote("AAPL") is None


# ---------------------------------------------------------------------------
# AlpacaQuoteStreamer -- backpressure and reconnection
# ---------------------------------------------------------------------------

def test_streamer_buffer_is_bounded_and_evicts_oldest(monkeypatch):
    monkeypatch.setattr(mdws, "_STREAM_BUFFER_MAXLEN", 3)
    monkeypatch.setattr(mdws, "_STREAM_POLL_SECONDS", 0.02)

    store = _WSQuoteStore(stale_seconds=60)
    streamer = AlpacaQuoteStreamer(
        api_key="fake", secret_key="fake", symbols=["A", "B", "C", "D", "E"], store=store,
        reconnect_base_seconds=0.01, reconnect_max_seconds=0.05,
    )

    async def _run():
        with mock.patch("alpaca.data.live.StockDataStream", _FakeDataStreamBlocking):
            task = asyncio.create_task(streamer.run_forever())
            handler = await _await_handler(_FakeDataStreamBlocking.instances)

            for sym in ("A", "B", "C", "D", "E"):
                await handler(_fake_quote_payload(sym))

            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    # Only the last 3 (maxlen=3) should have survived into the store.
    surviving = [s for s in ("A", "B", "C", "D", "E") if store.get(s) is not None]
    assert surviving == ["C", "D", "E"], f"expected oldest evicted, got {surviving}"


def test_streamer_reconnects_on_disconnect(monkeypatch):
    monkeypatch.setattr(mdws, "_STREAM_POLL_SECONDS", 0.02)

    store = _WSQuoteStore()
    streamer = AlpacaQuoteStreamer(
        api_key="fake", secret_key="fake", symbols=["AAPL"], store=store,
        reconnect_base_seconds=0.01, reconnect_max_seconds=0.05,
    )

    async def _run():
        with mock.patch("alpaca.data.live.StockDataStream", _FakeDataStreamDisconnectOnce):
            task = asyncio.create_task(streamer.run_forever())
            for _ in range(300):
                if len(_FakeDataStreamDisconnectOnce.instances) >= 2:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
    assert len(_FakeDataStreamDisconnectOnce.instances) >= 2, "expected reconnection to create a second stream"


def test_streamer_no_symbols_is_immediate_noop():
    store = _WSQuoteStore()
    streamer = AlpacaQuoteStreamer(api_key="fake", secret_key="fake", symbols=[], store=store)
    # Must return promptly without ever touching alpaca.data.live.
    asyncio.run(asyncio.wait_for(streamer.run_forever(), timeout=1.0))


def test_normalize_quote_handles_malformed_payload_gracefully():
    bad_payload = object()  # no symbol/bid_price/ask_price/timestamp attrs
    assert AlpacaQuoteStreamer._normalize_quote(bad_payload) is None


# ---------------------------------------------------------------------------
# start_market_data_ws_thread -- gating / no-op conditions
# ---------------------------------------------------------------------------

def test_start_thread_noop_when_disabled(monkeypatch):
    from settings import settings as live_settings
    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_ENABLED", False, raising=False)

    thread = start_market_data_ws_thread(symbols=["AAPL"])
    assert thread is None


def test_start_thread_noop_when_provider_not_alpaca(monkeypatch):
    from settings import settings as live_settings
    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_ENABLED", True, raising=False)

    fake_provider = SimpleNamespace(_quote_provider=SimpleNamespace())  # not an AlpacaProvider
    with mock.patch("data.market_data.get_provider", return_value=fake_provider):
        thread = start_market_data_ws_thread(symbols=["AAPL"])
    assert thread is None


def test_start_thread_noop_when_no_symbols_resolve(monkeypatch):
    from settings import settings as live_settings
    from data.market_data import AlpacaProvider

    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_ENABLED", True, raising=False)
    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_SYMBOLS", None, raising=False)
    monkeypatch.delenv("WATCHLIST", raising=False)

    fake_alpaca = AlpacaProvider.__new__(AlpacaProvider)
    fake_alpaca._api_key = "k"
    fake_alpaca._secret_key = "s"
    fake_provider = SimpleNamespace(_quote_provider=fake_alpaca)

    with mock.patch("data.market_data.get_provider", return_value=fake_provider):
        thread = start_market_data_ws_thread(symbols=None)
    assert thread is None


def test_start_thread_spawns_when_fully_configured(monkeypatch):
    from settings import settings as live_settings
    from data.market_data import AlpacaProvider

    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_ENABLED", True, raising=False)

    fake_alpaca = AlpacaProvider.__new__(AlpacaProvider)
    fake_alpaca._api_key = "k"
    fake_alpaca._secret_key = "s"
    fake_provider = SimpleNamespace(_quote_provider=fake_alpaca)

    spawned = {}

    class _FakeThread:
        def __init__(self, target, name, daemon):
            spawned["target"] = target
            spawned["name"] = name
            spawned["daemon"] = daemon

        def start(self):
            spawned["started"] = True

    with mock.patch("data.market_data.get_provider", return_value=fake_provider), \
         mock.patch("threading.Thread", _FakeThread):
        thread = start_market_data_ws_thread(symbols=["AAPL", "MSFT"])

    assert spawned.get("started") is True
    assert spawned.get("daemon") is True
    assert isinstance(thread, _FakeThread)


# ---------------------------------------------------------------------------
# CompositeProvider integration -- flag-off is byte-identical to today
# ---------------------------------------------------------------------------

def test_composite_provider_flag_off_never_touches_ws_store(monkeypatch):
    from data.market_data import CompositeProvider, Quote

    monkeypatch.setattr(mdws, "get_ws_quote", mock.MagicMock(side_effect=AssertionError("should not be called")))

    provider = CompositeProvider.__new__(CompositeProvider)
    from data.market_data import _QuoteCache
    provider._cache = _QuoteCache(ttl_seconds=30)
    rest_quote = Quote(symbol="AAPL", price=150.0, bid=149.9, ask=150.1,
                        timestamp=datetime.now(timezone.utc), is_stale=False, source="alpaca")
    provider._quote_provider = SimpleNamespace(get_latest_quote=lambda s: rest_quote)

    from settings import settings as live_settings
    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_ENABLED", False, raising=False)

    result = provider.get_latest_quote("AAPL")
    assert result == rest_quote


def test_composite_provider_uses_fresh_ws_quote_when_enabled(monkeypatch):
    from data.market_data import CompositeProvider, _QuoteCache

    monkeypatch.setattr(
        mdws, "get_ws_quote",
        lambda sym: _WSQuote(symbol=sym, price=200.0, bid=199.9, ask=200.1, timestamp=datetime.now(timezone.utc)),
    )

    provider = CompositeProvider.__new__(CompositeProvider)
    provider._cache = _QuoteCache(ttl_seconds=30)
    provider._quote_provider = SimpleNamespace(
        get_latest_quote=mock.MagicMock(side_effect=AssertionError("REST should not be called"))
    )

    from settings import settings as live_settings
    monkeypatch.setattr(live_settings, "MARKET_DATA_WS_ENABLED", True, raising=False)

    result = provider.get_latest_quote("AAPL")
    assert result.price == 200.0
    assert result.source == "alpaca_ws"
    assert result.is_stale is False
