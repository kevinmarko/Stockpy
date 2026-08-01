"""
tests/test_data_api_chat.py
============================
Tests for api/data_api.py::chat_endpoint (POST /api/chat).

Covers three fixes:
- SSE frames use real newlines (\n\n), not the literal two-character
  sequence "\\n\\n" -- the frontend parses with buffer.split('\n').
- The endpoint is gated by _require_ai_generation_enabled
  (settings.AI_GENERATION_API_ENABLED), matching the /data/ai/* generation
  endpoints -- this API is fail-open by design when STATE_API_TOKEN is
  unset, so the capability flag is the only thing stopping this
  paid-LLM-calling endpoint from being remotely triggerable.
- An exception during generation never leaks its raw string (CodeQL:
  information exposure through an exception) to the SSE stream -- only a
  generic message, with full detail logged server-side instead.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

client = TestClient(data_api.app, client=("127.0.0.1", 54125))


@pytest.fixture(autouse=True)
def _enabled_and_no_provider(monkeypatch):
    """Default fixture state for every test: capability enabled, no LLM
    provider configured (hits the 'neither key configured' branch, which
    needs no real network access)."""
    monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)


def _post_chat():
    return client.stream("POST", "/api/chat", json={"message": "hi", "history": []})


class TestCapabilityGate:
    def test_403_when_ai_generation_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", False)
        resp = client.post("/api/chat", json={"message": "hi", "history": []})
        assert resp.status_code == 403
        assert "AI_GENERATION_API_ENABLED" in resp.json()["detail"]

    def test_200_when_ai_generation_enabled(self):
        with _post_chat() as resp:
            assert resp.status_code == 200


class TestSSEFormat:
    def test_frames_use_real_newlines(self):
        with _post_chat() as resp:
            raw = b"".join(resp.iter_bytes()).decode()

        assert "\\n\\n" not in raw, "must never emit the literal two-char sequence backslash-n"
        frames = [f for f in raw.split("\n\n") if f.strip()]
        assert len(frames) >= 3
        for frame in frames:
            assert frame.startswith("data: ")

    def test_frontend_parser_recovers_every_message(self):
        """Replays the exact frontend parsing contract: buffer.split('\n'),
        pop the trailing partial line, parse 'data: ' lines as JSON."""
        with _post_chat() as resp:
            raw = b"".join(resp.iter_bytes()).decode()

        lines = raw.split("\n")
        buffer_tail = lines.pop()  # matches buffer = lines.pop() || ''
        assert buffer_tail == ""

        parsed = []
        for line in lines:
            if line.startswith("data: "):
                data_str = line[len("data: "):].strip()
                if data_str and data_str != "[DONE]":
                    parsed.append(json.loads(data_str))

        types = [p["type"] for p in parsed]
        assert "THOUGHT" in types
        assert "MESSAGE" in types
        assert "SUGGESTION" in types


class TestExceptionSanitization:
    def test_provider_exception_does_not_leak_raw_message(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")

        class _FakePart:
            @staticmethod
            def from_text(text):
                return {"text": text}

        class _FakeContent:
            def __init__(self, role, parts):
                self.role = role
                self.parts = parts

        class _FakeTypesModule:
            Content = _FakeContent
            Part = _FakePart

        class _FakeModels:
            @staticmethod
            def generate_content_stream(**kwargs):
                raise RuntimeError("internal detail: /secret/path/creds.json leaked")

        class _FakeClient:
            models = _FakeModels()

        class _FakeGenaiModule:
            @staticmethod
            def Client(api_key):
                return _FakeClient()

        import sys
        import types as _std_types
        fake_google_genai_pkg = _std_types.ModuleType("google.genai")
        fake_google_genai_pkg.Client = _FakeGenaiModule.Client
        fake_google_pkg = _std_types.ModuleType("google")
        fake_google_pkg.genai = fake_google_genai_pkg

        monkeypatch.setitem(sys.modules, "google", fake_google_pkg)
        monkeypatch.setitem(sys.modules, "google.genai", fake_google_genai_pkg)
        monkeypatch.setitem(sys.modules, "google.genai.types", _FakeTypesModule)

        with _post_chat() as resp:
            raw = b"".join(resp.iter_bytes()).decode()

        assert "/secret/path/creds.json" not in raw
        assert "internal detail" not in raw
        assert "something went wrong generating a response" in raw
