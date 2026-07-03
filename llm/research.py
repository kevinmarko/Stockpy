"""
llm/research.py — Tier 9 Scope 4 Opal grounded research brief (OpenAI/GPT).
============================================================================

Public entry point :func:`generate_research_brief` takes a symbol (+ an
optional context dict), assembles a grounding packet of REAL retrieved
Finnhub company news + earnings date (+ an optional macro snippet from
``context``), sends it to OpenAI via :meth:`OpenAIProvider.call_structured`,
and returns a schema-validated :class:`llm.schemas.ResearchBrief` — or
``None`` on any failure.

Front-of-pipeline design
------------------------
Opal runs BEFORE Claude's analyst-rationale call. Its output is threaded
into ``context["research_brief"]`` by
:func:`engine.advisory.enrich_with_llm_rationale` so
:func:`llm.commentary._format_rationale_user_prompt` can cite it as
additional context for Claude's own synthesis — enrichment, never
replacement (same contract as the rest of Tier 9).

No fabricated metrics (CONSTRAINT #4)
--------------------------------------
The grounding packet is assembled from REAL retrieved data only
(``signals.news_catalyst.fetch_company_news`` / ``fetch_next_earnings``).
The system prompt explicitly forbids inventing catalysts, headlines, or
dates not present in the packet. ``ResearchBrief`` itself has NO numeric
field — there is nothing to fabricate a price target or score into.

Soft-fail contract (CONSTRAINT #6)
-----------------------------------
Every code path that touches the Finnhub client, the SDK, or the cache is
wrapped in try/except. Any failure → ``None``; the caller falls back to
the deterministic template exactly as if Opal were disabled.

Opt-in / default-off
---------------------
Gated on ``settings.OPAL_RESEARCH_ENABLED`` (default ``False``) — when off,
zero network calls and (via the lazy imports below) zero ``openai`` SDK
reach.

Cache reuse
-----------
Reuses :func:`llm.cache.make_cache_key` / :func:`cache_get` / :func:`cache_put`
so the day-bucketed JSON cache contract matches the rest of Tier 9. The
``score`` / ``action`` slots of the cache key aren't semantic for a research
brief, so they're pinned to ``0.0`` / ``"RESEARCH"`` (mirrors how
:mod:`llm.chart_insight` pins non-scored artifacts) and the UTC-date bucket
is the natural daily refresh boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from llm.cache import cache_get, cache_put, make_cache_key
from llm.schemas import ResearchBrief
from settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Baseline system prompt — inlined; the Prompt Registry override path is
# identical to llm.commentary._registry_prompt / llm.chart_insight._registry_prompt.
# ---------------------------------------------------------------------------
_RESEARCH_SYSTEM_PROMPT = (
    "You are a careful equity research assistant producing a qualitative "
    "research brief. Your output MUST conform exactly to the provided "
    "structured schema. Synthesize ONLY the news headlines, earnings date, "
    "and macro context supplied in the user prompt — you MUST NOT invent "
    "any catalyst, headline, number, or date that is not present in the "
    "supplied grounding packet. If the grounding packet is sparse or empty, "
    "reflect that honestly via a low data_confidence and a short "
    "sources_note rather than fabricating content. This is advisory "
    "research only — you are not authorising any trade."
)


def _registry_prompt(prompt_id: str, default: str) -> str:
    """Mirror :func:`llm.commentary._registry_prompt` for prompt-registry overrides."""
    if not getattr(settings, "PROMPT_REGISTRY_ENABLED", False):
        return default
    try:
        from prompt_registry import get_registry  # noqa: PLC0415

        body = get_registry().get(prompt_id)
        if isinstance(body, str) and body.strip():
            return body
    except Exception as exc:
        logger.debug("research: registry lookup for %s failed: %s", prompt_id, exc)
    return default


# ---------------------------------------------------------------------------
# Grounding — real retrieved data only (CONSTRAINT #4)
# ---------------------------------------------------------------------------


def _gather_grounding(symbol: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble a grounding packet of REAL retrieved data for ``symbol``.

    Returns a dict shaped ``{"headlines": list[str], "next_earnings":
    Optional[str] (ISO date), "macro_snippet": Optional[str]}``. Degrades to
    the empty shape on any failure (missing ``FINNHUB_API_KEY``, network
    error, missing ``finnhub-python`` package) — never invents grounding
    data (CONSTRAINT #4 + #6).
    """
    packet: Dict[str, Any] = {"headlines": [], "next_earnings": None, "macro_snippet": None}
    try:
        from signals.news_catalyst import (  # noqa: PLC0415
            build_finnhub_client,
            fetch_company_news,
            fetch_next_earnings,
        )

        client = build_finnhub_client()
        if client is not None:
            lookback = int(getattr(settings, "NEWS_LOOKBACK_DAYS", 7) or 7)
            try:
                news_items = fetch_company_news(client, symbol, lookback)
                packet["headlines"] = [
                    str(item.get("headline", "")).strip()
                    for item in news_items
                    if item.get("headline")
                ][:8]
            except Exception as exc:
                logger.debug(
                    "research: _gather_grounding news fetch failed for %s: %s", symbol, exc
                )
            try:
                next_earnings = fetch_next_earnings(client, symbol)
                if next_earnings is not None:
                    packet["next_earnings"] = next_earnings.date().isoformat()
            except Exception as exc:
                logger.debug(
                    "research: _gather_grounding earnings fetch failed for %s: %s", symbol, exc
                )
    except Exception as exc:
        logger.debug(
            "research: _gather_grounding Finnhub client unavailable for %s: %s", symbol, exc
        )

    if isinstance(context, dict):
        macro = context.get("macro_snippet") or context.get("market_regime")
        if macro:
            packet["macro_snippet"] = str(macro)

    return packet


def _format_grounding_user_prompt(symbol: str, packet: Dict[str, Any]) -> str:
    """Render the user-turn prompt from a grounding packet."""
    lines = [f"Symbol: {symbol}"]
    headlines = packet.get("headlines") or []
    if headlines:
        lines.append("Recent headlines (real, retrieved from Finnhub):")
        for headline in headlines:
            lines.append(f"  - {headline}")
    else:
        lines.append("Recent headlines: none retrieved.")
    next_earnings = packet.get("next_earnings")
    lines.append(f"Next earnings date: {next_earnings or 'unknown'}")
    macro_snippet = packet.get("macro_snippet")
    if macro_snippet:
        lines.append(f"Macro context: {macro_snippet}")
    lines.append(
        "\nSynthesize the structured research brief from ONLY the above. "
        "If little or no data is available, reflect that in data_confidence "
        "and sources_note — do not invent content."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider plumbing
# ---------------------------------------------------------------------------


def _get_default_provider():
    """Resolve the configured Opal provider via the router.

    Lazy-imported at call time (not module top) so ``llm/research.py`` never
    reaches for the ``openai`` SDK when Opal is disabled — the router itself
    only constructs :class:`llm.providers.OpenAIProvider` when
    ``OPAL_RESEARCH_ENABLED`` is True and ``OPENAI_API_KEY`` is set.
    """
    try:
        from llm.router import get_research_provider  # noqa: PLC0415

        return get_research_provider()
    except Exception as exc:
        logger.warning("research: get_research_provider failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_research_brief(
    symbol: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    provider=None,
    grounding_fn: Optional[Callable[[str, Optional[Dict[str, Any]]], Dict[str, Any]]] = None,
) -> Optional[ResearchBrief]:
    """Return a grounded, qualitative :class:`ResearchBrief` for ``symbol``.

    Parameters
    ----------
    symbol :
        Ticker (case-insensitive; normalized to uppercase).
    context :
        Optional extra payload (e.g. ``{"macro_snippet": "..."}``) folded
        into the grounding packet. Never required.
    provider :
        Optional pre-constructed provider (test seam). Must implement
        ``call_structured(system, user, schema_model)``. Defaults to
        :func:`llm.router.get_research_provider`.
    grounding_fn :
        Optional override for :func:`_gather_grounding` (test seam so tests
        never hit real Finnhub). Receives ``(symbol, context)`` and must
        return the grounding-packet dict shape.

    Returns
    -------
    Optional[ResearchBrief]
        Schema-validated structured output, or ``None`` when
        ``OPAL_RESEARCH_ENABLED`` is False, the symbol is empty, no provider
        is configured, the call failed, or the response was malformed.
        Callers MUST treat ``None`` as "Opal unavailable this cycle"
        (CONSTRAINT #6) — never block on it.
    """
    try:
        if not getattr(settings, "OPAL_RESEARCH_ENABLED", False):
            return None
        sym = (symbol or "").upper().strip()
        if not sym:
            return None

        cache_key = make_cache_key(
            provider="openai",
            schema_name=ResearchBrief.__name__,
            symbol=sym,
            score=0.0,
            action="RESEARCH",
        )
        cached = cache_get(cache_key)
        if cached is not None:
            try:
                return ResearchBrief.model_validate(cached)
            except Exception:
                # Corrupt cache entry — fall through to re-fetch.
                pass

        gather = grounding_fn if grounding_fn is not None else _gather_grounding
        packet = gather(sym, context)

        prov = provider if provider is not None else _get_default_provider()
        if prov is None:
            return None

        system = _registry_prompt("llm.research.system", _RESEARCH_SYSTEM_PROMPT)
        user = _format_grounding_user_prompt(sym, packet)
        result = prov.call_structured(system=system, user=user, schema_model=ResearchBrief)
        if result is None:
            return None

        try:
            cache_put(
                cache_key,
                result.model_dump(),
                meta={"provider": getattr(prov, "name", "?"), "symbol": sym},
            )
        except Exception as exc:
            logger.debug("research: cache_put failed (non-fatal): %s", exc)
        return result
    except Exception as exc:
        logger.warning("generate_research_brief failed unexpectedly: %s", exc)
        return None
