"""
llm/providers.py — Provider abstraction over Claude, Gemini, and OpenAI.
=========================================================================

Three providers implement :class:`LLMProvider`:

* :class:`ClaudeProvider` — Anthropic Messages API with structured-output
  forcing via ``tool_use`` (single emitter tool whose ``input_schema`` is the
  pydantic JSON schema; the first ``tool_use`` block's ``input`` is parsed
  through the schema).
* :class:`GeminiProvider` — Google ``google.genai`` client with
  ``response_mime_type='application/json'`` + ``response_schema=...`` for
  structured-output forcing.
* :class:`OpenAIProvider` — OpenAI ``openai`` SDK (>=1.40) using the
  ``client.beta.chat.completions.parse(response_format=schema_model)``
  Structured Outputs helper, which returns an already-validated pydantic
  instance (or ``None`` on a refusal). Backs Opal (Tier 9 Scope 4), the
  front-of-pipeline research agent in :mod:`llm.research`.

All three providers:

* Lazy-import their SDK inside ``__init__`` so the package costs nothing
  to load when the relevant master switch is off.
* Apply a hard wall-clock timeout (``LLM_COMMENTARY_TIMEOUT_SECONDS`` /
  ``OPAL_RESEARCH_TIMEOUT_SECONDS``).
* Wrap the entire call + parse in try/except — any exception → ``None``
  (CONSTRAINT #6).  Callers depend on this contract to fall back to the
  deterministic template.
* Validate the response through ``schema_model.model_validate`` /
  ``model_validate_json`` (or receive an already-validated instance, as
  with OpenAI's ``.parse()`` helper) so a malformed response is rejected at
  the schema boundary, NOT downstream.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, Type

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


_STRUCTURED_TOOL_NAME = "emit_structured_output"


class LLMProvider(ABC):
    """Abstract provider — implementations soft-fail to ``None``."""

    name: str

    @abstractmethod
    def call_structured(
        self,
        system: str,
        user: str,
        schema_model: Type[BaseModel],
    ) -> Optional[BaseModel]:
        """Return a validated schema instance or ``None`` on any failure.

        Implementations MUST NOT raise.  A network error, auth failure,
        timeout, malformed payload, or schema mismatch all return ``None``
        so the caller can fall back to a deterministic template.
        """


class ClaudeProvider(LLMProvider):
    """Anthropic Claude via the official ``anthropic`` SDK.

    Uses the Messages API with a single forced tool whose ``input_schema``
    is the pydantic JSON schema.  Forcing ``tool_choice={"type": "tool",
    "name": ...}`` yields strict structured JSON without prompt engineering.
    """

    name = "claude"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-opus-4-7",
        timeout_seconds: float = 8.0,
        max_tokens: int = 800,
    ) -> None:
        # Lazy import: the SDK is only loaded when this provider is actually
        # instantiated.  When LLM_COMMENTARY_ENABLED is False the router never
        # constructs a provider, and `anthropic` is never imported at all.
        import anthropic  # noqa: PLC0415

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._max_tokens = max_tokens

    def call_structured(
        self,
        system: str,
        user: str,
        schema_model: Type[BaseModel],
    ) -> Optional[BaseModel]:
        try:
            tool_schema = {
                "name": _STRUCTURED_TOOL_NAME,
                "description": (
                    "Emit the structured output strictly conforming to the "
                    "input_schema. Do not add fields. Do not call any other tool."
                ),
                "input_schema": schema_model.model_json_schema(),
            }
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": _STRUCTURED_TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            logger.warning("ClaudeProvider call failed: %s", exc)
            return None

        # The forced-tool path guarantees the model emits at least one
        # tool_use block.  Defensive: skim for the first matching name.
        try:
            for block in getattr(response, "content", []) or []:
                btype = getattr(block, "type", None) or (
                    block.get("type") if isinstance(block, dict) else None
                )
                if btype != "tool_use":
                    continue
                bname = getattr(block, "name", None) or (
                    block.get("name") if isinstance(block, dict) else None
                )
                if bname != _STRUCTURED_TOOL_NAME:
                    continue
                payload = getattr(block, "input", None) or (
                    block.get("input") if isinstance(block, dict) else None
                )
                if payload is None:
                    return None
                return schema_model.model_validate(payload)
        except ValidationError as exc:
            logger.warning("ClaudeProvider schema validation failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("ClaudeProvider response parse failed: %s", exc)
            return None
        return None


class GeminiProvider(LLMProvider):
    """Google Gemini via the official ``google-genai`` SDK.

    Uses ``response_mime_type='application/json'`` + ``response_schema``
    to force structured JSON output.  Response ``.text`` is the JSON
    payload, parsed via ``schema_model.model_validate_json``.
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 8.0,
        max_output_tokens: int = 800,
    ) -> None:
        # Lazy import: see ClaudeProvider note.
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types as genai_types  # noqa: PLC0415
        except ImportError:  # pragma: no cover - import guard
            # If the SDK isn't installed, every call returns None and the
            # caller falls back to the deterministic template.
            self._client = None
            self._types = None
            self._model = model
            self._timeout_seconds = timeout_seconds
            self._max_output_tokens = max_output_tokens
            return

        self._genai = genai
        self._types = genai_types
        # The SDK's HTTP client honours an httpx-style timeout via
        # http_options; falling back to default if the surface differs.
        try:
            http_options = genai_types.HttpOptions(timeout=int(timeout_seconds * 1000))
            self._client = genai.Client(api_key=api_key, http_options=http_options)
        except Exception:
            self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    def call_structured(
        self,
        system: str,
        user: str,
        schema_model: Type[BaseModel],
    ) -> Optional[BaseModel]:
        if self._client is None or self._types is None:
            return None
        try:
            config = self._types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema_model,
                max_output_tokens=self._max_output_tokens,
            )
            response = self._client.models.generate_content(
                model=self._model,
                contents=user,
                config=config,
            )
        except Exception as exc:
            logger.warning("GeminiProvider call failed: %s", exc)
            return None

        try:
            text = getattr(response, "text", None)
            if not text:
                return None
            return schema_model.model_validate_json(text)
        except ValidationError as exc:
            logger.warning("GeminiProvider schema validation failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("GeminiProvider response parse failed: %s", exc)
            return None

    def call_structured_with_image(
        self,
        system: str,
        user: str,
        image_bytes: bytes,
        schema_model: Type[BaseModel],
        *,
        mime_type: str = "image/png",
    ) -> Optional[BaseModel]:
        """Multimodal variant — sends a PNG/JPEG alongside the user prompt.

        Used by :mod:`llm.chart_insight` (Tier 9 Scope 3) to send a
        matplotlib-rendered price chart to Gemini Vision and receive a
        structured :class:`llm.schemas.ChartPatternRead` interpretation.

        Soft-fail contract: same as :meth:`call_structured` — any provider
        failure, parse error, or schema mismatch returns ``None``.  The
        caller falls back to whatever deterministic chart description it
        was going to render anyway.
        """
        if self._client is None or self._types is None:
            return None
        try:
            image_part = self._types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            config = self._types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema_model,
                max_output_tokens=self._max_output_tokens,
            )
            response = self._client.models.generate_content(
                model=self._model,
                contents=[user, image_part],
                config=config,
            )
        except Exception as exc:
            logger.warning("GeminiProvider multimodal call failed: %s", exc)
            return None

        try:
            text = getattr(response, "text", None)
            if not text:
                return None
            return schema_model.model_validate_json(text)
        except ValidationError as exc:
            logger.warning("GeminiProvider multimodal validation failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("GeminiProvider multimodal response parse failed: %s", exc)
            return None


class OpenAIProvider(LLMProvider):
    """OpenAI GPT via the official ``openai`` SDK (>=1.40, Structured Outputs).

    Uses ``client.beta.chat.completions.parse(response_format=schema_model)``
    — the SDK-side helper that performs OpenAI's strict-mode JSON-schema
    post-processing (``additionalProperties: false`` on every object,
    Optional fields folded into ``required`` with nullable typing) so the
    caller never hand-rolls a raw ``response_format={"type": "json_schema",
    ...}`` payload, which would 400 against a bare pydantic
    ``model_json_schema()`` output.

    Backs Opal (Tier 9 Scope 4) — the front-of-pipeline research agent in
    :mod:`llm.research`.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o",
        timeout_seconds: float = 15.0,
    ) -> None:
        # Lazy import: see ClaudeProvider note. The timeout is applied at
        # CLIENT INIT (mirrors anthropic.Anthropic(..., timeout=...)) — never
        # signal.alarm, which breaks under threads/Streamlit.
        try:
            import openai  # noqa: PLC0415
        except ImportError:  # pragma: no cover - import guard
            self._client = None
            self._model = model
            self._timeout_seconds = timeout_seconds
            return

        self._openai = openai
        try:
            self._client = openai.OpenAI(api_key=api_key, timeout=timeout_seconds)
        except Exception:
            self._client = None
        self._model = model
        self._timeout_seconds = timeout_seconds

    def call_structured(
        self,
        system: str,
        user: str,
        schema_model: Type[BaseModel],
    ) -> Optional[BaseModel]:
        if self._client is None:
            return None
        try:
            completion = self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=schema_model,
            )
        except Exception as exc:
            logger.warning("OpenAIProvider call failed: %s", exc)
            return None

        try:
            message = completion.choices[0].message
            if getattr(message, "refusal", None):
                logger.warning("OpenAIProvider refused: %s", message.refusal)
                return None
            parsed = getattr(message, "parsed", None)
            if parsed is None:
                return None
            return parsed
        except Exception as exc:
            logger.warning("OpenAIProvider response parse failed: %s", exc)
            return None
