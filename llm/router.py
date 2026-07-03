"""
llm/router.py — Provider selection.
====================================

Flexible per-job routing: each job (analyst rationale, alert commentary) has
its own operator-configured provider choice (``LLM_COMMENTARY_RATIONALE_PROVIDER``
/ ``LLM_COMMENTARY_ALERT_PROVIDER``), and either ``"claude"`` or ``"gemini"``
is valid for either job — the operator can run Claude-only, Gemini-only, or
mix-and-match (e.g. Gemini for rationale, Claude for alerts). There is still
no cross-check: each job calls exactly one provider, never both.

Both selectors return ``None`` when the master switch is off, when no key
is configured for the requested provider, or when the operator pinned the
provider to ``"none"``.  ``None`` is the contract for "skip LLM, fall back
to deterministic template" (CONSTRAINT #6).
"""

from __future__ import annotations

import logging
from typing import Optional

from llm.providers import ClaudeProvider, GeminiProvider, LLMProvider
from settings import settings

logger = logging.getLogger(__name__)


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
