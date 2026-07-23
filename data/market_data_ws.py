"""
data/market_data_ws.py
=======================
Opt-in real-time quote ingestion via Alpaca's ``StockDataStream`` WebSocket,
SUPPLEMENTING (never replacing) the REST-polling ``CompositeProvider`` in
``data/market_data.py``.

Gated behind ``settings.MARKET_DATA_WS_ENABLED`` (default False). When
disabled, or when the active quote provider isn't Alpaca, or when Alpaca
credentials are absent, ``start_market_data_ws_thread()`` is a no-op (logged,
never raised) and every consumer falls straight through to the existing REST
path — the pipeline is completely unaffected either way (CONSTRAINT #6 style).

Design mirrors ``execution/alpaca_broker.py``'s ``stream_trade_updates``
exactly: a bounded ring buffer absorbs backpressure, the blocking SDK
``stream.run()`` call runs in a thread-pool executor, and a supervisor loop
reconnects with exponential backoff on any disconnect. The one structural
difference: that method is an async generator driven by an already-running
event loop (the daemon's own asyncio task); this module owns its OWN event
loop on a dedicated background thread, because market-data ingestion has no
natural "host" event loop to attach to outside the daemon process.

This module is a pure DATA-INGESTION concern -- it does not read, gate, or
duplicate any order/risk-gate/kill-switch logic.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Bounded ring buffer for the handler thread -> async consumer handoff.
# Same rationale as alpaca_broker.py's _STREAM_BUFFER_MAXLEN: deque append/
# popleft are atomic under the GIL, so no lock is required for the handoff
# itself (the quote STORE below is a separate structure that IS locked, since
# it's read concurrently from arbitrary CompositeProvider caller threads).
_STREAM_BUFFER_MAXLEN = 2000
_STREAM_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class _WSQuote:
    """Minimal quote payload captured off the WebSocket -- deliberately not
    ``data.market_data.Quote`` (that dataclass requires an ``is_stale``/
    ``source`` framing computed by the REST providers); this is translated
    into a real ``Quote`` only at read time in ``get_ws_quote``."""

    symbol: str
    price: float
    bid: float
    ask: float
    timestamp: datetime


class _WSQuoteStore:
    """Thread-safe in-process quote store, never persisted to disk.

    Written by the WS handler thread, read by arbitrary caller threads via
    ``data.market_data.CompositeProvider.get_latest_quote``. A quote older
    than ``stale_seconds`` is treated as absent -- a stale WS quote must
    never masquerade as fresh (the caller falls through to REST instead).
    """

    def __init__(self, stale_seconds: int = 10) -> None:
        self._stale_seconds = max(1, int(stale_seconds))
        self._store: Dict[str, _WSQuote] = {}
        self._lock = threading.Lock()

    def put(self, quote: _WSQuote) -> None:
        with self._lock:
            self._store[quote.symbol] = quote

    def get(self, symbol: str) -> Optional[_WSQuote]:
        with self._lock:
            q = self._store.get(symbol.upper())
        if q is None:
            return None
        age = (datetime.now(timezone.utc) - q.timestamp).total_seconds()
        if age > self._stale_seconds:
            return None
        return q

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Module-level singleton -- CompositeProvider instances (constructed in many
# contexts: CLI one-shots, tests, GUI) read from this store; only the daemon
# process's start_market_data_ws_thread() ever writes to it.
_quote_store: Optional[_WSQuoteStore] = None


def get_ws_quote(symbol: str) -> Optional[_WSQuote]:
    """Return the freshest WS-delivered quote for ``symbol``, or ``None`` when
    no WS ingestion is running, the symbol was never subscribed, or the last
    update is older than the configured staleness threshold."""
    if _quote_store is None:
        return None
    return _quote_store.get(symbol)


class AlpacaQuoteStreamer:
    """Owns one ``alpaca.data.live.StockDataStream`` connection for a fixed
    symbol set, writing every received quote into the module-level
    ``_WSQuoteStore``. Never raises out of ``run_forever`` -- any failure to
    construct/subscribe/run the stream is logged and retried with backoff.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        symbols: List[str],
        store: _WSQuoteStore,
        reconnect_base_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._symbols = [s.upper() for s in symbols]
        self._store = store
        self._reconnect_base = reconnect_base_seconds
        self._reconnect_max = reconnect_max_seconds
        self._stop_event: Optional["object"] = None  # set to asyncio.Event() in run_forever

    @staticmethod
    def _normalize_quote(data) -> Optional[_WSQuote]:
        try:
            symbol = str(getattr(data, "symbol", "")).upper()
            if not symbol:
                return None
            bid = float(getattr(data, "bid_price", None) or float("nan"))
            ask = float(getattr(data, "ask_price", None) or float("nan"))
            price = (
                (bid + ask) / 2
                if (bid == bid and ask == ask)  # both non-NaN
                else (bid if bid == bid else ask)
            )
            ts = getattr(data, "timestamp", None)
            ts_utc = (
                ts.astimezone(timezone.utc)
                if ts is not None and ts.tzinfo is not None
                else (ts.replace(tzinfo=timezone.utc) if ts is not None else datetime.now(timezone.utc))
            )
            return _WSQuote(symbol=symbol, price=price, bid=bid, ask=ask, timestamp=ts_utc)
        except Exception as exc:  # noqa: BLE001 - never let a malformed tick crash the stream
            logger.debug("market_data_ws: failed to normalize quote payload: %s", exc)
            return None

    async def run_forever(self) -> None:
        """Blocks until cancelled. Supervises the underlying WS connection
        with exponential-backoff reconnect, exactly mirroring
        ``execution/alpaca_broker.py``'s ``stream_trade_updates`` shape."""
        import asyncio
        from alpaca.data.live import StockDataStream

        if not self._symbols:
            logger.info("market_data_ws: no symbols to subscribe -- WS ingestion is a no-op.")
            return

        buffer: "deque" = deque(maxlen=_STREAM_BUFFER_MAXLEN)

        async def _handler(data) -> None:
            q = self._normalize_quote(data)
            if q is None:
                return
            if len(buffer) == buffer.maxlen:
                logger.warning(
                    "market_data_ws: quote buffer full (maxlen=%d) -- "
                    "evicting oldest (consumer falling behind)",
                    buffer.maxlen,
                )
            buffer.append(q)

        loop = asyncio.get_event_loop()
        stream = None
        stream_task = None
        backoff = self._reconnect_base

        def _spawn_stream():
            nonlocal stream, stream_task, backoff
            stream = StockDataStream(api_key=self._api_key, secret_key=self._secret_key)
            stream.subscribe_quotes(_handler, *self._symbols)
            stream_task = loop.run_in_executor(None, stream.run)
            backoff = self._reconnect_base
            logger.info(
                "market_data_ws: quote stream connected (%d symbols)", len(self._symbols)
            )

        _spawn_stream()

        try:
            while True:
                await asyncio.sleep(_STREAM_POLL_SECONDS)

                while buffer:
                    self._store.put(buffer.popleft())

                if stream_task is not None and stream_task.done():
                    exc = stream_task.exception()
                    if exc is not None:
                        logger.critical(
                            "market_data_ws: quote stream errored (%s) -- reconnecting in %.1fs",
                            exc, backoff,
                        )
                    else:
                        logger.warning(
                            "market_data_ws: quote stream closed -- reconnecting in %.1fs", backoff
                        )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._reconnect_max)
                    try:
                        if stream is not None:
                            stream.stop()
                    except Exception:
                        pass
                    _spawn_stream()
        except asyncio.CancelledError:
            logger.info("market_data_ws: quote stream cancelled")
            try:
                if stream is not None:
                    stream.stop()
                if stream_task is not None:
                    await stream_task
            except Exception:
                pass
            return


def _resolve_symbols(explicit: Optional[List[str]]) -> List[str]:
    """Symbol resolution for the WS subscription. Explicit list wins; else
    ``settings.MARKET_DATA_WS_SYMBOLS`` (comma-separated); else the
    ``WATCHLIST`` env var (same convention ``main.py`` reads); else empty
    (no-op, logged -- never raises)."""
    if explicit:
        return [s.strip().upper() for s in explicit if s.strip()]

    try:
        from settings import settings as _settings
        configured = getattr(_settings, "MARKET_DATA_WS_SYMBOLS", None)
    except Exception:
        configured = None
    if configured:
        return [s.strip().upper() for s in str(configured).split(",") if s.strip()]

    import os
    watchlist = os.environ.get("WATCHLIST", "")
    if watchlist:
        return [s.strip().upper() for s in watchlist.split(",") if s.strip()]

    return []


def start_market_data_ws_thread(symbols: Optional[List[str]] = None) -> Optional[threading.Thread]:
    """Start the WS ingestion thread, or return ``None`` (logged, never
    raised) when disabled, credentials are missing, the active provider
    isn't Alpaca, or no symbols resolve.

    Intended to be called ONCE by the long-lived daemon process
    (``desktop/orchestrator_daemon.py``), not by every ``CompositeProvider``
    construction site -- a short-lived CLI/test/GUI process spawning this
    thread would leak it.
    """
    global _quote_store

    try:
        from settings import settings as _settings
        enabled = bool(getattr(_settings, "MARKET_DATA_WS_ENABLED", False))
    except Exception as exc:  # noqa: BLE001
        logger.debug("market_data_ws: settings read failed (%s) -- WS disabled.", exc)
        return None
    if not enabled:
        return None

    try:
        from data.market_data import get_provider, AlpacaProvider
        provider = get_provider()
        if not isinstance(getattr(provider, "_quote_provider", None), AlpacaProvider):
            logger.info(
                "market_data_ws: active quote provider is not Alpaca -- WS ingestion is a no-op "
                "(no legitimate real-time feed for the delayed yfinance path)."
            )
            return None
        quote_provider = provider._quote_provider  # AlpacaProvider instance, already holds creds
        api_key = quote_provider._api_key
        secret_key = quote_provider._secret_key
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_data_ws: could not resolve Alpaca credentials (%s) -- skipping WS.", exc)
        return None

    resolved_symbols = _resolve_symbols(symbols)
    if not resolved_symbols:
        logger.info("market_data_ws: no symbols resolved (MARKET_DATA_WS_SYMBOLS/WATCHLIST empty) -- skipping WS.")
        return None

    stale_seconds = int(getattr(_settings, "MARKET_DATA_WS_STALE_SECONDS", 10))
    reconnect_base = float(getattr(_settings, "MARKET_DATA_WS_RECONNECT_BASE_SECONDS", 1.0))
    reconnect_max = float(getattr(_settings, "MARKET_DATA_WS_RECONNECT_MAX_SECONDS", 30.0))

    _quote_store = _WSQuoteStore(stale_seconds=stale_seconds)
    streamer = AlpacaQuoteStreamer(
        api_key=api_key,
        secret_key=secret_key,
        symbols=resolved_symbols,
        store=_quote_store,
        reconnect_base_seconds=reconnect_base,
        reconnect_max_seconds=reconnect_max,
    )

    def _run():
        import asyncio
        try:
            asyncio.run(streamer.run_forever())
        except Exception as exc:  # noqa: BLE001 - thread must never crash the process
            logger.error("market_data_ws: streamer thread terminated unexpectedly: %s", exc, exc_info=True)

    thread = threading.Thread(target=_run, name="market-data-ws", daemon=True)
    thread.start()
    logger.info("market_data_ws: WebSocket ingestion thread started for %d symbols.", len(resolved_symbols))
    return thread
