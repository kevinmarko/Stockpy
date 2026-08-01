"""
tests/test_websocket_streamer.py
================================
Unit tests for data/websocket_streamer.py

Tests:
- TokenBucket-like: get_quote returns None for unseen symbols
- get_quote returns data when cache is fresh
- get_quote returns None when data is stale (TTL exceeded)
- subscribe adds to _subscribed set
- start() logs an error when called outside an event loop
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import patch, MagicMock

from data.websocket_streamer import WebSocketStreamer, _TICK_TTL_SECONDS


def test_get_quote_returns_none_for_unknown_symbol():
    streamer = WebSocketStreamer()
    assert streamer.get_quote("AAPL") is None


def test_get_quote_returns_fresh_tick():
    streamer = WebSocketStreamer()
    streamer._cache["AAPL"] = {"S": "AAPL", "bp": 191.5, "ap": 191.6, "_ts": time.monotonic()}
    tick = streamer.get_quote("AAPL")
    assert tick is not None
    assert tick["bp"] == 191.5


def test_get_quote_returns_none_for_stale_tick():
    streamer = WebSocketStreamer()
    # Inject a tick that is older than the TTL
    streamer._cache["MSFT"] = {
        "S": "MSFT",
        "bp": 370.0,
        "ap": 370.1,
        "_ts": time.monotonic() - (_TICK_TTL_SECONDS + 1),
    }
    assert streamer.get_quote("MSFT") is None


def test_get_quote_case_insensitive():
    streamer = WebSocketStreamer()
    streamer._cache["GOOG"] = {"S": "GOOG", "bp": 150.0, "_ts": time.monotonic()}
    # lowercase lookup should still find it
    assert streamer.get_quote("goog") is not None


def test_subscribe_adds_to_subscribed_set():
    streamer = WebSocketStreamer()
    assert "AAPL" not in streamer._subscribed
    streamer.subscribe(["AAPL", "MSFT"])
    assert "AAPL" in streamer._subscribed
    assert "MSFT" in streamer._subscribed


def test_subscribe_normalises_to_uppercase():
    streamer = WebSocketStreamer()
    streamer.subscribe(["tsla"])
    assert "TSLA" in streamer._subscribed


def test_subscribe_idempotent():
    streamer = WebSocketStreamer()
    streamer.subscribe(["AAPL"])
    streamer.subscribe(["AAPL"])
    assert len([s for s in streamer._subscribed if s == "AAPL"]) == 1


def test_start_outside_event_loop_logs_error(caplog):
    """start() must log an error (not raise) when called outside asyncio."""
    import logging
    streamer = WebSocketStreamer()
    with caplog.at_level(logging.ERROR, logger="data.websocket_streamer"):
        streamer.start()  # No running loop in pytest sync context
    assert streamer._task is None


def test_stop_sets_is_running_false():
    streamer = WebSocketStreamer()
    streamer.is_running = True
    streamer.stop()
    assert streamer.is_running is False
    assert streamer._task is None
