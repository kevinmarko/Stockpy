"""
data/websocket_streamer.py
==========================
Persistent asyncio WebSocket streamer for high-frequency telemetry.
Routes tick updates directly into memory caches to bypass REST polling
latency.

Integration: ``AlpacaProvider.get_latest_quote()`` calls
``_STREAMER.get_quote(symbol)`` first; only falls back to the REST
``/v2/stocks/{sym}/quotes/latest`` endpoint when no fresh WS tick is
available (or the streamer is not running).

``_STREAMER`` is a module-level singleton created at import time.
``start_streamer()`` and ``stop_streamer()`` are called by the FastAPI
lifespan context in ``api/data_api.py`` (or by ``main_orchestrator.py``
when it owns the event loop).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import websockets
from settings import settings

logger = logging.getLogger(__name__)

# Max age of a cached tick before it is considered stale (seconds)
_TICK_TTL_SECONDS = 2.0


class WebSocketStreamer:
    """Persistent asyncio WebSocket streamer with exponential-backoff reconnect.

    Subscriptions are accumulated in ``_subscribed`` and flushed to the broker
    on every (re)connect so symbols subscribed before the first connect are not
    lost.
    """

    def __init__(self, feed_url: str = "wss://stream.data.alpaca.markets/v2/iex"):
        self.feed_url = feed_url
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        # symbol → {raw event dict, "_ts": monotonic timestamp}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._subscribed: set[str] = set()
        # Symbols already flushed to the broker on the CURRENT connection —
        # reset to empty on every (re)connect so a fresh connect's initial
        # flush re-sends everything, and subscribe() only needs to send the
        # delta for symbols added after that.
        self._flushed_on_current_connection: set[str] = set()
        # The live connection, set while _stream_loop holds one open. Lets
        # subscribe() push a delta subscribe message immediately instead of
        # only updating _subscribed and waiting for the next reconnect.
        self._ws: Optional[Any] = None

    # ------------------------------------------------------------------
    # Public API used by AlpacaProvider
    # ------------------------------------------------------------------

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return the most recent tick for *symbol* if it is fresh, else None.

        A tick is considered stale after ``_TICK_TTL_SECONDS``.  Callers must
        fall back to REST when this returns ``None``.
        """
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        age = time.monotonic() - entry.get("_ts", 0.0)
        if age > _TICK_TTL_SECONDS:
            return None
        return entry

    def subscribe(self, symbols: list[str]) -> None:
        """Register *symbols* for real-time streaming.

        Safe to call before ``start()`` — the subscription list is replayed on
        every (re)connect so no messages are lost. If a connection is already
        open, newly-added symbols are also flushed to the broker immediately
        (as a delta subscribe message) rather than waiting for the next
        reconnect, which could otherwise be an unbounded wait on a healthy
        connection.
        """
        for sym in symbols:
            self._subscribed.add(sym.upper())
        if self._task and not self._task.done():
            # Schedule the delta flush on the streamer's own loop. subscribe()
            # may be called from a different thread/loop than _stream_loop
            # runs on, so this must go through call_soon_threadsafe rather
            # than assuming the caller's running loop is the right one.
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._schedule_subscribe_flush)
            except RuntimeError:
                pass  # No running loop; subscriptions will be sent on next connect

    def _schedule_subscribe_flush(self) -> None:
        """Create a task to send any not-yet-flushed subscriptions over the live connection."""
        if self._ws is not None:
            asyncio.create_task(self._flush_new_subscriptions())

    async def _flush_new_subscriptions(self) -> None:
        """Send a delta subscribe message for symbols added since the last flush on this connection."""
        if self._ws is None:
            return
        new_symbols = self._subscribed - self._flushed_on_current_connection
        if not new_symbols:
            return
        try:
            await self._ws.send(json.dumps({
                "action": "subscribe",
                "quotes": sorted(new_symbols),
                "trades": sorted(new_symbols),
            }))
            self._flushed_on_current_connection |= new_symbols
            logger.info("WS delta-subscribed to %d new symbol(s)", len(new_symbols))
        except Exception as exc:
            logger.warning("WS delta subscribe failed (will retry on next reconnect): %s", exc)

    # ------------------------------------------------------------------
    # Internal streaming loop
    # ------------------------------------------------------------------

    async def _stream_loop(self):
        """Persistent connection loop with exponential-backoff reconnect."""
        self.is_running = True
        backoff = 1.0

        while self.is_running:
            try:
                async with websockets.connect(self.feed_url) as ws:
                    logger.info("WebSocket connected to %s", self.feed_url)

                    # Authenticate with Alpaca
                    if "alpaca" in self.feed_url and settings.ALPACA_API_KEY:
                        await ws.send(json.dumps({
                            "action": "auth",
                            "key": settings.ALPACA_API_KEY,
                            "secret": settings.ALPACA_SECRET_KEY,
                        }))
                        auth_resp = await ws.recv()
                        logger.info("WS auth response: %s", auth_resp)

                    # Flush any accumulated subscriptions
                    self._flushed_on_current_connection = set()
                    if self._subscribed:
                        await ws.send(json.dumps({
                            "action": "subscribe",
                            "quotes": sorted(self._subscribed),
                            "trades": sorted(self._subscribed),
                        }))
                        self._flushed_on_current_connection = set(self._subscribed)
                        logger.info("WS subscribed to %d symbols", len(self._subscribed))

                    # Publish the live connection so subscribe() can push
                    # delta subscribes without waiting for a reconnect.
                    self._ws = ws
                    backoff = 1.0  # reset on successful connect

                    async for message in ws:
                        if not self.is_running:
                            break
                        try:
                            data = json.loads(message)
                            if isinstance(data, list):
                                ts = time.monotonic()
                                for event in data:
                                    sym = event.get("S", "").upper()
                                    if sym:
                                        entry = self._cache.setdefault(sym, {})
                                        entry.update(event)
                                        entry["_ts"] = ts
                        except Exception as parse_exc:
                            logger.error("WS parse error: %s", parse_exc)

            except Exception as exc:
                logger.warning(
                    "WS disconnected: %s. Reconnecting in %.1fs …", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            finally:
                # Whether this iteration ended cleanly (is_running went False)
                # or via an exception, the connection this iteration held is
                # no longer live — clear it so subscribe()'s delta-flush path
                # doesn't try to send on a closed/stale websocket.
                self._ws = None

    # ------------------------------------------------------------------
    # Lifecycle (called by FastAPI lifespan or orchestrator)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background stream task inside the running event loop."""
        if self._task and not self._task.done():
            return  # already running
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._stream_loop())
            logger.info("WebSocketStreamer background task started.")
        except RuntimeError:
            logger.error(
                "WebSocketStreamer.start() called outside an asyncio event loop. "
                "Call start() from within an async context (FastAPI lifespan or "
                "asyncio.run())."
            )

    def stop(self) -> None:
        """Gracefully stop the background task."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("WebSocketStreamer stopped.")


# ---------------------------------------------------------------------------
# Module-level singleton — imported by AlpacaProvider and api/ws_api.py
# ---------------------------------------------------------------------------

# True whenever this module itself imported successfully (the `websockets`
# package is a hard top-level import above, so reaching this line already
# proves it's installed). Exported so callers — api/ws_api.py, data/market_data.py
# — can gate on it the same way market_data.py's own try/except-derived flag
# already does, without needing to re-derive "is streaming available" themselves.
_WS_AVAILABLE: bool = True

_STREAMER: WebSocketStreamer = WebSocketStreamer()


def start_streamer() -> None:
    """Start the module-level singleton streamer (called from lifespan)."""
    _STREAMER.start()


def stop_streamer() -> None:
    """Stop the module-level singleton streamer (called from lifespan)."""
    _STREAMER.stop()
