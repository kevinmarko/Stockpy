"""
llm/router.py — Provider selection.
====================================

Flexible per-job routing: each job (analyst rationale, alert commentary) has
its own operator-configured provider choice (``LLM_COMMENTARY_RATIONALE_PROVIDER``
/ ``LLM_COMMENTARY_ALERT_PROVIDER``), and either ``"claude"`` or ``"gemini"``
is valid for either job — the operator can run Claude-only, Gemini-only, or
mix-and-match (e.g. Gemini for rationale, Claude for alerts). There is still
no cross-check: each job calls exactly one provider, never both.

A third job — Opal's front-of-pipeline research brief (Tier 9 Scope 4,
:mod:`llm.research`) — is served by :func:`get_research_provider`, gated on
its own independent master switch (``OPAL_RESEARCH_ENABLED``). Opal now also
supports flexible routing between ``"openai"`` and ``"gemini"`` via
``OPAL_RESEARCH_PROVIDER``.

All three selectors return ``None`` when the relevant master switch is off,
when no key is configured for the requested provider, or when the operator
pinned the provider to ``"none"``.  ``None`` is the contract for "skip LLM,
fall back to deterministic template" (CONSTRAINT #6).
"""

from __future__ import annotations

import logging
from typing import Optional

from llm.providers import ClaudeProvider, GeminiProvider, LLMProvider, OpenAIProvider
from settings import settings

logger = logging.getLogger(__name__)

# The OpenAI-flavored default baked into ``settings.OPAL_RESEARCH_MODEL``.
# When the operator switches ``OPAL_RESEARCH_PROVIDER`` to "gemini" without
# also touching ``OPAL_RESEARCH_MODEL``, this lets us detect "still the
# OpenAI default" and fall back to GeminiProvider's own model default
# instead of passing an OpenAI model name to the Gemini SDK.
_OPENAI_DEFAULT_MODEL = "gpt-4o"


def _construct_provider(choice: str, timeout_seconds: float) -> Optional[LLMProvider]:
    """Construct the named provider (``"claude"`` | ``"gemini"``), or ``None``.

    Shared dispatch used by both :func:`get_rationale_provider` and
    :func:`get_alert_provider` so either provider can serve either job.
    Soft-fails to ``None`` on a missing key or a construction error
    (CONSTRAINT #6) — never raises.
    """
    if choice == "claude":
        if not settings.ANTHROPIC_API_KEY:
            logger.info("Provider 'claude' selected but ANTHROPIC_API_KEY is unset.")
            return None
        try:
            return ClaudeProvider(
                api_key=settings.ANTHROPIC_API_KEY,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to construct ClaudeProvider: %s", exc)
            return None
    if choice == "gemini":
        if not settings.GEMINI_API_KEY:
            logger.info("Provider 'gemini' selected but GEMINI_API_KEY is unset.")
            return None
        try:
            return GeminiProvider(
                api_key=settings.GEMINI_API_KEY,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to construct GeminiProvider: %s", exc)
            return None
    logger.info("Unknown provider '%s' — skipping LLM.", choice)
    return None


def get_rationale_provider() -> Optional[LLMProvider]:
    """Return the configured rationale provider, or ``None`` to skip LLM.

    Reads ``LLM_COMMENTARY_RATIONALE_PROVIDER`` — either ``"claude"`` or
    ``"gemini"`` is valid here.
    """
    if not settings.LLM_COMMENTARY_ENABLED:
        return None
    choice = (settings.LLM_COMMENTARY_RATIONALE_PROVIDER or "").lower()
    if choice in ("", "none"):
        return None
    return _construct_provider(choice, float(settings.LLM_COMMENTARY_TIMEOUT_SECONDS))


def get_alert_provider() -> Optional[LLMProvider]:
    """Return the configured alert provider, or ``None`` to skip LLM.

    Reads ``LLM_COMMENTARY_ALERT_PROVIDER`` — either ``"claude"`` or
    ``"gemini"`` is valid here.
    """
    if not settings.LLM_COMMENTARY_ENABLED:
        return None
    choice = (settings.LLM_COMMENTARY_ALERT_PROVIDER or "").lower()
    if choice in ("", "none"):
        return None
    return _construct_provider(choice, float(settings.LLM_COMMENTARY_TIMEOUT_SECONDS))


def get_research_provider() -> Optional[LLMProvider]:
    """Return the configured Opal research provider, or ``None`` to skip LLM.

    Gated on its OWN master switch (``OPAL_RESEARCH_ENABLED``, independent of
    ``LLM_COMMENTARY_ENABLED`` — Opal can run without commentary enabled) and
    ``OPAL_RESEARCH_PROVIDER`` — ``"openai"`` or ``"gemini"``. Returns
    ``None`` when the switch is off, the provider choice is unrecognized,
    the relevant API key is unset, or construction fails (CONSTRAINT #6).
    """
    if not getattr(settings, "OPAL_RESEARCH_ENABLED", False):
        return None
    choice = (getattr(settings, "OPAL_RESEARCH_PROVIDER", "") or "").lower()
    if choice in ("", "none"):
        return None

    timeout = float(getattr(settings, "OPAL_RESEARCH_TIMEOUT_SECONDS", 15) or 15)
    configured_model = getattr(settings, "OPAL_RESEARCH_MODEL", "") or ""

    if choice == "openai":
        api_key = getattr(settings, "OPENAI_API_KEY", None)
        if not api_key:
            logger.info("Opal research provider 'openai' selected but OPENAI_API_KEY is unset.")
            return None
        try:
            return OpenAIProvider(
                api_key=api_key,
                model=configured_model or _OPENAI_DEFAULT_MODEL,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            logger.warning("Failed to construct OpenAIProvider: %s", exc)
            return None

    if choice == "gemini":
        api_key = getattr(settings, "GEMINI_API_KEY", None)
        if not api_key:
            logger.info("Opal research provider 'gemini' selected but GEMINI_API_KEY is unset.")
            return None
        try:
            kwargs: dict = {"api_key": api_key, "timeout_seconds": timeout}
            # Only pass an explicit model override when the operator set one
            # that isn't the OpenAI-flavored default — otherwise let
            # GeminiProvider fall back to its own (Gemini) model default.
            if configured_model and configured_model != _OPENAI_DEFAULT_MODEL:
                kwargs["model"] = configured_model
            return GeminiProvider(**kwargs)
        except Exception as exc:
            logger.warning("Failed to construct GeminiProvider: %s", exc)
            return None

    logger.info("Unknown Opal research provider '%s' — skipping LLM.", choice)
    return None
