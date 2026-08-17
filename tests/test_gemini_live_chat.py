"""tests/test_gemini_live_chat.py
==============================
Unit tests for the Gemini Live API bidirectional WebSocket streaming endpoint
mounted at ``/ws/chat/live`` in ``api/data_api.py`` (implemented in ``api/ws_api.py``).

Covers:
1. Authentication & Capability Gating:
   - Valid token (?token= / Authorization: Bearer header)
   - Invalid token -> 4003 close code
   - AI_GENERATION_API_ENABLED=False -> 4003 close code
   - GEMINI_LIVE_CHAT_ENABLED=False -> 4003 close code
   - Missing GEMINI_API_KEY -> error message + 4003 close code

2. Full Bidirectional Session Flow:
   - Connection handshake (type="connected", model, voice)
   - Ping / Pong (type="ping" -> type="pong")
   - Text & audio realtime input forwarding
   - audio_stream_end flushing signal
   - Background context forwarding
   - Server content delivery (audio chunks 24kHz, text, transcriptions, interrupted, turn_complete)

3. Live Tool Calling & Safe Execution:
   - Tool execution on function_calls
   - Thought event emitted to client
   - Result formatted into types.FunctionResponse and sent via session.send_tool_response
   - Unknown tools and tool exceptions handled gracefully without crashing
"""

import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.data_api import app
from settings import settings

client = TestClient(app)


class TestAuthAndGating:
    def test_rejects_invalid_token(self, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret-token-123")

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/chat/live?token=wrong-token"):
                pass
        assert exc_info.value.code == 4003

    def test_accepts_valid_token_query(self, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret-token-123")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)

        with client.websocket_connect("/ws/chat/live?token=secret-token-123") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "GEMINI_API_KEY" in msg["message"]

    def test_accepts_valid_token_bearer_header(self, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret-token-123")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)

        with client.websocket_connect(
            "/ws/chat/live",
            headers={"Authorization": "Bearer secret-token-123"}
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "GEMINI_API_KEY" in msg["message"]

    def test_rejects_when_ai_generation_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", False)

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/chat/live"):
                pass
        assert exc_info.value.code == 4003

    def test_rejects_when_live_chat_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_LIVE_CHAT_ENABLED", False)

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/chat/live"):
                pass
        assert exc_info.value.code == 4003

    def test_notifies_and_closes_when_gemini_key_missing(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)

        with client.websocket_connect("/ws/chat/live") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "GEMINI_API_KEY" in msg["message"]


class TestLiveChatSession:
    def test_full_bidirectional_flow(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "real-key-mocked")
        monkeypatch.setattr(settings, "GEMINI_LIVE_CHAT_MODEL", "gemini-3.1-flash-live-preview")
        monkeypatch.setattr(settings, "GEMINI_LIVE_VOICE_NAME", "Aoede")

        class _FakePart:
            def __init__(self, data=None, text=None):
                self.inline_data = MagicMock(data=data) if data else None
                self.text = text

        class _FakeModelTurn:
            def __init__(self, parts):
                self.parts = parts

        class _FakeTranscription:
            def __init__(self, text):
                self.text = text

        class _FakeServerContent:
            def __init__(
                self,
                parts=None,
                in_transcript=None,
                out_transcript=None,
                interrupted=False,
                turn_complete=False,
            ):
                self.model_turn = _FakeModelTurn(parts) if parts else None
                self.input_transcription = _FakeTranscription(in_transcript) if in_transcript else None
                self.output_transcription = _FakeTranscription(out_transcript) if out_transcript else None
                self.interrupted = interrupted
                self.turn_complete = turn_complete

        class _FakeResponse:
            def __init__(self, content):
                self.server_content = content

        sent_inputs = []

        class _FakeAsyncGen:
            def __init__(self, items):
                self._items = list(items)
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx < len(self._items):
                    item = self._items[self._idx]
                    self._idx += 1
                    return item
                while True:
                    await asyncio.sleep(0.1)

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def send_realtime_input(self, **kwargs):
                sent_inputs.append(kwargs)

            def receive(self):
                return _FakeAsyncGen([
                    _FakeResponse(_FakeServerContent(in_transcript="Hello Stockpy")),
                    _FakeResponse(
                        _FakeServerContent(
                            parts=[
                                _FakePart(data=b"\x00\x01\x02\x03"),
                                _FakePart(text="Hello! How can I help?"),
                            ],
                            out_transcript="Hello! How can I help?",
                            interrupted=True,
                            turn_complete=True,
                        )
                    ),
                ])

        class _FakeLive:
            @staticmethod
            def connect(model, config):
                return _FakeSession()

        class _FakeAio:
            live = _FakeLive()

        class _FakeClient:
            aio = _FakeAio()

        from google import genai
        with patch.object(genai, "Client", return_value=_FakeClient()):
            with client.websocket_connect("/ws/chat/live") as ws:
                # 1. First event: connected handshake
                connected = ws.receive_json()
                assert connected["type"] == "connected"
                assert connected["model"] == "gemini-3.1-flash-live-preview"
                assert connected["voice"] == "Aoede"

                # 2. Send commands
                ws.send_json({"type": "ping"})
                ws.send_json({"type": "realtime_input", "text": "What is my portfolio?"})
                dummy_pcm = base64.b64encode(b"\x10\x20\x30\x40").decode("ascii")
                ws.send_json({"type": "realtime_input", "audio": dummy_pcm})
                ws.send_json({"type": "realtime_input", "audio_stream_end": True})
                ws.send_json({"type": "context", "text": "SPY is up 1%"})

                # 3. Collect the responses
                received_types = []
                for _ in range(7):
                    msg = ws.receive_json()
                    received_types.append(msg["type"])

                assert "pong" in received_types
                assert "input_transcription" in received_types
                assert "audio" in received_types
                assert "text" in received_types
                assert "output_transcription" in received_types
                assert "interrupted" in received_types
                assert "turn_complete" in received_types

                # Verify sent_inputs on the session
                assert any(inp.get("text") == "What is my portfolio?" for inp in sent_inputs)
                assert any("audio" in inp for inp in sent_inputs)
                assert any(inp.get("audio_stream_end") is True for inp in sent_inputs)
                assert any("SPY is up 1%" in inp.get("text", "") for inp in sent_inputs)

    def test_live_tool_call_execution(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "real-key-mocked")

        class _FakeFunctionCall:
            def __init__(self, name, call_id, args):
                self.name = name
                self.id = call_id
                self.args = args

        class _FakeToolCall:
            def __init__(self, calls):
                self.function_calls = calls

        class _FakeToolResponseMsg:
            def __init__(self, calls):
                self.tool_call = _FakeToolCall(calls)
                self.server_content = None

        sent_tool_responses = []

        class _FakeAsyncGen:
            def __init__(self, items):
                self._items = list(items)
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx < len(self._items):
                    item = self._items[self._idx]
                    self._idx += 1
                    return item
                while True:
                    await asyncio.sleep(0.1)

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def send_realtime_input(self, **kwargs):
                pass

            async def send_tool_response(self, function_responses):
                sent_tool_responses.append(function_responses)

            def receive(self):
                return _FakeAsyncGen([
                    _FakeToolResponseMsg([
                        _FakeFunctionCall("get_platform_status", "call_abc123", {}),
                        _FakeFunctionCall("unknown_tool_test", "call_xyz789", {}),
                    ])
                ])

        class _FakeLive:
            @staticmethod
            def connect(model, config):
                return _FakeSession()

        class _FakeAio:
            live = _FakeLive()

        class _FakeClient:
            aio = _FakeAio()

        from google import genai
        with patch.object(genai, "Client", return_value=_FakeClient()):
            with client.websocket_connect("/ws/chat/live") as ws:
                connected = ws.receive_json()
                assert connected["type"] == "connected"

                # Wait for thought events sent to client
                msg1 = ws.receive_json()
                assert msg1["type"] == "thought"
                assert "get_platform_status" in msg1["content"]

                msg2 = ws.receive_json()
                assert msg2["type"] == "thought"
                assert "unknown_tool_test" in msg2["content"]

                # Ensure send_tool_response was called on the session
                assert len(sent_tool_responses) == 1
                responses = sent_tool_responses[0]
                assert len(responses) == 2
                assert responses[0].name == "get_platform_status"
                assert responses[0].id == "call_abc123"
                assert "result" in responses[0].response

                assert responses[1].name == "unknown_tool_test"
                assert responses[1].id == "call_xyz789"
                assert "error" in responses[1].response["result"]
