"""
llm/commentary.py — public entry points for analyst rationale + alert text.
============================================================================

Both functions are *pure on the contract*:

* Return a validated pydantic schema instance on success.
* Return ``None`` on ANY failure (master switch off, missing key, provider
  exception, schema mismatch, cache miss + provider miss).

Callers MUST handle the ``None`` path by falling back to the existing
deterministic template — never overwrite, never default to empty string.

System prompts are inlined here as constants so the package is self-contained.
If the prompt registry is enabled it overrides the inlined defaults at
runtime, falling back silently when the registry is degraded
(``CONSTRAINT #6``).

Caching: each call derives a sha256 key (see :mod:`llm.cache`) and stores
the validated payload as a JSON dict.  Cache hits skip the provider call
entirely — the operator can re-render the same recommendation any number
of times in a single UTC day without re-spending tokens.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from llm.cache import cache_get, cache_put, make_cache_key
from llm.router import get_alert_provider, get_rationale_provider
from llm.schemas import AlertCommentary, AnalystRationale
from settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Baseline system prompts (used when the Prompt Registry is disabled or its
# fetch fails).  They cite the recommendation/alert payload but DO NOT permit
# the model to invent numeric values — the schema bounds the prose only.
# ---------------------------------------------------------------------------
_RATIONALE_SYSTEM_PROMPT = (
    "You are a careful, sober equity analyst writing a single advisory note. "
    "Your output MUST conform exactly to the provided structured schema. "
    "Use ONLY the numeric and categorical fields the user provides — never "
    "invent prices, percentages, conviction scores, or position sizes. "
    "Treat the recommendation as advisory text only; you are not authorising "
    "any trade. If a number is missing, do not fabricate one — refer to it "
    "qualitatively instead. Be concise, declarative, and avoid hedging filler."
)

_ALERT_SYSTEM_PROMPT = (
    "You are writing a single short push-notification body for a stock-watch "
    "alert. Your output MUST conform exactly to the provided structured schema. "
    "Stay within the bounds of the trigger payload — never invent numbers or "
    "claim new signals. Maximum 280 characters in the body. Be informative, "
    "concrete, and skip filler like 'please be advised'."
)


def _registry_prompt(prompt_id: str, default: str) -> str:
    """Pull a system prompt from the Prompt Registry, falling back on failure."""
    if not getattr(settings, "PROMPT_REGISTRY_ENABLED", False):
        return default
    try:
        from prompt_registry import get_registry  # noqa: PLC0415

        registry = get_registry()
        body = registry.get(prompt_id, default)
    except Exception as exc:
        logger.debug("Prompt registry lookup for %s failed: %s", prompt_id, exc)
        return default
    if not body:
        return default
    return body


def _research_brief_cache_variant(context: Optional[Dict[str, Any]]) -> str:
    """Return a short, deterministic fingerprint of ``context["research_brief"]``.

    Empty string when no research brief is present — so the cache key is
    byte-identical to the pre-Opal format for the common (no-brief) path.
    When a brief IS present, its content is hashed into the key so a
    brief-augmented rationale never collides with a brief-less one, and a
    *changed* brief invalidates the cached rationale (Tier 9 Scope 4).
    """
    brief = (context or {}).get("research_brief") if context else None
    if not isinstance(brief, dict) or not brief:
        return ""
    try:
        canonical = json.dumps(brief, sort_keys=True, default=str)
    except Exception:
        # A non-serialisable brief still segregates the cache — just less
        # precisely — via a stable marker rather than a content hash.
        return "rb"
    return "rb" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _format_rationale_user_prompt(
    rec_skeleton: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Render the user-turn prompt for analyst rationale generation.

    When ``context["research_brief"]`` is present (Tier 9 Scope 4 — Opal's
    grounded research brief, generated BEFORE this call by
    ``engine.advisory.enrich_with_llm_rationale``), appends a "Research
    context" block so Claude's own synthesis can cite it. Purely additive —
    the existing payload lines and instruction paragraph are unchanged when
    no research brief is present, so pre-Opal behavior is byte-identical.
    """
    payload_lines = ["Recommendation payload:"]
    for key in (
        "symbol",
        "action",
        "strategy",
        "conviction",
        "rationale",
        "suggested_position_pct",
        "forecast",
        "key_indicators",
        "data_quality",
    ):
        if key in rec_skeleton:
            payload_lines.append(f"  {key}: {rec_skeleton[key]!r}")

    research_brief = (context or {}).get("research_brief") if context else None
    if isinstance(research_brief, dict) and research_brief:
        payload_lines.append("\nResearch context (Opal, grounded on real retrieved data):")
        for key in ("thesis_context", "catalysts", "risk_factors", "recent_developments",
                    "data_confidence", "sources_note"):
            if key in research_brief:
                payload_lines.append(f"  {key}: {research_brief[key]!r}")

    payload_lines.append(
        "\nWrite the structured analyst note. Headline first, then a 2-3 sentence "
        "'why now' grounded in the payload, 1-3 key-risk bullets, and one "
        "invalidation sentence. Reference the payload's existing numbers — do "
        "not invent any. If research context is provided above, you may draw "
        "on it for framing but must not invent facts beyond it."
    )
    return "\n".join(payload_lines)


def _format_alert_user_prompt(alert_skeleton: Dict[str, Any]) -> str:
    """Render the user-turn prompt for alert commentary generation."""
    payload_lines = ["Watch / trade alert payload:"]
    for key in ("symbol", "rule_type", "kind", "priority", "trigger_detail", "template"):
        if key in alert_skeleton:
            payload_lines.append(f"  {key}: {alert_skeleton[key]!r}")
    payload_lines.append(
        "\nWrite a single-paragraph push-notification body ≤280 chars that an "
        "operator can scan from their phone. Stay within the trigger payload."
    )
    return "\n".join(payload_lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_analyst_rationale(
    rec_skeleton: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Optional[AnalystRationale]:
    """Return Claude-generated analyst narrative or ``None`` on any failure.

    Parameters
    ----------
    rec_skeleton :
        Dict view of the deterministic ``Recommendation`` (use
        ``dataclasses.asdict(rec)`` or a hand-built dict).  The model
        references these fields verbatim — it never invents new ones.
    context :
        Optional extra payload (regime DTO snapshot, macro snippets).  When
        ``context["research_brief"]`` is present (Tier 9 Scope 4 — Opal),
        it is appended as a "Research context" block in the user prompt.
        Not required — absent or empty behaves exactly as before Opal.

    Returns
    -------
    AnalystRationale | None
        Schema-validated structured output, or ``None`` if the master
        switch is off, no provider is configured, the call failed, or
        the response was malformed.  Callers MUST treat ``None`` as
        "fall back to the deterministic template" (CONSTRAINT #6).
    """
    try:
        if not getattr(settings, "LLM_COMMENTARY_ENABLED", False):
            return None
        symbol = str(rec_skeleton.get("symbol", "")).upper()
        action = str(rec_skeleton.get("action", "HOLD")).upper()
        score = 0.0
        ki = rec_skeleton.get("key_indicators") or {}
        if isinstance(ki, dict):
            try:
                score = float(ki.get("score", ki.get("adjusted_score", 0.0)) or 0.0)
            except Exception:
                score = 0.0
        # Cache key must reflect the LIVE-configured provider for this job —
        # either "claude" or "gemini" is valid (flexible per-job routing) — so
        # switching providers naturally segregates/invalidates cache entries
        # rather than serving a payload generated by a different model.
        configured_provider = (settings.LLM_COMMENTARY_RATIONALE_PROVIDER or "none").lower()
        # An Opal research brief in the context changes the user prompt but
        # none of the base key dimensions — fold a fingerprint of it into the
        # key so a brief-augmented rationale never serves (or is served by) a
        # brief-less cached entry, and a changed brief invalidates (Tier 9
        # Scope 4).  Empty when no brief → byte-identical to the pre-Opal key.
        brief_variant = _research_brief_cache_variant(context)
        cache_key = make_cache_key(
            provider=configured_provider,
            schema_name=AnalystRationale.__name__,
            symbol=symbol,
            score=score,
            action=action,
            variant=brief_variant,
        )
        cached = cache_get(cache_key)
        if cached is not None:
            try:
                return AnalystRationale.model_validate(cached)
            except Exception:
                # Corrupt cache entry — fall through to re-fetch.
                pass

        provider = get_rationale_provider()
        if provider is None:
            return None

        system = _registry_prompt("llm.rationale.system", _RATIONALE_SYSTEM_PROMPT)
        user = _format_rationale_user_prompt(rec_skeleton, context)
        result = provider.call_structured(system=system, user=user, schema_model=AnalystRationale)
        if result is None:
            return None

        try:
            cache_put(
                cache_key,
                result.model_dump(),
                meta={"provider": provider.name, "symbol": symbol, "action": action},
            )
        except Exception as exc:
            logger.debug("LLM cache_put failed (non-fatal): %s", exc)
        return result
    except Exception as exc:
        logger.warning("generate_analyst_rationale failed unexpectedly: %s", exc)
        return None


def generate_alert_commentary(
    alert_skeleton: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Optional[AlertCommentary]:
    """Return Gemini-generated alert text or ``None`` on any failure.

    Parameters
    ----------
    alert_skeleton :
        Dict view of the deterministic ``WatchAlert`` / ``TradeAlert``.
        Should include at minimum ``symbol`` and either ``rule_type`` or
        ``kind`` plus ``trigger_detail`` for context.
    context :
        Optional extra payload; reserved for future expansion.

    Returns
    -------
    AlertCommentary | None
        Schema-validated body, or ``None`` if the master switch is off,
        no provider is configured, the call failed, or the response was
        malformed.  Callers MUST treat ``None`` as "use the deterministic
        template message verbatim" (append-never-replace).
    """
    try:
        if not getattr(settings, "LLM_COMMENTARY_ENABLED", False):
            return None
        symbol = str(alert_skeleton.get("symbol", "")).upper()
        rule_or_kind = str(alert_skeleton.get("kind") or alert_skeleton.get("rule_type") or "alert")
        # See the matching comment in generate_analyst_rationale — the cache
        # key must reflect the LIVE-configured provider for this job.
        configured_provider = (settings.LLM_COMMENTARY_ALERT_PROVIDER or "none").lower()
        cache_key = make_cache_key(
            provider=configured_provider,
            schema_name=AlertCommentary.__name__,
            symbol=symbol,
            score=0.0,
            action=rule_or_kind.upper(),
        )
        cached = cache_get(cache_key)
        if cached is not None:
            try:
                return AlertCommentary.model_validate(cached)
            except Exception:
                pass

        provider = get_alert_provider()
        if provider is None:
            return None

        system = _registry_prompt("llm.alert.system", _ALERT_SYSTEM_PROMPT)
        user = _format_alert_user_prompt(alert_skeleton)
        result = provider.call_structured(system=system, user=user, schema_model=AlertCommentary)
        if result is None:
            return None

        try:
            cache_put(
                cache_key,
                result.model_dump(),
                meta={"provider": provider.name, "symbol": symbol, "kind": rule_or_kind},
            )
        except Exception as exc:
            logger.debug("LLM cache_put failed (non-fatal): %s", exc)
        return result
    except Exception as exc:
        logger.warning("generate_alert_commentary failed unexpectedly: %s", exc)
        return None
