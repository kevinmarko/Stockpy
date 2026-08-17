"""
api/ws_api.py
=============
FastAPI WebSocket endpoints, split into two independent routers so that
mounting one in a given process's app never drags the other's route along
with it:

``tick_router`` -- ``GET /ws/ticks/{symbol}``, live tick streaming from the
``WebSocketStreamer`` singleton every 500 ms while the client is connected
(falls back gracefully to polling the REST quote if the streamer has no
fresh tick). Mounted by ``api/data_api.py`` only.

``training_router`` -- ``GET /ws/training/status``, training-job
started/finished broadcasts (``TrainingStatusManager``). Mounted by
``api/control_api.py`` only -- that process is the one that actually runs
``POST /jobs`` and the ``train_lgbm``/``train_meta`` job types, so it's the
only process with anything real to broadcast. Mounting ``tick_router`` there
too would make the daemon process also unintentionally serve live
market-tick streaming, an unrelated capability with no test coverage in
that context; mounting ``training_router`` in ``data_api.py`` would expose a
route that can never broadcast anything there, since the
``training_status_manager``/``_MAIN_LOOP`` singletons this module owns are
only ever populated by ``control_api.py``'s own startup hook and
``create_job``/``stream_job_logs`` call sites.

Both routers share this module's helpers (``_check_ws_token``,
``_sanitize``) and the ``/ws/training/status``-adjacent broadcast plumbing
(``training_status_manager``, ``set_main_loop``,
``broadcast_training_status_threadsafe``) below.

Auth: every endpoint here accepts either a ``?token=<STATE_API_TOKEN>``
query parameter or an ``Authorization: Bearer <token>`` header, matching the
same gate used on HTTP endpoints, but adapted for the WS upgrade
handshake (headers are read from the initial HTTP upgrade request).
``_check_ws_token`` additionally FAILS CLOSED for a non-loopback caller when
no token is configured, mirroring ``api/auth.py::require_read_token``'s
posture for HTTP endpoints -- see that function's docstring and
``api.auth.is_loopback_host`` for why an unset token must never mean "open"
once an endpoint is reachable from outside this machine (e.g. via the
``scripts/Caddyfile`` reverse-proxy routes for ``/ws/ticks/*``/``/ws/chat/*``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from api.auth import is_loopback_host
from data.websocket_streamer import _STREAMER as _WS_STREAMER, _WS_AVAILABLE
from settings import settings

logger = logging.getLogger(__name__)

tick_router = APIRouter()
training_router = APIRouter()
live_chat_router = APIRouter()

def _check_ws_token(
    token: Optional[str], auth_header: Optional[str], client_host: Optional[str]
) -> bool:
    """Return True if the caller supplied the correct API token.

    Accepts token via query-param or Authorization: Bearer header. If no
    token is configured on the server side, connections are allowed ONLY
    from a loopback client (zero-config local dev/test) -- matching
    ``api/auth.py::require_read_token``'s HTTP posture exactly, rather than
    the earlier, looser "no token configured -> always open" rule this
    function used to apply regardless of where the connection came from.
    *client_host* is the WebSocket's ``.client.host`` (``None`` under some
    ASGI transports, treated as loopback -- see ``is_loopback_host``).
    """
    server_token = getattr(settings, "STATE_API_TOKEN", None)
    if not server_token:
        return is_loopback_host(client_host)
    if token and token == server_token:
        return True
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):] == server_token
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


@tick_router.websocket("/ws/ticks/{symbol}")
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
    client_host = websocket.client.host if websocket.client else None
    if not _check_ws_token(token, auth_header, client_host):
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


@training_router.websocket("/ws/training/status")
async def ws_training_status_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    """Stream live training-job status events (started/finished) to any
    connected client. Auth matches ws_tick_endpoint's convention -- a
    ?token= query param or Authorization: Bearer header, checked against
    the same STATE_API_TOKEN-derived gate."""
    auth_header = websocket.headers.get("authorization")
    client_host = websocket.client.host if websocket.client else None
    if not _check_ws_token(token, auth_header, client_host):
        await websocket.close(code=4003)
        logger.warning("ws_training_status_endpoint: rejected unauthenticated connection")
        return

    await training_status_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await training_status_manager.disconnect(websocket)
    except Exception as exc:
        logger.error("ws_training_status_endpoint error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
        await training_status_manager.disconnect(websocket)


_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from the hosting FastAPI app's startup event so that a
    SYNCHRONOUS route (which FastAPI runs in a threadpool with no event
    loop of its own) can still schedule a coroutine onto the real running
    loop. Without this, a sync route's asyncio.get_running_loop() call
    always raises RuntimeError."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def broadcast_training_status_threadsafe(message: str) -> None:
    """Fire-and-forget: schedule training_status_manager.broadcast(message)
    from a non-loop thread (a sync FastAPI route calls this directly).
    No-ops (logs at debug) if the loop was never captured -- e.g. this
    module imported standalone in a test with no running app / no startup
    event ever fired. Never raises."""
    if _MAIN_LOOP is None:
        logger.debug("broadcast_training_status_threadsafe: no loop captured yet")
        return
    try:
        asyncio.run_coroutine_threadsafe(
            training_status_manager.broadcast(message), _MAIN_LOOP
        )  # do not .result() -- fire-and-forget from a sync caller
    except Exception as exc:  # noqa: BLE001 - a broadcast must never crash the caller
        logger.warning("broadcast_training_status_threadsafe failed: %s", exc)


# ---------------------------------------------------------------------------
# Gemini Live WebSocket Endpoint (/ws/chat/live)
# Real-time bidirectional voice and text streaming over WebSockets.
# ---------------------------------------------------------------------------

LIVE_SYSTEM_INSTRUCTION = """You are Stockpy AI, the real-time voice and quant assistant for the Stockpy platform.
You are directly grounded in the user's trading pilots, current portfolio holdings, risk metrics, and macro regime state via read-only tools.
Answer questions directly, concisely, and conversationally."""


@live_chat_router.websocket("/ws/chat/live")
async def ws_live_chat_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    """Bidirectional WebSocket streaming endpoint for Gemini Live API.

    Enables low-latency real-time voice and text interaction with Gemini
    (gemini-3.1-flash-live-preview) grounded in platform tools and portfolio data.

    Auth & Gating:
    - Checked via _check_ws_token against STATE_API_TOKEN.
    - Gated by settings.AI_GENERATION_API_ENABLED and settings.GEMINI_LIVE_CHAT_ENABLED.
    - Requires settings.GEMINI_API_KEY.
    """
    auth_header = websocket.headers.get("authorization")
    client_host = websocket.client.host if websocket.client else None
    if not _check_ws_token(token, auth_header, client_host):
        await websocket.close(code=4003)
        logger.warning("ws_live_chat_endpoint: rejected unauthenticated connection")
        return

    if not getattr(settings, "AI_GENERATION_API_ENABLED", True) or not getattr(
        settings, "GEMINI_LIVE_CHAT_ENABLED", True
    ):
        await websocket.close(code=4003)
        logger.warning("ws_live_chat_endpoint: rejected because AI generation or Live Chat is disabled")
        return

    gemini_key = getattr(settings, "GEMINI_API_KEY", None)
    if not gemini_key:
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "GEMINI_API_KEY is not configured in settings."
        })
        await websocket.close(code=4003)
        return

    await websocket.accept()

    try:
        from google import genai
        from google.genai import types
        import base64

        # Lazy import grounding tools from data_api
        from api.data_api import _CHAT_TOOLS

        client = genai.Client(api_key=gemini_key)
        model_name = getattr(settings, "GEMINI_LIVE_CHAT_MODEL", "gemini-3.1-flash-live-preview")
        voice_name = getattr(settings, "GEMINI_LIVE_VOICE_NAME", "Aoede")

        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_name
                )
            )
        )

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=LIVE_SYSTEM_INSTRUCTION)]
            ),
            tools=_CHAT_TOOLS,
            speech_config=speech_config,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        tool_map = {getattr(fn, "__name__", str(fn)): fn for fn in _CHAT_TOOLS}

        await websocket.send_json({
            "type": "connected",
            "model": model_name,
            "voice": voice_name,
        })

        async with client.aio.live.connect(model=model_name, config=config) as session:
            # client_to_gemini (below) and gemini_to_client (further below)
            # run as two independently-scheduled asyncio tasks that BOTH
            # write to this same `websocket` (a ping's "pong" reply from the
            # former can race with the latter's audio/text/transcription/
            # tool-call stream). Starlette's WebSocket has no send-side
            # locking of its own, so two concurrent send_json() calls could
            # interleave partial writes on the wire; every outbound frame
            # goes through this one lock instead of calling
            # websocket.send_json directly.
            send_lock = asyncio.Lock()

            async def _send_json(payload: dict) -> None:
                async with send_lock:
                    await websocket.send_json(payload)

            async def client_to_gemini():
                try:
                    while True:
                        msg_text = await websocket.receive_text()
                        try:
                            data = json.loads(msg_text)
                            msg_type = data.get("type")
                            if msg_type == "ping":
                                await _send_json({"type": "pong"})
                            elif msg_type == "realtime_input":
                                audio_b64 = data.get("audio")
                                text_input = data.get("text")
                                audio_end = data.get("audio_stream_end")
                                if audio_b64:
                                    audio_bytes = base64.b64decode(audio_b64)
                                    await session.send_realtime_input(
                                        audio=types.Blob(
                                            data=audio_bytes,
                                            mime_type="audio/pcm;rate=16000"
                                        )
                                    )
                                elif text_input:
                                    await session.send_realtime_input(text=str(text_input))
                                elif audio_end:
                                    await session.send_realtime_input(audio_stream_end=True)
                            elif msg_type == "context":
                                ctx_text = data.get("text", "")
                                if ctx_text:
                                    await session.send_realtime_input(
                                        text=f"Background Context:\n{ctx_text}"
                                    )
                        except Exception as msg_err:
                            logger.warning("Error processing client live input message: %s", type(msg_err).__name__)
                            continue
                except WebSocketDisconnect:
                    pass
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("client_to_gemini loop terminated: %s", type(e).__name__)

            async def gemini_to_client():
                try:
                    async for response in session.receive():
                        # Handle live tool calls from Gemini
                        if getattr(response, "tool_call", None) and response.tool_call.function_calls:
                            function_responses = []
                            for call in response.tool_call.function_calls:
                                fn = tool_map.get(call.name)
                                await _send_json({
                                    "type": "thought",
                                    "content": f"Querying {call.name}..."
                                })
                                if fn:
                                    try:
                                        raw_args = call.args if isinstance(call.args, dict) else (dict(call.args) if call.args else {})
                                        result = await asyncio.to_thread(fn, **raw_args)
                                    except Exception as err:
                                        result = {"error": f"Tool execution error: {type(err).__name__}"}
                                else:
                                    result = {"error": f"Unknown tool: {call.name}"}

                                function_responses.append(
                                    types.FunctionResponse(
                                        name=call.name,
                                        id=call.id,
                                        response={"result": result}
                                    )
                                )
                            if function_responses:
                                await session.send_tool_response(function_responses=function_responses)

                        server_content = response.server_content
                        if server_content:
                            if server_content.model_turn:
                                for part in server_content.model_turn.parts:
                                    if part.inline_data:
                                        raw_audio = part.inline_data.data
                                        if isinstance(raw_audio, (bytes, bytearray)):
                                            audio_b64 = base64.b64encode(raw_audio).decode("ascii")
                                        else:
                                            audio_b64 = str(raw_audio)
                                        await _send_json({
                                            "type": "audio",
                                            "data": audio_b64,
                                            "mimeType": "audio/pcm;rate=24000"
                                        })
                                    if part.text:
                                        await _send_json({
                                            "type": "text",
                                            "content": part.text
                                        })
                            if server_content.input_transcription:
                                await _send_json({
                                    "type": "input_transcription",
                                    "text": server_content.input_transcription.text
                                })
                            if server_content.output_transcription:
                                await _send_json({
                                    "type": "output_transcription",
                                    "text": server_content.output_transcription.text
                                })
                            if getattr(server_content, "interrupted", False):
                                await _send_json({"type": "interrupted"})
                            if getattr(server_content, "turn_complete", False):
                                await _send_json({"type": "turn_complete"})
                except WebSocketDisconnect:
                    pass
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("gemini_to_client exception: %s", type(e).__name__)
                    try:
                        await _send_json({
                            "type": "error",
                            "message": "Live connection encountered an issue."
                        })
                    except Exception:
                        pass

            t1 = asyncio.create_task(client_to_gemini())
            t2 = asyncio.create_task(gemini_to_client())

            done, pending = await asyncio.wait(
                [t1, t2],
                return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("ws_live_chat_endpoint unexpected error: %s", exc, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Error connecting to Live API service."
            })
            await websocket.close(code=1011)
        except Exception:
            pass

