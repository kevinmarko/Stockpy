"""
tests/test_gemini_multimodal.py
================================
Unit tests for ``GeminiProvider.call_structured_with_image`` — the multimodal
extension used by Tier 9 Scope 3 (Gemini chart vision).

Reuses the same fake-SDK pattern as ``tests/test_llm_providers.py``.

Coverage
--------
TestMultimodalHappyPath        — Part.from_bytes is constructed; response parses.
TestMultimodalSchemaMismatch   — bad JSON → ValidationError → None.
TestMultimodalEmptyText        — None / empty text → None.
TestMultimodalNetworkException — generate_content raises → None.
TestMissingSDK                 — provider built without SDK → _client=None → None.
"""

from __future__ import annotations

import sys
import types
from unittest import mock
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Fake SDK installer (mirrors tests/test_llm_providers.py)
# ---------------------------------------------------------------------------


def _install_fake_google_genai() -> None:
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

    class _FakePart:
        def __init__(self, data=None, mime_type=None):
            self.data = data
            self.mime_type = mime_type

        @classmethod
        def from_bytes(cls, *, data, mime_type, **kw):
            return cls(data=data, mime_type=mime_type)

    pkg_genai.Client = _FakeClient  # type: ignore[attr-defined]
    pkg_types.HttpOptions = _FakeHttpOptions  # type: ignore[attr-defined]
    pkg_types.GenerateContentConfig = _FakeGenerateContentConfig  # type: ignore[attr-defined]
    pkg_types.Part = _FakePart  # type: ignore[attr-defined]
    pkg_genai.types = pkg_types  # type: ignore[attr-defined]

    sys.modules["google"] = pkg_google
    sys.modules["google.genai"] = pkg_genai
    sys.modules["google.genai.types"] = pkg_types


@pytest.fixture(autouse=True)
def _fresh_fake_sdk():
    _injected = ("google", "google.genai", "google.genai.types")
    _prior = {k: sys.modules.get(k) for k in _injected}
    _install_fake_google_genai()
    sys.modules.pop("llm.providers", None)
    try:
        yield
    finally:
        for k, prior in _prior.items():
            if prior is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = prior
        sys.modules.pop("llm.providers", None)


class _DemoSchema(BaseModel):
    title: str = Field(min_length=1, max_length=20)


class _GeminiResponse:
    def __init__(self, text):
        self.text = text


# ---------------------------------------------------------------------------
# TestMultimodalHappyPath
# ---------------------------------------------------------------------------


class TestMultimodalHappyPath:
    def test_part_from_bytes_constructed_and_response_parsed(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text='{"title": "ok"}')
        )

        out = p.call_structured_with_image(
            system="sys",
            user="usr",
            image_bytes=b"\x89PNG\x00fake",
            schema_model=_DemoSchema,
        )
        assert isinstance(out, _DemoSchema)
        assert out.title == "ok"
        # The provider must have called generate_content with a list contents
        # whose second element is a Part-shaped object (mime_type set).
        called_args, called_kwargs = p._client.models.generate_content.call_args
        contents = called_kwargs.get("contents")
        assert isinstance(contents, list) and len(contents) == 2
        assert contents[0] == "usr"
        assert getattr(contents[1], "mime_type", None) == "image/png"
        assert getattr(contents[1], "data", None) == b"\x89PNG\x00fake"


# ---------------------------------------------------------------------------
# TestMultimodalSchemaMismatch
# ---------------------------------------------------------------------------


class TestMultimodalSchemaMismatch:
    def test_schema_mismatched_payload_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text='{"wrong_field": 1}')
        )
        out = p.call_structured_with_image(
            system="sys", user="usr", image_bytes=b"png",
            schema_model=_DemoSchema,
        )
        assert out is None

    def test_malformed_json_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text="not json at all")
        )
        out = p.call_structured_with_image(
            system="sys", user="usr", image_bytes=b"png",
            schema_model=_DemoSchema,
        )
        assert out is None


# ---------------------------------------------------------------------------
# TestMultimodalEmptyText
# ---------------------------------------------------------------------------


class TestMultimodalEmptyText:
    def test_empty_text_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text="")
        )
        out = p.call_structured_with_image(
            system="sys", user="usr", image_bytes=b"png",
            schema_model=_DemoSchema,
        )
        assert out is None

    def test_none_text_returns_none(self):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(
            return_value=_GeminiResponse(text=None)
        )
        out = p.call_structured_with_image(
            system="sys", user="usr", image_bytes=b"png",
            schema_model=_DemoSchema,
        )
        assert out is None


# ---------------------------------------------------------------------------
# TestMultimodalNetworkException
# ---------------------------------------------------------------------------


class TestMultimodalNetworkException:
    @pytest.mark.parametrize("exc", [RuntimeError("x"), TimeoutError(), ValueError(), Exception()])
    def test_generate_content_raises_returns_none(self, exc):
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        p._client.models.generate_content = MagicMock(side_effect=exc)
        out = p.call_structured_with_image(
            system="sys", user="usr", image_bytes=b"png",
            schema_model=_DemoSchema,
        )
        assert out is None


# ---------------------------------------------------------------------------
# TestMissingSDK
# ---------------------------------------------------------------------------


class TestMissingSDK:
    def test_missing_sdk_returns_none(self):
        # Remove fake SDK before importing GeminiProvider — the ImportError
        # branch in __init__ sets _client=None; the call is silently inert.
        sys.modules.pop("google.genai", None)
        sys.modules.pop("google", None)
        sys.modules.pop("llm.providers", None)
        from llm.providers import GeminiProvider

        p = GeminiProvider(api_key="g-test")
        assert p._client is None
        out = p.call_structured_with_image(
            system="sys", user="usr", image_bytes=b"png",
            schema_model=_DemoSchema,
        )
        assert out is None
