"""
engine/portfolio_context.py — RAG-Powered Portfolio Contextualizer (Phase 2 PR3)
===================================================================================
Combines the deterministic sector-exposure classifier
(:mod:`engine.portfolio_exposure`) with best-effort retrieval over the
already-ingested sentiment corpus (:mod:`data.rag_index`) and an optional
LLM synthesis call (:mod:`llm.providers` via :mod:`llm.router`) into one
:class:`PortfolioContextResult`.

Template-survives contract (CONSTRAINT #6)
--------------------------------------------
Modeled directly on :func:`engine.advisory.enrich_with_llm_rationale`:

1. The deterministic exposure summary (:func:`engine.portfolio_exposure.
   compute_sector_exposure`) is a PURE function with no failure mode and is
   ALWAYS returned.
2. Retrieval (embedding the query, searching the FAISS index) is
   best-effort — any failure (feature flag off, no provider configured,
   ``faiss`` not installed, no index built yet, embedding call failed)
   degrades to an empty document list and the function CONTINUES.
3. The final LLM synthesis call is likewise best-effort — any failure
   degrades ``context_note`` to ``None``.

This function NEVER raises. When ``settings.RAG_PORTFOLIO_CONTEXT_ENABLED``
is ``False`` (the default), NO retrieval, NO embedding call, and NO LLM
call is attempted — only the pure exposure summary is computed.

Point-in-time safety
---------------------
Retrieved documents are filtered to ``as_of <= now`` before being cited in
the LLM prompt — a document whose ``as_of`` timestamp is in the future
relative to ``now`` (a data-quality anomaly, or a clock-skewed write) can
never leak into "today's" contextual note (CONSTRAINT #4 lookahead
discipline, mirrors ``sentiment_ingestion_audit``'s own trading-day roll).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.portfolio_exposure import SectorExposure, compute_sector_exposure

logger = logging.getLogger(__name__)

_MAX_RETRIEVED_SNIPPET_CHARS = 280
_MAX_RETRIEVED_DOCS_IN_PROMPT = 10

_SYSTEM_PROMPT = (
    "You are a careful portfolio-risk assistant producing a qualitative "
    "context note about a portfolio's sector concentration. Your output "
    "MUST conform exactly to the provided structured schema. Synthesize "
    "ONLY the sector-exposure figures and retrieved document snippets "
    "supplied in the user prompt — you MUST NOT invent any statistic, "
    "percentage, headline, or source not present in the supplied packet. "
    "If few or no documents were retrieved, reflect that honestly with a "
    "neutral tailwind_or_headwind and a rationale that says so, rather "
    "than fabricating supporting evidence. This is advisory context only "
    "— you are not authorising any trade."
)


@dataclass(frozen=True)
class PortfolioContextResult:
    """Result of :func:`generate_portfolio_context_note`.

    ``sector_exposure`` / ``total_equity`` are the deterministic, always-
    present exposure summary. ``context_note`` is the optional LLM
    enrichment — ``None`` when the feature is disabled, unconfigured, or
    any step in the retrieval/LLM chain failed.
    """

    sector_exposure: Dict[str, SectorExposure]
    total_equity: float
    context_note: Optional[Any] = None  # llm.schemas.PortfolioContextNote when present
    retrieved_document_count: int = 0
    retrieved_symbols: List[str] = field(default_factory=list)


def _build_query_text(sector_exposure: Dict[str, SectorExposure]) -> str:
    """Render a short natural-language query summarizing the exposure mix.

    Used as the retrieval query embedded via the configured embedding
    provider — sorted by descending |pct_of_equity| so the dominant
    concentration drives which documents get retrieved.
    """
    ranked = sorted(
        sector_exposure.values(), key=lambda s: abs(s.pct_of_equity), reverse=True
    )
    parts = [
        f"{s.sector} ({s.pct_of_equity * 100:.1f}% of equity, symbols: {', '.join(s.symbols)})"
        for s in ranked
    ]
    return "Current portfolio sector exposure: " + "; ".join(parts)


def _filter_pit_safe(documents: List[Any], now: datetime) -> List[Any]:
    """Drop any retrieved document whose ``as_of`` is after ``now`` or unparsable.

    Never raises — a document with a missing/malformed ``as_of`` is
    excluded (fails closed) rather than assumed safe.
    """
    import pandas as pd  # noqa: PLC0415

    safe: List[Any] = []
    for doc in documents:
        try:
            as_of_ts = pd.Timestamp(getattr(doc, "as_of", None))
            if as_of_ts.tzinfo is None:
                as_of_ts = as_of_ts.tz_localize("UTC")
            if as_of_ts.to_pydatetime() <= now:
                safe.append(doc)
        except Exception:
            continue  # unparsable as_of -> exclude, fail closed
    return safe


def _format_context_user_prompt(
    sector_exposure: Dict[str, SectorExposure],
    documents: List[Any],
) -> str:
    lines = ["Sector exposure breakdown:"]
    ranked = sorted(
        sector_exposure.values(), key=lambda s: abs(s.pct_of_equity), reverse=True
    )
    for s in ranked:
        lines.append(
            f"  - {s.sector}: {s.pct_of_equity * 100:.1f}% of equity "
            f"(net market value ${s.net_market_value:,.2f}; symbols: {', '.join(s.symbols)})"
        )
    if documents:
        lines.append("\nRetrieved context documents (real, from the ingested sentiment corpus):")
        for doc in documents[:_MAX_RETRIEVED_DOCS_IN_PROMPT]:
            snippet = str(getattr(doc, "text", ""))[:_MAX_RETRIEVED_SNIPPET_CHARS]
            lines.append(
                f"  - [{getattr(doc, 'symbol', '?')} / {getattr(doc, 'source', '?')} "
                f"/ {getattr(doc, 'as_of', '?')}] {snippet}"
            )
    else:
        lines.append("\nRetrieved context documents: none available.")
    lines.append(
        "\nSynthesize the structured portfolio context note from ONLY the above. "
        "Do not invent percentages, sectors, or sources not listed."
    )
    return "\n".join(lines)


def _registry_prompt(prompt_id: str, default: str) -> str:
    """Mirror :func:`llm.research._registry_prompt` for prompt-registry overrides."""
    try:
        from settings import settings  # noqa: PLC0415

        if not getattr(settings, "PROMPT_REGISTRY_ENABLED", False):
            return default
        from prompt_registry import get_registry  # noqa: PLC0415

        body = get_registry().get(prompt_id, default)
        if isinstance(body, str) and body.strip():
            return body
    except Exception as exc:
        logger.debug("portfolio_context: registry lookup for %s failed: %s", prompt_id, exc)
    return default


def generate_portfolio_context_note(
    snapshot: Any,
    market_signals: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
    vector_store: Optional[Any] = None,
    embedding_provider: Optional[Any] = None,
    llm_provider: Optional[Any] = None,
) -> PortfolioContextResult:
    """Return a :class:`PortfolioContextResult` for ``snapshot``.

    Parameters
    ----------
    snapshot:
        An ``AccountSnapshot`` (or any object exposing ``.positions`` /
        ``.total_equity`` compatible with :func:`engine.portfolio_exposure.
        compute_sector_exposure`).
    market_signals:
        Reserved for future use (e.g. threading in regime/VIX context
        alongside the exposure breakdown). Currently unused but accepted so
        callers (the MCP tool, future GUI panel) have a stable signature.
    now:
        Injectable "current time" for PIT-safety filtering and testing.
        Defaults to ``datetime.now(timezone.utc)``.
    vector_store, embedding_provider, llm_provider:
        Test/DI seams. Defaults resolve the real
        :class:`data.rag_index.DocumentVectorStore` and the configured
        providers via :mod:`llm.router`.

    Returns
    -------
    PortfolioContextResult
        ``sector_exposure`` / ``total_equity`` are ALWAYS populated (pure,
        no failure mode). ``context_note`` is ``None`` whenever
        ``settings.RAG_PORTFOLIO_CONTEXT_ENABLED`` is ``False`` or any step
        of retrieval/synthesis failed. Never raises.
    """
    from settings import settings  # noqa: PLC0415 — avoid import-time settings coupling

    total_equity = float(getattr(snapshot, "total_equity", 0.0) or 0.0)
    sector_exposure = compute_sector_exposure(snapshot)

    if not getattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", False):
        return PortfolioContextResult(
            sector_exposure=sector_exposure,
            total_equity=total_equity,
        )

    if not sector_exposure:
        # Nothing to contextualize (empty portfolio) — skip retrieval/LLM
        # entirely rather than generating a note about zero exposure.
        return PortfolioContextResult(
            sector_exposure=sector_exposure,
            total_equity=total_equity,
        )

    now_dt = now if now is not None else datetime.now(timezone.utc)

    retrieved: List[Any] = []
    try:
        emb_provider = (
            embedding_provider
            if embedding_provider is not None
            else _resolve_embedding_provider()
        )
        if emb_provider is not None:
            query_text = _build_query_text(sector_exposure)
            embedding_batch = emb_provider.embed_texts([query_text])
            if embedding_batch:
                store = vector_store if vector_store is not None else _resolve_vector_store()
                top_k = int(getattr(settings, "RAG_RETRIEVAL_TOP_K", 5) or 5)
                raw_hits = store.search(embedding_batch[0], k=top_k)
                retrieved = _filter_pit_safe(raw_hits, now_dt)
    except Exception as exc:
        logger.warning(
            "generate_portfolio_context_note: retrieval step soft-failed: %s", exc
        )
        retrieved = []

    context_note = None
    try:
        prov = llm_provider if llm_provider is not None else _resolve_llm_provider()
        if prov is not None:
            from llm.schemas import PortfolioContextNote  # noqa: PLC0415

            system = _registry_prompt("engine.portfolio_context.system", _SYSTEM_PROMPT)
            user = _format_context_user_prompt(sector_exposure, retrieved)
            context_note = prov.call_structured(
                system=system, user=user, schema_model=PortfolioContextNote
            )
    except Exception as exc:
        logger.warning(
            "generate_portfolio_context_note: LLM synthesis step soft-failed: %s", exc
        )
        context_note = None

    return PortfolioContextResult(
        sector_exposure=sector_exposure,
        total_equity=total_equity,
        context_note=context_note,
        retrieved_document_count=len(retrieved),
        retrieved_symbols=sorted({d.symbol for d in retrieved if getattr(d, "symbol", None)}),
    )


def _resolve_embedding_provider() -> Optional[Any]:
    try:
        from llm.router import get_embedding_provider  # noqa: PLC0415
        return get_embedding_provider()
    except Exception as exc:
        logger.debug("generate_portfolio_context_note: embedding provider unavailable: %s", exc)
        return None


def _resolve_llm_provider() -> Optional[Any]:
    try:
        from llm.router import get_portfolio_context_provider  # noqa: PLC0415
        return get_portfolio_context_provider()
    except Exception as exc:
        logger.debug("generate_portfolio_context_note: LLM provider unavailable: %s", exc)
        return None


def _resolve_vector_store() -> Any:
    from data.rag_index import DocumentVectorStore  # noqa: PLC0415
    return DocumentVectorStore()
