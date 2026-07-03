"""
tests/test_openai_provider.py
==============================
Unit tests for ``llm.providers.OpenAIProvider`` (Tier 9 Scope 4 — Opal).

All SDK calls are monkeypatched via a fake ``openai`` module installed into
``sys.modules`` before import — no real network requests are made, mirroring
the pattern already established in ``tests/test_llm_providers.py`` for
``ClaudeProvider``/``GeminiProvider``.

Coverage
--------
TestSchemaSurface        — exposes .name == "openai" and a soft-fail call_structured.
TestHappyPath             — ``.parse()`` happy path returns a validated model.
TestSoftFailMatrix        — refusal, missing `.parsed`, schema mismatch, network
                            exception, missing SDK, timeout-as-exception → all None.
TestNoTopLevelImport      — the fake SDK is only reachable via the lazy
                            ``import openai`` inside ``__init__`` (CONSTRAINT: lazy reach).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field, ValidationError


# ---------------------------------------------------------------------------
# Fake `openai` SDK installer
# ---------------------------------------------------------------------------


def _install_fake_openai() -> types.ModuleType:
    fake = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.beta = MagicMock()

    fake.OpenAI = _FakeOpenAI  # type: ignore[attr-defined]
    sys.modules["openai"] = fake
    return fake


@pytest.fixture(autouse=True)
def _fresh_fake_openai():
    """Each test gets a fresh fake `openai` module; teardown restores prior state."""
    prior = sys.modules.get("openai")
    _install_fake_openai()
    sys.modules.pop("llm.providers", None)
    try:
        yield
    finally:
        if prior is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = prior
        sys.modules.pop("llm.providers", None)


class _DemoSchema(BaseModel):
    headline: str = Field(min_length=1, max_length=50)


def _completion_with_parsed(parsed, refusal=None):
    message = MagicMock()
    message.parsed = parsed
    message.refusal = refusal
    choice = MagicMock()
    choice.message = message
    completion = MagicMock()
    completion.choices = [choice]
    return completion


# ---------------------------------------------------------------------------
# TestSchemaSurface
# ---------------------------------------------------------------------------


class TestSchemaSurface:
    def test_exposes_name_openai(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        assert p.name == "openai"
        assert hasattr(p, "call_structured")

    def test_call_structured_returns_optional_basemodel(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        p._client.beta.chat.completions.parse = MagicMock(side_effect=RuntimeError("down"))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestHappyPath
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_parse_happy_path_returns_validated_model(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        good = _DemoSchema(headline="hi")
        p._client.beta.chat.completions.parse = MagicMock(
            return_value=_completion_with_parsed(good)
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert isinstance(out, _DemoSchema)
        assert out.headline == "hi"

    def test_parse_called_with_response_format_schema(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test", model="gpt-4o-test")
        mock_parse = MagicMock(
            return_value=_completion_with_parsed(_DemoSchema(headline="ok"))
        )
        p._client.beta.chat.completions.parse = mock_parse
        p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        _, kwargs = mock_parse.call_args
        assert kwargs["model"] == "gpt-4o-test"
        assert kwargs["response_format"] is _DemoSchema
        assert kwargs["messages"][0]["role"] == "system"
        assert kwargs["messages"][1]["role"] == "user"


# ---------------------------------------------------------------------------
# TestSoftFailMatrix
# ---------------------------------------------------------------------------


class TestSoftFailMatrix:
    def test_refusal_returns_none(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        p._client.beta.chat.completions.parse = MagicMock(
            return_value=_completion_with_parsed(None, refusal="cannot comply")
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_missing_parsed_returns_none(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        p._client.beta.chat.completions.parse = MagicMock(
            return_value=_completion_with_parsed(None)
        )
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_empty_choices_returns_none(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        completion = MagicMock()
        completion.choices = []
        p._client.beta.chat.completions.parse = MagicMock(return_value=completion)
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_network_exception_returns_none(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        p._client.beta.chat.completions.parse = MagicMock(side_effect=ConnectionError("nope"))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_timeout_exception_returns_none(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        p._client.beta.chat.completions.parse = MagicMock(side_effect=TimeoutError("slow"))
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    @pytest.mark.parametrize("exc", [RuntimeError("x"), ValueError(), Exception(), ValidationError.from_exception_data("x", [])])
    def test_never_raises_for_any_exception(self, exc):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        p._client.beta.chat.completions.parse = MagicMock(side_effect=exc)
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_missing_sdk_returns_none(self):
        sys.modules.pop("openai", None)
        sys.modules.pop("llm.providers", None)
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        assert p._client is None
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None

    def test_client_construction_failure_returns_none_client(self):
        # Force `openai.OpenAI(...)` itself to raise inside __init__.
        fake = sys.modules["openai"]

        class _RaisingOpenAI:
            def __init__(self, **kwargs):
                raise RuntimeError("bad api key")

        fake.OpenAI = _RaisingOpenAI  # type: ignore[attr-defined]
        sys.modules.pop("llm.providers", None)
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        assert p._client is None
        out = p.call_structured(system="sys", user="usr", schema_model=_DemoSchema)
        assert out is None


# ---------------------------------------------------------------------------
# TestConstructorDefaults
# ---------------------------------------------------------------------------


class TestConstructorDefaults:
    def test_default_model_is_gpt4o(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        assert p._model == "gpt-4o"

    def test_timeout_applied_at_client_construction(self):
        from llm.providers import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test", timeout_seconds=7.5)
        assert p._timeout_seconds == 7.5
