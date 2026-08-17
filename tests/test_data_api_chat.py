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

Also covers the optional `context` field on ChatMessageRequest (added to
support the Options Matrix screen's "Ask Gemini" button threading a
client-built summary of the currently displayed options directives into the
prompt -- see webapp/src/chat/formatOptionsContext.ts): omitted/empty is
byte-identical to pre-existing behavior, and when present it's threaded into
both the Gemini `contents` list (as a leading Content turn) and the
Anthropic `system` parameter (chosen specifically because Anthropic's
Messages API requires strictly alternating user/assistant turns, so a
leading "user" context turn would collide with a from-scratch conversation
whose first history entry is also role "user").

Also covers a direct, isolated regression test for `_iter_blocking` (see
`TestIterBlocking` below): it bridges a synchronous iterator into an async
generator via `run_in_executor(None, next, it)`. A bare `next(it)` raises
`StopIteration` when `it` is exhausted, and asyncio's `Future` machinery
cannot propagate a `StopIteration` through `run_in_executor` (PEP 479 --
`asyncio.Future.set_exception()` explicitly refuses one), so the `await`
hangs forever instead of completing -- exactly the case an empty/exhausted
iterator hits, exactly what `TestContextField`'s fake SDK modules return
(they only need to inspect call kwargs, not emit real chunks). This was a
real, confirmed, previously-shipped bug (see docs/test_coverage_analysis.md
Phase 6) fixed by using `next(it, SENTINEL)` instead of bare `next(it)`.
`TestIterBlocking` exercises the fixed function directly, in well under a
second, rather than relying on `TestContextField`'s full HTTP-round-trip
tests (which only catch a regression here via a 180s pytest-timeout hang).
"""
from __future__ import annotations

import asyncio
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


def _fake_google_genai_module(monkeypatch, captured):
    """Installs a fake `google.genai` (+ `.types`) module pair into
    sys.modules so `from google import genai` / `from google.genai import
    types` inside chat_endpoint resolve to test doubles, and records the
    kwargs passed to `generate_content_stream` into `captured['kwargs']`.
    Mirrors TestExceptionSanitization's fake-module technique above."""
    import sys
    import types as _std_types

    class _FakePart:
        @staticmethod
        def from_text(text):
            return {"text": text}

    class _FakeContent:
        def __init__(self, role, parts):
            self.role = role
            self.parts = parts

    class _FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeTypesModule:
        Content = _FakeContent
        Part = _FakePart
        GenerateContentConfig = _FakeGenerateContentConfig

    class _FakeModels:
        @staticmethod
        def generate_content_stream(**kwargs):
            captured["kwargs"] = kwargs
            return iter([])  # no chunks -- test only inspects the call args

    class _FakeClient:
        models = _FakeModels()

    class _FakeGenaiModule:
        @staticmethod
        def Client(api_key):
            return _FakeClient()

    fake_google_genai_pkg = _std_types.ModuleType("google.genai")
    fake_google_genai_pkg.Client = _FakeGenaiModule.Client
    fake_google_pkg = _std_types.ModuleType("google")
    fake_google_pkg.genai = fake_google_genai_pkg

    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake_google_genai_pkg)
    monkeypatch.setitem(sys.modules, "google.genai.types", _FakeTypesModule)


def _fake_anthropic_module(monkeypatch, captured):
    """Installs a fake `anthropic` module into sys.modules recording the
    kwargs passed to `client.messages.stream(...)` into
    `captured['kwargs']`."""
    import sys
    import types as _std_types

    class _FakeStreamCM:
        def __enter__(self):
            self.text_stream = iter([])
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeMessages:
        @staticmethod
        def stream(**kwargs):
            captured["kwargs"] = kwargs
            return _FakeStreamCM()

    class _FakeAnthropicClient:
        messages = _FakeMessages()

    fake_anthropic_module = _std_types.ModuleType("anthropic")
    fake_anthropic_module.Anthropic = lambda api_key: _FakeAnthropicClient()

    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_module)


class TestContextField:
    """Covers the new optional ChatMessageRequest.context field."""

    def test_omitted_context_is_byte_identical_to_no_context_field(self):
        """A request with no `context` key at all and a request with an
        explicit `context: None` must stream identical SSE output -- proves
        the new field is purely additive with no behavior change for every
        existing caller that doesn't know about it yet."""
        with client.stream("POST", "/api/chat", json={"message": "hi", "history": []}) as resp_a:
            raw_a = b"".join(resp_a.iter_bytes())
        with client.stream(
            "POST", "/api/chat", json={"message": "hi", "history": [], "context": None}
        ) as resp_b:
            raw_b = b"".join(resp_b.iter_bytes())

        assert raw_a == raw_b

    def test_gemini_branch_threads_context_as_leading_content_turn(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
        captured: dict = {}
        _fake_google_genai_module(monkeypatch, captured)

        context_text = "AAPL: AltmanZ=3.10, daysToEarnings=5, earningsRisk=yes"
        with client.stream(
            "POST",
            "/api/chat",
            json={"message": "which of these have earnings risk?", "history": [], "context": context_text},
        ) as resp:
            b"".join(resp.iter_bytes())

        contents = captured["kwargs"]["contents"]
        assert len(contents) >= 1
        leading = contents[0]
        assert leading.role == "user"
        assert leading.parts[0]["text"] == f"Context:\n{context_text}"
        # The real query still goes in as its own, final turn.
        assert contents[-1].parts[0]["text"] == "which of these have earnings risk?"

    def test_gemini_branch_omits_leading_turn_when_context_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
        captured: dict = {}
        _fake_google_genai_module(monkeypatch, captured)

        with client.stream(
            "POST", "/api/chat", json={"message": "hi", "history": []}
        ) as resp:
            b"".join(resp.iter_bytes())

        contents = captured["kwargs"]["contents"]
        assert len(contents) == 1
        assert contents[0].parts[0]["text"] == "hi"

    def test_anthropic_branch_threads_context_as_system_param(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "fake-key")
        captured: dict = {}
        _fake_anthropic_module(monkeypatch, captured)

        context_text = "AAPL: AltmanZ=3.10, daysToEarnings=5, earningsRisk=yes"
        with client.stream(
            "POST",
            "/api/chat",
            json={"message": "which of these have earnings risk?", "history": [], "context": context_text},
        ) as resp:
            b"".join(resp.iter_bytes())

        assert captured["kwargs"].get("system") == f"Context:\n{context_text}"
        # Roles still strictly alternate -- no extra leading "user" turn was
        # spliced into the messages list itself.
        messages = captured["kwargs"]["messages"]
        assert [m["role"] for m in messages] == ["user"]

    def test_anthropic_branch_omits_system_param_when_context_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "fake-key")
        captured: dict = {}
        _fake_anthropic_module(monkeypatch, captured)

        with client.stream(
            "POST", "/api/chat", json={"message": "hi", "history": []}
        ) as resp:
            b"".join(resp.iter_bytes())

        assert "system" not in captured["kwargs"]


class TestIterBlocking:
    """Direct, isolated regression coverage for `_iter_blocking` -- see the
    module docstring for the full StopIteration/run_in_executor hazard this
    guards against. Every test is wrapped in `asyncio.wait_for` with a short
    deadline so a reintroduced hang fails fast (a real assertion failure)
    instead of hanging the whole test run for pytest-timeout's much longer
    deadline to eventually kill."""

    @staticmethod
    async def _collect(sync_iterable):
        return [item async for item in data_api._iter_blocking(sync_iterable)]

    def test_empty_iterator_completes_without_hanging(self):
        result = asyncio.run(asyncio.wait_for(self._collect(iter([])), timeout=5))
        assert result == []

    def test_non_empty_iterator_yields_every_item_in_order(self):
        result = asyncio.run(asyncio.wait_for(self._collect(iter([1, 2, 3])), timeout=5))
        assert result == [1, 2, 3]

    def test_single_item_iterator(self):
        result = asyncio.run(asyncio.wait_for(self._collect(iter(["only"])), timeout=5))
        assert result == ["only"]

    def test_generator_input_not_just_list_iterator(self):
        """The real callers pass an SDK stream object, not necessarily a
        plain list -- confirms _iter_blocking works against any iterable,
        not specifically `iter([...])`."""
        def gen():
            yield "a"
            yield "b"

        result = asyncio.run(asyncio.wait_for(self._collect(gen()), timeout=5))
        assert result == ["a", "b"]


class TestAiModelsEndpoint:
    """Tests for GET /data/ai/models listing supported providers and models."""

    def test_get_ai_models_returns_providers_and_defaults(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-gemini-key")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")

        with TestClient(data_api.app, client=("127.0.0.1", 54125)) as test_client:
            resp = test_client.get("/data/ai/models")
            assert resp.status_code == 200
            data = resp.json()
            assert "providers" in data
            assert "default_provider" in data

            providers_by_id = {p["id"]: p for p in data["providers"]}
            assert "gemini" in providers_by_id
            assert providers_by_id["gemini"]["available"] is True
            assert "anthropic" in providers_by_id
            assert providers_by_id["anthropic"]["available"] is False
            assert "openai" in providers_by_id
            assert providers_by_id["openai"]["available"] is False
            assert "local" in providers_by_id
            assert providers_by_id["local"]["available"] is True


class TestMultiProviderRouting:
    """Tests for multi-provider routing (OpenAI, Local LLM, Claude, Gemini)."""

    def test_openai_routing_invokes_openai_client(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", True)
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-openai-key")

        captured = {}

        class _FakeDelta:
            content = "Hello from OpenAI"

        class _FakeChoice:
            delta = _FakeDelta()

        class _FakeChunk:
            choices = [_FakeChoice()]

        async def _fake_stream():
            yield _FakeChunk()

        class _FakeCompletions:
            async def create(self, **kwargs):
                captured["kwargs"] = kwargs
                return _fake_stream()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeAsyncOpenAI:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs
                self.chat = _FakeChat()

        monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)

        with TestClient(data_api.app, client=("127.0.0.1", 54125)) as test_client:
            resp = test_client.post(
                "/api/chat",
                json={"message": "Hello", "provider": "openai", "model": "o3-mini", "context": "Test context"},
            )
            raw = resp.text
            assert "Hello from OpenAI" in raw
            assert captured["kwargs"]["model"] == "o3-mini"
            assert any(m["role"] == "system" and "Test context" in m["content"] for m in captured["kwargs"]["messages"])

    def test_local_routing_uses_configured_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", True)
        monkeypatch.setattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")

        captured = {}

        class _FakeDelta:
            content = "Hello from DeepSeek"

        class _FakeChoice:
            delta = _FakeDelta()

        class _FakeChunk:
            choices = [_FakeChoice()]

        async def _fake_stream():
            yield _FakeChunk()

        class _FakeCompletions:
            async def create(self, **kwargs):
                captured["kwargs"] = kwargs
                return _fake_stream()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeAsyncOpenAI:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs
                self.chat = _FakeChat()

        monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)

        with TestClient(data_api.app, client=("127.0.0.1", 54125)) as test_client:
            resp = test_client.post(
                "/api/chat",
                json={
                    "message": "Analyze stock",
                    "provider": "local",
                    "model": "deepseek-r1",
                },
            )
            raw = resp.text
            assert "Hello from DeepSeek" in raw
            assert captured["client_kwargs"]["base_url"] == "http://localhost:11434/v1"
            assert captured["kwargs"]["model"] == "deepseek-r1"

    def test_local_routing_ignores_client_supplied_base_url(self, monkeypatch):
        """Security regression test: a request body cannot redirect the
        "local" provider's outbound call to an arbitrary host. Sending a
        (no-longer-part-of-the-schema) "custom_base_url" field must be
        silently dropped by pydantic, not honored -- otherwise this
        endpoint is an open SSRF / LOCAL_LLM_API_KEY-relay to any URL an
        unauthenticated-or-loopback caller supplies (see the comment on
        ChatMessageRequest.provider/model in api/data_api.py)."""
        monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", True)
        monkeypatch.setattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(settings, "LOCAL_LLM_API_KEY", "super-secret-local-key")

        captured = {}

        class _FakeDelta:
            content = "Hello"

        class _FakeChoice:
            delta = _FakeDelta()

        class _FakeChunk:
            choices = [_FakeChoice()]

        async def _fake_stream():
            yield _FakeChunk()

        class _FakeCompletions:
            async def create(self, **kwargs):
                captured["kwargs"] = kwargs
                return _fake_stream()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeAsyncOpenAI:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs
                self.chat = _FakeChat()

        monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)

        with TestClient(data_api.app, client=("127.0.0.1", 54125)) as test_client:
            resp = test_client.post(
                "/api/chat",
                json={
                    "message": "Analyze stock",
                    "provider": "local",
                    "model": "deepseek-r1",
                    # An attacker-supplied field trying to redirect the
                    # outbound call and exfiltrate LOCAL_LLM_API_KEY.
                    "custom_base_url": "https://attacker.example/v1",
                },
            )
            raw = resp.text
            assert "Hello" in raw
            # The attacker's URL must never be reached -- only the
            # operator's own configured LOCAL_LLM_BASE_URL is ever used.
            assert captured["client_kwargs"]["base_url"] == "http://localhost:11434/v1"
            assert captured["client_kwargs"]["base_url"] != "https://attacker.example/v1"
