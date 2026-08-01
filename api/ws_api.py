"""
api/ws_api.py
=============
FastAPI WebSocket endpoint for live tick streaming.

``GET /ws/ticks/{symbol}``  — upgrades to a WebSocket and pushes tick
updates from the ``WebSocketStreamer`` singleton every 500 ms while the
client is connected.  Falls back gracefully to polling the REST quote if
the streamer has no fresh tick.

Mount: ``app.include_router(ws_router)`` in data_api.py (already done for
the main FastAPI app).

Auth: The endpoint accepts either a ``?token=<STATE_API_TOKEN>`` query
parameter or an ``Authorization: Bearer <token>`` header, matching the
same gate used on HTTP endpoints, but adapted for the WS upgrade
handshake (headers are read from the initial HTTP upgrade request).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from data.websocket_streamer import _STREAMER as _WS_STREAMER, _WS_AVAILABLE
from settings import settings

logger = logging.getLogger(__name__)

ws_router = APIRouter()

_TOKEN: Optional[str] = getattr(settings, "STATE_API_TOKEN", None)


def _check_ws_token(token: Optional[str], auth_header: Optional[str]) -> bool:
    """Return True if the caller supplied the correct API token.

    Accepts token via query-param or Authorization: Bearer header.
    If no token is configured on the server side, all connections are allowed
    (loopback-only dev mode).
    """
    if not _TOKEN:
        return True  # no token configured — open access (loopback dev)
    if token and token == _TOKEN:
        return True
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):] == _TOKEN
    return False


def _sanitize(value) -> float | None:
    """Convert a value to float, returning None for NaN/Inf (never fabricated)."""
    try:
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


@ws_router.websocket("/ws/ticks/{symbol}")
async def ws_tick_endpoint(
    websocket: WebSocket,
    symbol: str,
    token: Optional[str] = Query(default=None),
):
    """Stream live bid/ask/price ticks for *symbol* every 500 ms.

    Message format (JSON):
    ::

        {
            "symbol": "AAPL",
            "price": 192.34,
            "bid":   192.30,
            "ask":   192.38,
            "source": "alpaca-ws",   // or "rest-fallback"
            "is_stale": false
        }

    The connection is closed with 4003 if the auth token is invalid.
    """
    auth_header = websocket.headers.get("authorization")
    if not _check_ws_token(token, auth_header):
        await websocket.close(code=4003)
        logger.warning("ws_tick_endpoint: rejected unauthenticated connection for %s", symbol)
        return

    await websocket.accept()
    sym_upper = symbol.upper()
    logger.info("ws_tick_endpoint: client connected for %s", sym_upper)

    # Ensure the symbol is subscribed to the streamer
    if _WS_AVAILABLE and _WS_STREAMER is not None:
        _WS_STREAMER.subscribe([sym_upper])

    try:
        while True:
            tick = None

            # 1. Try the live WS cache
            if _WS_AVAILABLE and _WS_STREAMER is not None:
                tick = _WS_STREAMER.get_quote(sym_upper)

            if tick is not None:
                bid = _sanitize(tick.get("bp"))
                ask = _sanitize(tick.get("ap"))
                price = (
                    ((bid or 0) + (ask or 0)) / 2
                    if bid is not None and ask is not None
                    else (bid or ask)
                )
                payload = {
                    "symbol": sym_upper,
                    "price": price,
                    "bid": bid,
                    "ask": ask,
                    "source": "alpaca-ws",
                    "is_stale": False,
                }
            else:
                # 2. REST fallback via CompositeProvider
                try:
                    from data.market_data import CompositeProvider
                    provider = CompositeProvider()
                    q = provider.get_latest_quote(sym_upper)
                    payload = {
                        "symbol": sym_upper,
                        "price": _sanitize(q.price),
                        "bid": _sanitize(q.bid),
                        "ask": _sanitize(q.ask),
                        "source": q.source,
                        "is_stale": q.is_stale,
                    }
                except Exception as rest_exc:
                    logger.warning("ws_tick REST fallback failed for %s: %s", sym_upper, rest_exc)
                    payload = {"symbol": sym_upper, "error": "quote unavailable"}

            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(0.5)  # 2 Hz push cadence

    except WebSocketDisconnect:
        logger.info("ws_tick_endpoint: client disconnected from %s", sym_upper)
    except Exception as exc:
        logger.error("ws_tick_endpoint error for %s: %s", sym_upper, exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
