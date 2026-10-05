"""
tests/test_ws_api.py
=====================
Tests for api/ws_api.py::_build_tick_payload (the REST-fallback tick builder
ws_tick_endpoint's loop calls every 500 ms).

Covers the fix from constructing a fresh data.market_data.CompositeProvider()
on every call (silently defeating MARKET_DATA_QUOTE_TTL_SECONDS and
re-hitting the underlying network provider on every tick) and calling its
synchronous get_latest_quote() directly on the event loop (blocking every
other connected client's socket for the duration of a slow/cold-cache call)
to reusing the data.market_data module singleton (get_provider()) and
offloading the blocking call to the executor.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import api.ws_api as ws_api


class _FakeQuote:
    def __init__(self, price=192.34, bid=192.30, ask=192.38, source="yfinance", is_stale=False):
        self.price = price
        self.bid = bid
        self.ask = ask
        self.source = source
        self.is_stale = is_stale


class TestTickSourceIsTheQuoteProvider:
    """The Alpaca WebSocket cache (source "alpaca-ws") was removed 2026-09-30:
    every tick now comes from the REST quote provider, and its ``source`` is
    that provider's own name (e.g. "fmp"/"yfinance")."""

    def test_source_is_the_provider_name(self, monkeypatch):
        class _Provider:
            def get_latest_quote(self, symbol):
                return _FakeQuote(source="fmp")

        class _FakeMarketData:
            @staticmethod
            def get_provider():
                return _Provider()

        import sys
        monkeypatch.setitem(sys.modules, "data.market_data", _FakeMarketData)

        payload = asyncio.run(ws_api._build_tick_payload("AAPL"))

        assert payload["source"] == "fmp"
        assert payload["price"] == 192.34

    def test_ws_streamer_globals_are_gone(self):
        assert not hasattr(ws_api, "_WS_STREAMER")
        assert not hasattr(ws_api, "_WS_AVAILABLE")


class TestBuildTickPayloadRestFallback:
    def test_reuses_provider_singleton_not_a_fresh_instance(self, monkeypatch):
        """The fix: get_provider() (the shared singleton) is called, never a
        freshly constructed CompositeProvider() that would carry its own
        cold TTL cache on every tick."""
        call_count = {"n": 0}

        class _Provider:
            def get_latest_quote(self, symbol):
                return _FakeQuote()

        provider = _Provider()

        class _FakeMarketData:
            @staticmethod
            def get_provider():
                call_count["n"] += 1
                return provider

        import sys
        monkeypatch.setitem(sys.modules, "data.market_data", _FakeMarketData)

        payload1 = asyncio.run(ws_api._build_tick_payload("AAPL"))
        payload2 = asyncio.run(ws_api._build_tick_payload("AAPL"))

        assert call_count["n"] == 2, "get_provider() called once per tick, as expected"
        assert payload1["price"] == 192.34
        assert payload2["price"] == 192.34

    def test_blocking_quote_call_does_not_block_event_loop(self, monkeypatch):
        """get_latest_quote() is a synchronous call; it must be offloaded to
        the executor so a slow call doesn't stall other coroutines sharing
        this event loop (every other connected client's socket)."""
        class _SlowProvider:
            def get_latest_quote(self, symbol):
                time.sleep(0.1)  # simulate a slow synchronous network call
                return _FakeQuote()

        class _FakeMarketData:
            @staticmethod
            def get_provider():
                return _SlowProvider()

        import sys
        monkeypatch.setitem(sys.modules, "data.market_data", _FakeMarketData)

        progress: list[int] = []

        async def other_coro():
            for i in range(5):
                progress.append(i)
                await asyncio.sleep(0.02)

        async def main():
            other = asyncio.create_task(other_coro())
            payload = await ws_api._build_tick_payload("AAPL")
            await asyncio.sleep(0.05)
            return payload

        payload = asyncio.run(main())
        assert payload["price"] == 192.34
        assert len(progress) >= 2, f"event loop was blocked -- other coroutine only ran {len(progress)} times"

    def test_quote_failure_degrades_to_unavailable(self, monkeypatch):
        class _FailingProvider:
            def get_latest_quote(self, symbol):
                raise RuntimeError("no network")

        class _FakeMarketData:
            @staticmethod
            def get_provider():
                return _FailingProvider()

        import sys
        monkeypatch.setitem(sys.modules, "data.market_data", _FakeMarketData)

        payload = asyncio.run(ws_api._build_tick_payload("AAPL"))
        assert payload == {"symbol": "AAPL", "error": "quote unavailable"}


class _FakeWebSocket:
    """Hand-rolled stand-in for fastapi.WebSocket -- NOT a MagicMock, per this
    repo's stated testing convention against mocks that don't model async
    semantics correctly (a bare MagicMock's methods aren't awaitable)."""

    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.closed_code: int | None = None

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent.append(data)

    async def close(self, code: int = 1000):
        self.closed_code = code


class TestTrainingStatusManager:
    def test_broadcast_reaches_all_connected_clients(self):
        manager = ws_api.TrainingStatusManager()
        ws1 = _FakeWebSocket()
        ws2 = _FakeWebSocket()

        async def scenario():
            await manager.connect(ws1)
            await manager.connect(ws2)
            await manager.broadcast("hello")

        asyncio.run(scenario())

        assert ws1.accepted is True
        assert ws2.accepted is True
        assert ws1.sent == ["hello"]
        assert ws2.sent == ["hello"]

    def test_disconnected_client_no_longer_receives_broadcasts(self):
        manager = ws_api.TrainingStatusManager()
        ws1 = _FakeWebSocket()
        ws2 = _FakeWebSocket()

        async def scenario():
            await manager.connect(ws1)
            await manager.connect(ws2)
            await manager.broadcast("first")
            await manager.disconnect(ws1)
            await manager.broadcast("second")

        asyncio.run(scenario())

        assert ws1.sent == ["first"]
        assert ws2.sent == ["first", "second"]
        assert ws1 not in manager.active_connections
        assert ws2 in manager.active_connections

    def test_broadcast_to_a_socket_that_raises_removes_it(self):
        """send_text failing (e.g. the socket dropped without a clean
        disconnect) must not raise out of broadcast() and must self-heal by
        removing the dead connection."""
        manager = ws_api.TrainingStatusManager()
        good = _FakeWebSocket()

        class _RaisingWebSocket(_FakeWebSocket):
            async def send_text(self, data: str):
                raise RuntimeError("connection reset")

        bad = _RaisingWebSocket()

        async def scenario():
            await manager.connect(good)
            await manager.connect(bad)
            await manager.broadcast("msg")

        asyncio.run(scenario())

        assert good.sent == ["msg"]
        assert bad not in manager.active_connections
        assert good in manager.active_connections


class TestBroadcastTrainingStatusThreadsafe:
    def test_no_op_when_main_loop_never_captured(self, monkeypatch):
        """The module's default state (set_main_loop never called, e.g. this
        module imported standalone in a test with no running app / no
        startup event ever fired) must never raise."""
        monkeypatch.setattr(ws_api, "_MAIN_LOOP", None)

        ws_api.broadcast_training_status_threadsafe("no loop yet")  # must not raise

    def test_schedules_broadcast_onto_captured_loop(self, monkeypatch):
        async def _runner():
            loop = asyncio.get_running_loop()
            monkeypatch.setattr(ws_api, "_MAIN_LOOP", loop)

            manager = ws_api.TrainingStatusManager()
            ws = _FakeWebSocket()
            await manager.connect(ws)
            monkeypatch.setattr(ws_api, "training_status_manager", manager)

            ws_api.broadcast_training_status_threadsafe("scheduled")
            # run_coroutine_threadsafe only schedules the broadcast; give the
            # loop repeated turns (bounded) to actually execute it before
            # asserting, rather than a single fixed sleep that could flake
            # under load.
            for _ in range(50):
                if ws.sent:
                    break
                await asyncio.sleep(0.01)
            return ws.sent

        sent = asyncio.run(_runner())
        assert sent == ["scheduled"]


class TestRouterSplit:
    """Pins the tick_router / training_router split (route-bleed fix):
    api/data_api.py must mount tick_router only, api/control_api.py must
    mount training_router only. A previous version of this module exposed a
    single shared ``ws_router`` carrying both routes, which any importer had
    to mount in full -- silently giving the Control API's daemon process
    live tick-streaming and giving the Data API a permanently-dead copy of
    /ws/training/status (its broadcast singletons are only ever populated by
    control_api.py). See api/ws_api.py's module docstring."""

    def _paths(self, router) -> set[str]:
        return {route.path for route in router.routes}

    def test_tick_router_carries_only_the_ticks_route(self):
        assert self._paths(ws_api.tick_router) == {"/ws/ticks/{symbol}"}

    def test_training_router_carries_only_the_training_status_route(self):
        assert self._paths(ws_api.training_router) == {"/ws/training/status"}

    def test_the_two_routers_are_distinct_objects_with_no_overlap(self):
        assert ws_api.tick_router is not ws_api.training_router
        assert self._paths(ws_api.tick_router).isdisjoint(
            self._paths(ws_api.training_router)
        )


class TestAuthRejectionIsVisibleToTheBrowser:
    """An unauthenticated connection must be accepted and then closed with
    4003, not closed before accept. A pre-accept close becomes an HTTP 403 on
    the handshake, which a browser reports only as close code 1006 -- the PWA
    could not tell "rejected" from "server down" and retried several times a
    second, flooding the log (2026-10-02). Built on a bare FastAPI app with
    just these routers so neither the Data API nor the Control API needs to
    be imported."""

    @pytest.fixture()
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setattr(ws_api.settings, "STATE_API_TOKEN", "secret-token-123")
        app = FastAPI()
        app.include_router(ws_api.tick_router)
        app.include_router(ws_api.training_router)
        return TestClient(app, client=("127.0.0.1", 54321))

    @pytest.mark.parametrize("path", ["/ws/ticks/AAPL", "/ws/training/status"])
    @pytest.mark.parametrize("query", ["", "?token=wrong-token"])
    def test_rejection_reaches_client_as_close_4003(self, client, path, query):
        from starlette.websockets import WebSocketDisconnect

        with client.websocket_connect(path + query) as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
        assert exc_info.value.code == ws_api.WS_AUTH_REJECTED_CODE == 4003

    def test_rejected_tick_connection_never_receives_a_quote(self, client, monkeypatch):
        called = []

        async def _fake_payload(sym):
            called.append(sym)
            return {"symbol": sym}

        monkeypatch.setattr(ws_api, "_build_tick_payload", _fake_payload)
        from starlette.websockets import WebSocketDisconnect

        with client.websocket_connect("/ws/ticks/AAPL?token=wrong") as ws:
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        assert called == []

    def test_valid_token_is_still_accepted(self, client):
        with client.websocket_connect("/ws/training/status?token=secret-token-123") as ws:
            assert ws is not None
        assert not ws_api.training_status_manager.active_connections
