"""
agents/rag_orchestrator.py
==========================
LangGraph workflow for portfolio-aware sentiment analysis with Qdrant RAG.

Architecture (3 nodes):
  1. portfolio_context  — queries SQLite account_positions for held symbols
  2. retrieve           — embeds the query with a sentence transformer and
                          queries the Qdrant vector store
  3. credibility_filter — drops docs whose source_score < 0.5
  4. generate           — calls the active LLM provider via llm/router.py

Graceful degradation: if langgraph or qdrant_client are not installed the
``build_graph()`` function logs a warning and returns None, so callers can
check for None and fall back to the non-RAG path without crashing.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, TypedDict

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
    credible_docs: List[Dict[str, Any]]
    final_analysis: str


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def fetch_portfolio_context(state: RAGState) -> Dict[str, Any]:
    """Queries SQLite account_positions table for currently held symbols."""
    held: List[str] = []
    try:
        import sqlite3
        db_path = os.environ.get("DATABASE_URL", "quant_platform.db")
        if db_path.startswith("sqlite:///"):
            db_path = db_path[len("sqlite:///"):]
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT symbol, qty FROM account_positions WHERE qty != 0"
            ).fetchall()
            conn.close()
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
        qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        collection = os.environ.get("QDRANT_COLLECTION", "investyo_news")
        client = QdrantClient(url=qdrant_url, timeout=5)

        # Simple dense embedding via a sentence transformer
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            model = SentenceTransformer("all-MiniLM-L6-v2")
            query_vector = model.encode(state["query"]).tolist()
        except ImportError:
            # Fall back to a random vector if sentence-transformers not installed
            import random
            query_vector = [random.random() for _ in range(384)]

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
                "source_score": float(h.score),
                "ticker": h.payload.get("ticker", ""),
            }
            for h in hits
        ]
        return {"retrieved_docs": docs}

    except Exception as exc:
        logger.error("retrieve_documents: Qdrant query failed: %s", exc)
        return {"retrieved_docs": []}


def credibility_filter(state: RAGState) -> Dict[str, Any]:
    """Drop documents with source_score < 0.5 (heuristic credibility gate)."""
    credible = [d for d in state.get("retrieved_docs", []) if d.get("source_score", 0) >= 0.5]
    logger.info(
        "credibility_filter: kept %d/%d docs",
        len(credible), len(state.get("retrieved_docs", [])),
    )
    return {"credible_docs": credible}


def generate_analysis(state: RAGState) -> Dict[str, Any]:
    """Generates a final narrative using the active LLM provider."""
    credible = state.get("credible_docs", [])
    portfolio = state.get("portfolio_context", [])

    context_block = "\n".join(
        f"- [{d['ticker']}] {d['title']}: {d['content'][:200]}" for d in credible
    )
    portfolio_block = ", ".join(portfolio) if portfolio else "no open positions"

    prompt = (
        f"Portfolio: {portfolio_block}\n\n"
        f"Relevant news (credibility-filtered):\n{context_block or 'No relevant news found.'}\n\n"
        f"Query: {state['query']}\n\n"
        "Provide a concise, evidence-backed analysis. Do not fabricate data."
    )

    analysis = ""
    try:
        from llm.router import get_provider
        provider = get_provider()
        if provider is not None:
            result = provider.generate(prompt)
            analysis = result or "(LLM returned empty response)"
        else:
            analysis = "(No LLM provider configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY)"
    except Exception as exc:
        logger.error("generate_analysis: LLM call failed: %s", exc)
        analysis = f"(LLM error: {exc})"

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
    workflow.add_node("credibility_filter", credibility_filter)
    workflow.add_node("generate", generate_analysis)

    workflow.set_entry_point("portfolio_context")
    workflow.add_edge("portfolio_context", "retrieve")
    workflow.add_edge("retrieve", "credibility_filter")
    workflow.add_edge("credibility_filter", "generate")
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
        result = app.invoke({"query": query, "portfolio_context": [], "retrieved_docs": [], "credible_docs": [], "final_analysis": ""})
        return result.get("final_analysis", "")
    except Exception as exc:
        logger.error("run_rag_query failed: %s", exc)
        return ""


if __name__ == "__main__":
    print(run_rag_query("What are the main risks for my portfolio this week?"))
