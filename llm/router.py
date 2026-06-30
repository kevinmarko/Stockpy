"""
llm/router.py — Provider selection.
====================================

Pure routing per the approved plan:

* Claude exclusively for analyst rationale (rich per-symbol "why" prose).
* Gemini exclusively for alert commentary (concise ntfy push body).
* No second opinion, no cross-check.

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


def get_rationale_provider() -> Optional[LLMProvider]:
    """Return the configured rationale provider, or ``None`` to skip LLM."""
    if not settings.LLM_COMMENTARY_ENABLED:
        return None
    choice = (settings.LLM_COMMENTARY_RATIONALE_PROVIDER or "").lower()
    if choice in ("", "none"):
        return None
    if choice == "claude":
        if not settings.ANTHROPIC_API_KEY:
            logger.info("Rationale provider 'claude' selected but ANTHROPIC_API_KEY is unset.")
            return None
        try:
            return ClaudeProvider(
                api_key=settings.ANTHROPIC_API_KEY,
                timeout_seconds=float(settings.LLM_COMMENTARY_TIMEOUT_SECONDS),
            )
        except Exception as exc:
            logger.warning("Failed to construct ClaudeProvider: %s", exc)
            return None
    logger.info("Unknown rationale provider '%s' — skipping LLM.", choice)
    return None


def get_alert_provider() -> Optional[LLMProvider]:
    """Return the configured alert provider, or ``None`` to skip LLM."""
    if not settings.LLM_COMMENTARY_ENABLED:
        return None
    choice = (settings.LLM_COMMENTARY_ALERT_PROVIDER or "").lower()
    if choice in ("", "none"):
        return None
    if choice == "gemini":
        if not settings.GEMINI_API_KEY:
            logger.info("Alert provider 'gemini' selected but GEMINI_API_KEY is unset.")
            return None
        try:
            return GeminiProvider(
                api_key=settings.GEMINI_API_KEY,
                timeout_seconds=float(settings.LLM_COMMENTARY_TIMEOUT_SECONDS),
            )
        except Exception as exc:
            logger.warning("Failed to construct GeminiProvider: %s", exc)
            return None
    logger.info("Unknown alert provider '%s' — skipping LLM.", choice)
    return None
