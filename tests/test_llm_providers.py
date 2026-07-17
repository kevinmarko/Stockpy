"""
tests/test_llm_providers.py
============================
Unit tests for ``llm.providers`` — Claude (Anthropic) + Gemini (Google) provider
abstractions.

All SDK calls are monkeypatched.  No real API requests are made.

Coverage
--------
TestSchemaSurface           — provider classes expose .name and a soft-fail call_structured.
TestClaudeProvider          — tool_use happy path; missing block; ValidationError;
                              network exception → None; bad payload → None.
TestGeminiProvider          — response.text JSON happy path; empty text → None;
                              ValidationError → None; network exception → None;
                              missing SDK → None.
TestSoftFailContract        — every provider's call_structured returns Optional
                              and NEVER propagates an exception (CONSTRAINT #6).
"""

from __future__ import annotations

import sys
import types
from typing import Any, List, Optional
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field, ValidationError

# ---------------------------------------------------------------------------
# Helpers — install fake `anthropic` and `google.genai` modules BEFORE
# importing llm.providers, so the lazy SDK imports inside the providers'
# __init__ succeed against the fake SDK in tests.
# ---------------------------------------------------------------------------


def _install_fake_anthropic() -> None:
    """Install a minimal fake `anthropic` package into sys.modules."""
    fake = types.ModuleType("anthropic")

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = MagicMock()

    fake.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    fake.APIError = Exception  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake


def _install_fake_google_genai() -> None:
    """Install fake `google.genai` + `google.genai.types` packages."""
    pkg_google = types.ModuleType("google")
    pkg_genai = types.ModuleType("google.genai")
    pkg_types = types.ModuleType("google.genai.types")

    class _FakeClient:
        def __init__(self, **kwargs):
            self.models = MagicMock()

    class _FakeHttpOptions:
        def __init__(self, timeout: int = 0):
            self.timeout = timeout

    class _FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    pkg_genai.Client = _FakeClient  # type: ignore[attr-defined]
    pkg_types.HttpOptions = _FakeHttpOptions  # type: ignore[attr-defined]
    pkg_types.GenerateContentConfig = _FakeGenerateContentConfig  # type: ignore[attr-defined]
    pkg_genai.types = pkg_types  # type: ignore[attr-defined]

    sys.modules["google"] = pkg_google
    sys.modules["google.genai"] = pkg_genai
    sys.modules["google.genai.types"] = pkg_types


@pytest.fixture(autouse=True)
def _fresh_fake_sdks():
    """Each test gets fresh fake SDK modules to avoid state leak.

    Only ``llm.providers`` is popped — popping ``llm.commentary`` /
    ``llm.router`` would orphan references captured at collection time in
    sibling test files (notably ``test_advisory_llm_enrichment.py``), so
    later monkeypatches there would land on the wrong module object.

    Teardown removes the fake ``anthropic`` and ``google.*`` entries from
    ``sys.modules`` so sibling test files (e.g. ``test_run_once.py``'s
    Google Sheets path) can import the REAL google-auth / gspread stack.
    Without this teardown a fake-google module persists across files and
    poisons every later import of the real package.
    """
    # Remember which keys we're injecting so teardown can roll them back
    # without touching anything pre-existing.
    _injected = ("anthropic", "google", "google.genai", "google.genai.types")
    _prior = {k: sys.modules.get(k) for k in _injected}

    _install_fake_anthropic()
    _install_fake_google_genai()
    # Force re-import of llm.providers so it picks up the fake SDKs.  Do NOT
    # touch llm.commentary or llm.router — see docstring above.
    sys.modules.pop("llm.providers", None)
    try:
        yield
    finally:
        # Restore prior sys.modules entries — for each fake we injected,
        # either drop it (if nothing was there before) or put the original
        # back.  This unblocks later test files that need the real packages.
        for k, prior in _prior.items():
            if prior is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = prior
        # Also pop llm.providers so the next file's fresh import resolves
        # the real (or absent) SDKs, not the fakes.
        sys.modules.pop("llm.providers", None)


# Test schema used across all provider tests.
class _DemoSchema(BaseModel):
    headline: str = Field(min_length=1, max_length=50)


# ---------------------------------------------------------------------------
# TestSchemaSurface
# ---------------------------------------------------------------------------


class TestSchemaSurface:
    def test_claude_provider_exposes_name(self):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        assert p.name == "claude"
        assert hasattr(p, "call_structured")

    def test_gemini_provider_exposes_name(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        assert p.name == "gemini"
        assert hasattr(p, "call_structured")

    def test_provider_call_structured_returns_optional_basemodel(self):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        # Force the internal client to error → soft-fail → None.
        p._client.messages.create = MagicMock(side_effect=RuntimeError("network down"))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestClaudeProvider
# ---------------------------------------------------------------------------


class _BlockObject:
    """Object-style content block to match the SDK's typed responses."""

    def __init__(self, btype: str, name: Optional[str] = None, payload: Optional[dict] = None):
        self.type = btype
        self.name = name
        self.input = payload


class _Response:
    def __init__(self, blocks: List[Any]):
        self.content = blocks


class TestClaudeProvider:
    def test_tool_use_happy_path_returns_validated_model(self):
        from llm.providers import ClaudeProvider, _STRUCTURED_TOOL_NAME

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(
            return_value=_Response([
                _BlockObject("tool_use", name=_STRUCTURED_TOOL_NAME, payload={"headline": "hi"}),
            ])
        )

        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert isinstance(out, _DemoSchema)
        assert out.headline == "hi"

    def test_dict_block_shape_also_parsed(self):
        from llm.providers import ClaudeProvider, _STRUCTURED_TOOL_NAME

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(
            return_value=_Response([
                {"type": "tool_use", "name": _STRUCTURED_TOOL_NAME, "input": {"headline": "ok"}},
            ])
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert isinstance(out, _DemoSchema)
        assert out.headline == "ok"

    def test_no_tool_use_block_returns_none(self):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(
            return_value=_Response([_BlockObject("text", name=None, payload=None)])
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_wrong_tool_name_returns_none(self):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(
            return_value=_Response([
                _BlockObject("tool_use", name="not_the_right_tool", payload={"headline": "hi"})
            ])
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_schema_mismatched_payload_returns_none(self):
        from llm.providers import ClaudeProvider, _STRUCTURED_TOOL_NAME

        p = ClaudeProvider(api_key="sk-test")
        # Missing required 'headline' field → ValidationError soft-failed to None.
        p._client.messages.create = MagicMock(
            return_value=_Response([
                _BlockObject("tool_use", name=_STRUCTURED_TOOL_NAME, payload={"wrong_field": 1}),
            ])
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_network_exception_returns_none(self):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(side_effect=ConnectionError("nope"))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_empty_content_list_returns_none(self):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(return_value=_Response([]))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestGeminiProvider
# ---------------------------------------------------------------------------


class _GeminiResponse:
    def __init__(self, text: Optional[str]):
        self.text = text


class TestGeminiProvider:
    def test_response_text_json_happy_path(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text='{"headline": "hi"}')
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert isinstance(out, _DemoSchema)
        assert out.headline == "hi"

    def test_empty_text_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(return_value=_GeminiResponse(text=""))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_none_text_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(return_value=_GeminiResponse(text=None))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_schema_mismatched_payload_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text='{"wrong_field": 99}')
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_malformed_json_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text="not-a-json-object")
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_network_exception_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(side_effect=TimeoutError("slow"))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_missing_sdk_returns_none(self):
        # Force ImportError for `from google import genai` regardless of
        # whether the real google-genai package is actually installed
        # (requirements.txt now requires it unconditionally): setting a
        # sys.modules entry to None — not just popping it — makes Python's
        # import system raise ModuleNotFoundError immediately rather than
        # falling through to a real on-disk package. The autouse fixture's
        # teardown restores whatever was there before regardless.
        sys.modules["google.genai"] = None
        sys.modules["google"] = None
        sys.modules.pop("llm.providers", None)
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        # ImportError path sets _client=None — call returns None silently.
        assert p._client is None
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestSoftFailContract — every entry never raises
# ---------------------------------------------------------------------------


class TestSoftFailContract:
    @pytest.mark.parametrize("exc", [RuntimeError("x"), TimeoutError(), ValueError(), Exception()])
    def test_claude_never_raises(self, exc):
        from llm.providers import ClaudeProvider

        p = ClaudeProvider(api_key="sk-test")
        p._client.messages.create = MagicMock(side_effect=exc)
        # Must NOT raise.
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    @pytest.mark.parametrize("exc", [RuntimeError("x"), TimeoutError(), ValueError(), Exception()])
    def test_gemini_never_raises(self, exc):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(side_effect=exc)
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestStatusRecording — last-real-call telemetry (llm/status_store.py) wiring.
# The store is patched on the providers module object; we assert WHAT was
# recorded, not that the file was written (that's the store's own suite).
# ---------------------------------------------------------------------------


class _RecordingStore:
    """Stand-in for llm.status_store — records calls instead of writing a file."""

    def __init__(self):
        self.successes: list = []
        self.failures: list = []

    def record_success(self, provider):
        self.successes.append(provider)

    def record_failure(self, provider, exc=None, *, error_kind=None):
        self.failures.append((provider, type(exc).__name__ if exc else None, error_kind))


class TestStatusRecording:
    def _patch_store(self):
        import llm.providers as providers_mod

        store = _RecordingStore()
        return providers_mod, patch.object(providers_mod, "status_store", store), store

    def test_call_failure_records_failure_once(self):
        from llm.providers import ClaudeProvider

        mod, ctx, store = self._patch_store()
        with ctx:
            p = ClaudeProvider(api_key="sk-test")
            p._client.messages.create = MagicMock(side_effect=RuntimeError("network down"))
            out = p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert out is None
        assert store.failures == [("claude", "RuntimeError", None)]
        assert store.successes == []

    def test_happy_path_records_success_exactly_once(self):
        from llm.providers import ClaudeProvider, _STRUCTURED_TOOL_NAME

        mod, ctx, store = self._patch_store()
        with ctx:
            p = ClaudeProvider(api_key="sk-test")
            p._client.messages.create = MagicMock(
                return_value=_Response(
                    [_BlockObject("tool_use", name=_STRUCTURED_TOOL_NAME, payload={"headline": "hi"})]
                )
            )
            out = p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert isinstance(out, _DemoSchema)
        assert store.successes == ["claude"]
        assert store.failures == []

    def test_schema_failure_records_success_then_schema(self):
        from llm.providers import ClaudeProvider, _STRUCTURED_TOOL_NAME

        mod, ctx, store = self._patch_store()
        with ctx:
            p = ClaudeProvider(api_key="sk-test")
            p._client.messages.create = MagicMock(
                return_value=_Response(
                    [_BlockObject("tool_use", name=_STRUCTURED_TOOL_NAME, payload={"wrong": 1})]
                )
            )
            out = p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert out is None
        # Key was ACCEPTED (success recorded) then the payload failed the schema.
        assert store.successes == ["claude"]
        assert store.failures == [("claude", "ValidationError", "schema")]

    def test_missing_block_still_records_success(self):
        # A 200 with no tool_use block (`return None`) STILL proves the key was
        # accepted — success must be recorded so a stale auth verdict clears.
        from llm.providers import ClaudeProvider

        mod, ctx, store = self._patch_store()
        with ctx:
            p = ClaudeProvider(api_key="sk-test")
            p._client.messages.create = MagicMock(
                return_value=_Response([_BlockObject("text", name=None, payload=None)])
            )
            out = p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert out is None
        assert store.successes == ["claude"]
        assert store.failures == []

    def test_gemini_records_under_gemini_provider_name(self):
        from llm.providers import GeminiProvider

        mod, ctx, store = self._patch_store()
        with ctx:
            p = GeminiProvider(api_key="g-test")
            p._client.models.generate_content = MagicMock(side_effect=RuntimeError("boom"))
            p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert store.failures == [("gemini", "RuntimeError", None)]

    def test_openai_records_success_before_parsing(self):
        from llm.providers import OpenAIProvider

        mod, ctx, store = self._patch_store()
        with ctx:
            p = OpenAIProvider(api_key="sk-test")
            p._client = MagicMock()
            # A refusal after a 200 → returns None but the key was accepted.
            msg = types.SimpleNamespace(refusal="no", parsed=None)
            completion = types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
            p._client.beta.chat.completions.parse = MagicMock(return_value=completion)
            out = p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert out is None
        assert store.successes == ["openai"]
        assert store.failures == []

    def test_real_store_under_adverse_fs_never_breaks_soft_fail(self):
        # The REAL store is non-raising BY CONSTRUCTION (its own suite pins
        # this), which is what makes providers.py's BARE store calls safe —
        # no call-site wrapping needed (the plan's explicit decision). This
        # verifies the end-to-end property: an unwritable OUTPUT_DIR makes the
        # store degrade silently, and call_structured still soft-fails to None
        # rather than propagating a write error into analyst commentary.
        from llm.providers import ClaudeProvider
        from settings import settings

        with mock.patch.object(settings, "OUTPUT_DIR", "/proc/nonexistent/cannot/write"):
            p = ClaudeProvider(api_key="sk-test")
            p._client.messages.create = MagicMock(side_effect=RuntimeError("network"))
            out = p.call_structured(system="s", user="u", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestSourceGuards — the lazy-SDK-import invariant (convention-only until now).
# ---------------------------------------------------------------------------


class TestSourceGuards:
    def test_no_top_level_sdk_import(self):
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "llm" / "providers.py"
        top = "\n".join(
            ln
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln and not ln[0].isspace()
        )
        for forbidden in (
            "import anthropic",
            "from anthropic",
            "import openai",
            "from openai",
            "import google",
            "from google",
        ):
            assert forbidden not in top, f"{forbidden!r} must be lazy (inside __init__), not top-level"
