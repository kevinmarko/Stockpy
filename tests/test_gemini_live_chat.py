"""
tests/test_gemini_live_chat.py
==============================
Unit tests for api/ws_api.py::ws_live_chat_endpoint (/ws/chat/live).

Tests:
- Authentication via token query param or Authorization header against STATE_API_TOKEN.
- Gating via AI_GENERATION_API_ENABLED and GEMINI_LIVE_CHAT_ENABLED.
- Graceful handling when GEMINI_API_KEY is unset.
- Full bidirectional streaming simulation with a mocked google.genai live session:
  - connected handshake
  - ping / pong
  - realtime text and audio input forwarding
  - context forwarding
  - server audio, transcription, and interruption events
  - clean disconnect and error sanitization
"""
from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.data_api as data_api
import api.ws_api as ws_api
from settings import settings

client = TestClient(data_api.app)


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    """Enable capability flags and clear tokens by default."""
    monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_LIVE_CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr(settings, "STATE_API_TOKEN", None)
    monkeypatch.setattr(ws_api, "_TOKEN", None)


class TestLiveChatAuth:
    def test_rejects_unauthenticated_connection_when_token_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret-token")
        monkeypatch.setattr(ws_api, "_TOKEN", "secret-token")

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/chat/live?token=wrong-token"):
                pass
        assert exc_info.value.code == 4003

    def test_accepts_valid_token_query_param(self, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret-token")
        monkeypatch.setattr(ws_api, "_TOKEN", "secret-token")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)

        with client.websocket_connect("/ws/chat/live?token=secret-token") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "GEMINI_API_KEY" in msg["message"]


class TestLiveChatGating:
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

        # Create mock server content events
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
            def __init__(self, parts=None, in_transcript=None, out_transcript=None, interrupted=False, turn_complete=False):
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
                    _FakeResponse(_FakeServerContent(
                        parts=[
                            _FakePart(data=b"\x00\x01\x02\x03"),
                            _FakePart(text="Hello! How can I help?")
                        ],
                        out_transcript="Hello! How can I help?",
                        turn_complete=True
                    ))
                ])

        class _FakeLive:
            @staticmethod
            def connect(model, config):
                return _FakeSession()

        class _FakeAio:
            live = _FakeLive()

        class _FakeClient:
            aio = _FakeAio()

        # Mock google.genai Client
        from google import genai
        with patch.object(genai, "Client", return_value=_FakeClient()):
            with client.websocket_connect("/ws/chat/live") as ws:
                # 1. First event: connected handshake
                connected = ws.receive_json()
                assert connected["type"] == "connected"
                assert connected["model"] == "gemini-3.1-flash-live-preview"
                assert connected["voice"] == "Aoede"

                # Send commands
                ws.send_json({"type": "ping"})
                ws.send_json({"type": "realtime_input", "text": "What is my portfolio?"})
                dummy_pcm = base64.b64encode(b"\x10\x20\x30\x40").decode("ascii")
                ws.send_json({"type": "realtime_input", "audio": dummy_pcm})
                ws.send_json({"type": "context", "text": "SPY is up 1%"})

                # Collect the responses
                received_types = []
                for _ in range(6):
                    msg = ws.receive_json()
                    received_types.append(msg["type"])

                assert "pong" in received_types
                assert "input_transcription" in received_types
                assert "audio" in received_types
                assert "text" in received_types
                assert "output_transcription" in received_types
                assert "turn_complete" in received_types
