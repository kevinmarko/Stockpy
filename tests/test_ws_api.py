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


class TestBuildTickPayloadWsCache:
    def test_uses_ws_cache_when_fresh(self, monkeypatch):
        class _FakeStreamer:
            def get_quote(self, symbol):
                return {"bp": 100.0, "ap": 100.2}

        monkeypatch.setattr(ws_api, "_WS_AVAILABLE", True)
        monkeypatch.setattr(ws_api, "_WS_STREAMER", _FakeStreamer())

        payload = asyncio.run(ws_api._build_tick_payload("AAPL"))

        assert payload["source"] == "alpaca-ws"
        assert payload["bid"] == 100.0
        assert payload["ask"] == 100.2
        assert payload["is_stale"] is False


class TestBuildTickPayloadRestFallback:
    def _no_ws_cache(self, monkeypatch):
        monkeypatch.setattr(ws_api, "_WS_AVAILABLE", False)
        monkeypatch.setattr(ws_api, "_WS_STREAMER", None)

    def test_reuses_provider_singleton_not_a_fresh_instance(self, monkeypatch):
        """The fix: get_provider() (the shared singleton) is called, never a
        freshly constructed CompositeProvider() that would carry its own
        cold TTL cache on every tick."""
        self._no_ws_cache(monkeypatch)

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
        self._no_ws_cache(monkeypatch)

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
        self._no_ws_cache(monkeypatch)

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
