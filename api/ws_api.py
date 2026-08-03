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


async def _build_tick_payload(sym_upper: str) -> dict:
    """Build one tick JSON payload for *sym_upper* (WS cache, else REST fallback).

    Extracted from ws_tick_endpoint's loop body so the REST-fallback path
    (provider reuse + executor offload) is directly unit-testable without
    driving a real WebSocket connection.
    """
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
        return {
            "symbol": sym_upper,
            "price": price,
            "bid": bid,
            "ask": ask,
            "source": "alpaca-ws",
            "is_stale": False,
        }

    # 2. REST fallback via the market_data module singleton. get_provider()
    # (not a fresh CompositeProvider()) so this reuses the provider's own
    # in-process quote TTL cache across ticks/clients instead of
    # constructing a brand-new, cold cache on every 500 ms iteration -- a
    # fresh CompositeProvider() re-creates that cache every call, silently
    # defeating MARKET_DATA_QUOTE_TTL_SECONDS entirely and re-hitting the
    # underlying network provider on every single tick. get_latest_quote()
    # is itself a synchronous/blocking call (yfinance/alpaca-py's REST
    # clients), so it's additionally offloaded to the executor -- otherwise
    # a slow or cold-cache call would block the whole event loop (every
    # other connected client's socket) for its duration.
    try:
        from data.market_data import get_provider
        provider = get_provider()
        loop = asyncio.get_running_loop()
        q = await loop.run_in_executor(None, provider.get_latest_quote, sym_upper)
        return {
            "symbol": sym_upper,
            "price": _sanitize(q.price),
            "bid": _sanitize(q.bid),
            "ask": _sanitize(q.ask),
            "source": q.source,
            "is_stale": q.is_stale,
        }
    except Exception as rest_exc:
        logger.warning("ws_tick REST fallback failed for %s: %s", sym_upper, rest_exc)
        return {"symbol": sym_upper, "error": "quote unavailable"}


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
            payload = await _build_tick_payload(sym_upper)
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


@ws_router.websocket("/ws/jobs/{job_id}/stream")
async def ws_job_stream_endpoint(
    websocket: WebSocket,
    job_id: str,
    token: Optional[str] = Query(default=None),
):
    """Stream live logs for a job over WebSocket.
    
    Auth is checked similarly to ws_tick_endpoint via query token or auth header.
    """
    auth_header = websocket.headers.get("authorization")
    if not _check_ws_token(token, auth_header):
        await websocket.close(code=4003)
        logger.warning("ws_job_stream_endpoint: rejected unauthenticated connection for job %s", job_id)
        return

    from api._jobs import job_manager
    from api._redact import redact_line
    import time as _time

    rec = job_manager.get_job(job_id)
    if not rec:
        await websocket.close(code=4004)
        logger.warning("ws_job_stream_endpoint: job %s not found", job_id)
        return

    await websocket.accept()
    logger.info("ws_job_stream_endpoint: client connected for job %s", job_id)

    log_path = rec.handle.log_path
    current_offset = 0
    last_sent = _time.monotonic()
    
    try:
        while True:
            sent_any = False
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(current_offset)
                    lines = f.readlines()
                    if lines:
                        for line in lines:
                            scrubbed = redact_line(line.rstrip("\n"))
                            await websocket.send_text(json.dumps({
                                "id": current_offset,
                                "data": scrubbed
                            }))
                        current_offset = f.tell()
                        sent_any = True

            if not rec.handle.is_running():
                await websocket.send_text(json.dumps({
                    "event": "end",
                    "data": f"Job completed with exit code {rec.exit_code()}"
                }))
                break

            now = _time.monotonic()
            if sent_any:
                last_sent = now
            elif now - last_sent >= 15.0:
                await websocket.send_text(json.dumps({"event": "heartbeat"}))
                last_sent = now

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info("ws_job_stream_endpoint: client disconnected from job %s", job_id)
    except Exception as exc:
        logger.error("ws_job_stream_endpoint error for job %s: %s", job_id, exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

class TrainingStatusManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.lock = asyncio.Lock()
        
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.active_connections.append(websocket)
            
    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
                
    async def broadcast(self, message: str):
        async with self.lock:
            for connection in self.active_connections[:]:
                try:
                    await connection.send_text(message)
                except Exception:
                    if connection in self.active_connections:
                        self.active_connections.remove(connection)

training_status_manager = TrainingStatusManager()

@ws_router.websocket("/ws/training/status")
async def ws_training_status_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    """Stream live training status events."""
    auth_header = websocket.headers.get("authorization")
    if not _check_ws_token(token, auth_header):
        await websocket.close(code=4003)
        return
        
    await training_status_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await training_status_manager.disconnect(websocket)
    except Exception as exc:
        logger.error("ws_training_status error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
        await training_status_manager.disconnect(websocket)
