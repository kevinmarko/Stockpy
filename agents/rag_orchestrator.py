"""
agents/rag_orchestrator.py
==========================
LangGraph workflow for portfolio-aware sentiment analysis with Qdrant RAG.

Architecture (3 nodes):
  1. portfolio_context — queries SQLite account_positions for held symbols
  2. retrieve          — embeds the query with a sentence transformer and
                         queries the Qdrant vector store
  3. relevance_filter  — drops docs whose relevance_score (Qdrant vector
                         similarity to the query) < 0.5. NOTE: this is a
                         semantic-relevance threshold, not a source-
                         credibility/trustworthiness measure — Qdrant's
                         search score has no notion of how trustworthy a
                         source is, only how similar its embedding is to the
                         query. A real credibility check (source reputation,
                         heuristics + LLM validation) is a separate, not-yet-
                         built feature; don't relabel this filter as that.
  4. generate           — calls the active LLM provider via llm/router.py

Graceful degradation: if langgraph or qdrant_client are not installed the
``build_graph()`` function logs a warning and returns None, so callers can
check for None and fall back to the non-RAG path without crashing.

NOT YET WIRED INTO ANY PRODUCTION CALLER. ``run_rag_query()`` is invoked
only by this module's own ``__main__`` block -- no API endpoint, MCP tool,
or pipeline step calls it. The four node functions above are individually
correct and tested, but exposing this as a real feature (an API endpoint on
which service? an MCP tool? what auth/rate-limit gate, given it calls a
paid LLM provider exactly like api/data_api.py's /api/chat and /data/ai/*
endpoints do?) is a product-surface decision this fix pass does not make
unilaterally -- see api/data_api.py's ``_require_ai_generation_enabled``
docstring for why that gate matters for any new paid-LLM-calling surface.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel
from settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional heavy deps — degrade gracefully
# ---------------------------------------------------------------------------
try:
    from langgraph.graph import StateGraph, END  # type: ignore
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    StateGraph = None  # type: ignore[assignment, misc]
    END = "__end__"
    _LANGGRAPH_AVAILABLE = False
    logger.warning("langgraph not installed — RAGOrchestrator will not compile. pip install langgraph")

try:
    from qdrant_client import QdrantClient  # type: ignore
    from qdrant_client.http.models import PointStruct  # type: ignore
    _QDRANT_AVAILABLE = True
except ImportError:
    QdrantClient = None  # type: ignore[assignment, misc]
    _QDRANT_AVAILABLE = False
    logger.warning("qdrant_client not installed — RAG retrieval will be empty. pip install qdrant-client")


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class RAGState(TypedDict):
    query: str
    portfolio_context: List[str]
    retrieved_docs: List[Dict[str, Any]]
    relevant_docs: List[Dict[str, Any]]
    final_analysis: str


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def fetch_portfolio_context(state: RAGState) -> Dict[str, Any]:
    """Queries the account_positions table for currently held symbols, from
    the MOST RECENT account_snapshots row only.

    Routed through db_config.create_readonly_db_engine() (settings.DATABASE_URL,
    with the SQLite/Postgres dual-backend handling and database-level read-only
    enforcement db_config.py already provides) rather than a hand-rolled
    sqlite3.connect() against os.environ — this codebase's settings.py, loaded
    via pydantic-settings' env_file, does not populate real os.environ, so an
    operator whose only source for DATABASE_URL is .env would otherwise be
    silently routed to the wrong DB file with no error.

    account_positions rows are linked to a snapshot_id FK and one row is
    inserted per position per snapshot (data/historical_store.py); querying
    without a snapshot_id filter would return every historical snapshot's
    positions at once (closed positions, duplicate qty per symbol across
    fetches). Filtered to the latest snapshot_id, mirroring
    HistoricalStore.latest_account_snapshot()'s own two-query pattern
    (ORDER BY fetched_at DESC LIMIT 1, then WHERE snapshot_id = ?) rather
    than importing that heavier method (it reconstructs full
    AccountSnapshot/PortfolioPosition dataclasses via data.robinhood_portfolio,
    which pulls in the robin_stocks client library this read-only node has no
    need for).
    """
    held: List[str] = []
    try:
        from sqlalchemy import text
        from db_config import create_readonly_db_engine

        engine = create_readonly_db_engine()
        with engine.connect() as conn:
            latest = conn.execute(
                text("SELECT snapshot_id FROM account_snapshots ORDER BY fetched_at DESC LIMIT 1")
            ).fetchone()
            if latest is not None:
                rows = conn.execute(
                    text(
                        "SELECT symbol, qty FROM account_positions "
                        "WHERE snapshot_id = :snapshot_id AND qty != 0"
                    ),
                    {"snapshot_id": latest[0]},
                ).fetchall()
                held = [f"{r[0]} (qty={r[1]})" for r in rows]
    except Exception as exc:
        logger.warning("fetch_portfolio_context: DB query failed: %s", exc)
    return {"portfolio_context": held}


def retrieve_documents(state: RAGState) -> Dict[str, Any]:
    """Queries Qdrant for documents relevant to the query.

    Falls back to an empty list when Qdrant is unavailable, so the rest of the
    pipeline continues without crashing.
    """
    if not _QDRANT_AVAILABLE or QdrantClient is None:
        return {"retrieved_docs": []}

    try:
        qdrant_url = settings.QDRANT_URL
        collection = settings.QDRANT_COLLECTION
        client = QdrantClient(url=qdrant_url, timeout=5)

        # Dense embedding via a sentence transformer -- MUST be the same
        # model that populated the collection (all-MiniLM-L6-v2), or the
        # query vector and the indexed vectors don't live in a comparable
        # embedding space. A random vector "fallback" does not degrade
        # gracefully: cosine similarity against a random query vector is
        # semantically arbitrary, so it would retrieve unrelated documents
        # and hand them to generate_analysis as if they were real evidence.
        # No documents is the honest degrade here (CONSTRAINT #4), matching
        # every other "optional dependency missing" path in this codebase
        # (e.g. data/rag_index.py's own faiss-absent no-op).
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError:
            logger.warning(
                "retrieve_documents: sentence-transformers not installed — "
                "returning no documents rather than querying with a "
                "meaningless random vector. pip install sentence-transformers"
            )
            return {"retrieved_docs": []}

        model = SentenceTransformer("all-MiniLM-L6-v2")
        query_vector = model.encode(state["query"]).tolist()

        hits = client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=10,
            with_payload=True,
        )
        docs = [
            {
                "title": h.payload.get("title", ""),
                "content": h.payload.get("content", ""),
                # Qdrant vector-similarity score -- semantic RELEVANCE to the
                # query, not a measure of source trustworthiness/credibility.
                # See relevance_filter below; don't rename this back to
                # "source_score"/"credibility" without a real credibility
                # signal to back it.
                "relevance_score": float(h.score),
                "ticker": h.payload.get("ticker", ""),
            }
            for h in hits
        ]
        return {"retrieved_docs": docs}

    except Exception as exc:
        logger.error("retrieve_documents: Qdrant query failed: %s", exc)
        return {"retrieved_docs": []}


def relevance_filter(state: RAGState) -> Dict[str, Any]:
    """Drop documents with relevance_score < 0.5 (semantic relevance
    threshold — NOT a credibility/trustworthiness gate; see module
    docstring)."""
    relevant = [d for d in state.get("retrieved_docs", []) if d.get("relevance_score", 0) >= 0.5]
    logger.info(
        "relevance_filter: kept %d/%d docs",
        len(relevant), len(state.get("retrieved_docs", [])),
    )
    return {"relevant_docs": relevant}


class _RAGAnalysisOutput(BaseModel):
    """Structured output schema for generate_analysis's call_structured()
    call -- this codebase's LLMProvider interface has no free-text
    ``generate(prompt)`` method (llm/providers.py::LLMProvider defines only
    ``call_structured(system, user, schema_model) -> Optional[BaseModel]``,
    the same contract every other LLM call site in this codebase uses), so a
    minimal one-field schema is the correct way to get a text narrative back."""

    analysis: str


def generate_analysis(state: RAGState) -> Dict[str, Any]:
    """Generates a final narrative using the active LLM provider.

    Uses get_rationale_provider() (LLM_COMMENTARY_ENABLED +
    LLM_COMMENTARY_RATIONALE_PROVIDER) -- llm/router.py has no generic
    provider selector; of the three job-specific selectors it exposes
    (rationale/alert/research), rationale generation is the closest match to
    "explain what's happening and why" narrative synthesis. This RAG node
    does not have its own settings flag, so it inherits that job's gate.
    """
    relevant = state.get("relevant_docs", [])
    portfolio = state.get("portfolio_context", [])

    context_block = "\n".join(
        f"- [{d['ticker']}] {d['title']}: {d['content'][:200]}" for d in relevant
    )
    portfolio_block = ", ".join(portfolio) if portfolio else "no open positions"

    system = (
        "You are a portfolio-aware financial news analyst. Provide a concise, "
        "evidence-backed analysis grounded strictly in the supplied context. "
        "Do not fabricate data or cite sources not provided."
    )
    user = (
        f"Portfolio: {portfolio_block}\n\n"
        f"Relevant news (relevance-filtered):\n{context_block or 'No relevant news found.'}\n\n"
        f"Query: {state['query']}"
    )

    analysis = ""
    try:
        from llm.router import get_rationale_provider
        provider = get_rationale_provider()
        if provider is not None:
            result = provider.call_structured(system, user, _RAGAnalysisOutput)
            analysis = result.analysis if result is not None else "(LLM returned no usable response)"
        else:
            analysis = "(No LLM provider configured — set LLM_COMMENTARY_ENABLED and an API key)"
    except Exception as exc:
        logger.error("generate_analysis: LLM call failed: %s", exc)
        analysis = "(LLM error — see server logs)"

    return {"final_analysis": analysis}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph():
    """Compile and return the LangGraph workflow, or None if unavailable."""
    if not _LANGGRAPH_AVAILABLE or StateGraph is None:
        return None

    workflow = StateGraph(RAGState)
    workflow.add_node("portfolio_context", fetch_portfolio_context)
    workflow.add_node("retrieve", retrieve_documents)
    workflow.add_node("relevance_filter", relevance_filter)
    workflow.add_node("generate", generate_analysis)

    workflow.set_entry_point("portfolio_context")
    workflow.add_edge("portfolio_context", "retrieve")
    workflow.add_edge("retrieve", "relevance_filter")
    workflow.add_edge("relevance_filter", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()


def run_rag_query(query: str) -> str:
    """Convenience wrapper — builds the graph and runs a single query.

    Returns the ``final_analysis`` string, or an empty string on failure.
    Dead-letter safe: exceptions are caught and logged.
    """
    try:
        app = build_graph()
        if app is None:
            return "(RAG unavailable — langgraph not installed)"
        result = app.invoke({"query": query, "portfolio_context": [], "retrieved_docs": [], "relevant_docs": [], "final_analysis": ""})
        return result.get("final_analysis", "")
    except Exception as exc:
        logger.error("run_rag_query failed: %s", exc)
        return ""


if __name__ == "__main__":
    print(run_rag_query("What are the main risks for my portfolio this week?"))
