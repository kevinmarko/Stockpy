"""
data/rag_index.py — Embedded FAISS Vector Store (Phase 2 PR3)
================================================================
Indexes the ALREADY-INGESTED sentiment corpus
(``sentiment_ingestion_audit`` — see ``data/historical_store.py`` and
``data/sentiment_sources.py``) into a local, embedded FAISS index so
:mod:`engine.portfolio_context` can retrieve relevant documents for a
portfolio-context note. This is deliberately NOT a second ingestion
pipeline — it only reads rows another module already wrote.

Design decision: embedded FAISS, not a server-mode vector DB
--------------------------------------------------------------
This is a single-operator desktop app; a Qdrant/Docker-style server-mode
vector database is the wrong fit. ``faiss-cpu`` (an embedded, in-process
library) plus a plain SQLite side-table (``rag_indexed_docs`` in
``data/historical_store.py``) for metadata tracking mirrors this
codebase's existing ``tensorflow`` opt-in-heavy-dependency precedent:
listed only in ``requirements-optional.txt``, lazy-imported INSIDE every
method (not at module top) so this module stays importable — and the
whole RAG feature stays a no-op — when ``faiss-cpu`` isn't installed.

Persistence
-----------
The FAISS index is persisted to ``output/rag_index/index.faiss``. A
``faiss.IndexIDMap`` wraps a cosine-similarity ``faiss.IndexFlatIP``
(vectors are L2-normalized before insertion, so inner product ==
cosine similarity); the ID assigned to each vector is the document's
own ``sentiment_ingestion_audit.ingest_id``, which lets
:meth:`DocumentVectorStore.search` map a FAISS result directly back to
its source row without an extra lookup, and lets
:meth:`DocumentVectorStore._evict_if_needed` remove specific vectors by
ID (``IndexIDMap.remove_ids``) for FIFO eviction against
``settings.RAG_INDEX_MAX_DOCUMENTS``.

Resilience (CONSTRAINT #6)
---------------------------
Every public method wraps its body in try/except. A missing ``faiss``
install, a corrupt on-disk index, an embedding-provider failure, or any
per-document error degrades to a logged WARNING and a safe empty/zero
return — never raises, never aborts an in-progress batch for one bad
document.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_INDEX_PATH = os.path.join("output", "rag_index", "index.faiss")


@dataclass(frozen=True)
class IndexedDocument:
    """One retrieved document, hydrated from ``sentiment_ingestion_audit``."""

    ingest_id: int
    symbol: str
    source: str
    text: str
    as_of: str
    score: float


def _doc_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _faiss_available() -> bool:
    """True iff ``faiss`` can be imported. Never raises."""
    try:
        import faiss  # noqa: F401,PLC0415
        return True
    except Exception:
        return False


class DocumentVectorStore:
    """Embedded FAISS-backed vector store over the sentiment ingestion corpus.

    Every method lazy-imports ``faiss`` internally and degrades gracefully
    (no-op / ``0`` / ``[]`` / ``None``) when the package is unavailable —
    this class is always constructible and always importable regardless of
    whether ``faiss-cpu`` is installed.
    """

    def __init__(
        self,
        *,
        index_path: str = _DEFAULT_INDEX_PATH,
        store: Optional[Any] = None,
        embedding_provider: Optional[Any] = None,
    ) -> None:
        self._index_path = index_path
        self._store = store
        self._embedding_provider = embedding_provider
        self._index: Optional[Any] = None  # faiss.IndexIDMap, lazily created/loaded

    # ─────────────────────────────────────────────────────────────────────
    # Dependency resolution
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_store(self) -> Any:
        if self._store is not None:
            return self._store
        from data.historical_store import HistoricalStore  # noqa: PLC0415
        self._store = HistoricalStore()
        return self._store

    def _resolve_embedding_provider(self) -> Optional[Any]:
        if self._embedding_provider is not None:
            return self._embedding_provider
        try:
            from llm.router import get_embedding_provider  # noqa: PLC0415
            return get_embedding_provider()
        except Exception as exc:
            logger.debug("DocumentVectorStore: could not resolve embedding provider: %s", exc)
            return None

    # ─────────────────────────────────────────────────────────────────────
    # Index load/save/create
    # ─────────────────────────────────────────────────────────────────────

    def _get_or_create_index(self, dim: int) -> Optional[Any]:
        """Return the live ``faiss.IndexIDMap``, loading from disk or creating fresh.

        Returns ``None`` when ``faiss`` is unavailable (checked by the
        caller first in every public method, but re-checked here too since
        this is also reachable from :meth:`search`).
        """
        import faiss  # noqa: PLC0415

        if self._index is not None:
            return self._index

        if os.path.exists(self._index_path):
            try:
                self._index = faiss.read_index(self._index_path)
                logger.debug(
                    "DocumentVectorStore: loaded existing index from %s (%d vectors).",
                    self._index_path, self._index.ntotal,
                )
                return self._index
            except Exception as exc:
                logger.warning(
                    "DocumentVectorStore: failed to read existing index at %s (%s); "
                    "creating a fresh one.", self._index_path, exc,
                )

        base = faiss.IndexFlatIP(dim)
        self._index = faiss.IndexIDMap(base)
        return self._index

    def _save_index(self) -> None:
        import faiss  # noqa: PLC0415

        if self._index is None:
            return
        try:
            os.makedirs(os.path.dirname(self._index_path) or ".", exist_ok=True)
            faiss.write_index(self._index, self._index_path)
        except Exception as exc:
            logger.warning("DocumentVectorStore: failed to persist index: %s", exc)

    @staticmethod
    def _normalize(vec: List[float]):
        import numpy as np  # noqa: PLC0415

        arr = np.asarray(vec, dtype="float32")
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        return arr

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def index_new_documents(self, since: datetime) -> int:
        """Embed and index every unindexed ``sentiment_ingestion_audit`` row
        published at/after ``since``.

        Best-effort PER DOCUMENT: any single embedding or index-insertion
        failure is logged and that document is skipped — it never aborts
        the rest of the batch. Applies FIFO eviction against
        ``settings.RAG_INDEX_MAX_DOCUMENTS`` after indexing. Returns the
        count of NEWLY indexed documents (``0`` on total failure or when
        ``faiss``/an embedding provider is unavailable — CONSTRAINT #6).
        """
        if not _faiss_available():
            logger.info(
                "DocumentVectorStore.index_new_documents: faiss not installed; no-op."
            )
            return 0
        try:
            store = self._resolve_store()
            rows = store.get_unindexed_sentiment_documents(since)
            if not rows:
                return 0

            provider = self._resolve_embedding_provider()
            if provider is None:
                logger.info(
                    "DocumentVectorStore.index_new_documents: no embedding provider "
                    "configured/available; no-op (%d pending documents).", len(rows),
                )
                return 0

            indexed_count = 0
            index_dim: Optional[int] = None
            for row in rows:
                try:
                    text = row.get("text_content") or ""
                    if not text.strip():
                        continue
                    embedding_batch = provider.embed_texts([text])
                    if not embedding_batch:
                        continue
                    vec = self._normalize(embedding_batch[0])

                    if self._index is None:
                        index_dim = index_dim or len(vec)
                        idx = self._get_or_create_index(index_dim)
                        if idx is None:
                            return indexed_count
                    idx = self._index
                    if idx.d != len(vec):
                        logger.warning(
                            "DocumentVectorStore: embedding dim %d does not match "
                            "index dim %d; skipping ingest_id=%s.",
                            len(vec), idx.d, row.get("ingest_id"),
                        )
                        continue

                    import numpy as np  # noqa: PLC0415
                    ingest_id = int(row["ingest_id"])
                    idx.add_with_ids(
                        vec.reshape(1, -1), np.array([ingest_id], dtype="int64")
                    )
                    store.record_rag_indexed_doc(
                        ingest_id=ingest_id,
                        doc_hash=_doc_hash(text),
                        faiss_row=ingest_id,
                        indexed_at=datetime.now(timezone.utc).isoformat(),
                    )
                    indexed_count += 1
                except Exception as exc:
                    logger.warning(
                        "DocumentVectorStore.index_new_documents: skipping "
                        "ingest_id=%s due to error: %s", row.get("ingest_id"), exc,
                    )
                    continue

            if indexed_count > 0:
                self._evict_if_needed(store)
                self._save_index()
            return indexed_count
        except Exception as exc:
            logger.warning("DocumentVectorStore.index_new_documents failed: %s", exc)
            return 0

    def search(self, query_embedding: List[float], k: int = 5) -> List[IndexedDocument]:
        """Return the ``k`` nearest indexed documents to ``query_embedding``.

        Returns ``[]`` on any failure — including ``faiss`` unavailable, no
        index built yet, or an empty/None query embedding (CONSTRAINT #6).
        """
        if not _faiss_available():
            return []
        if not query_embedding:
            return []
        try:
            import numpy as np  # noqa: PLC0415

            idx = self._get_or_create_index(len(query_embedding))
            if idx is None or idx.ntotal == 0:
                return []
            if idx.d != len(query_embedding):
                logger.warning(
                    "DocumentVectorStore.search: query embedding dim %d does not "
                    "match index dim %d.", len(query_embedding), idx.d,
                )
                return []

            vec = self._normalize(query_embedding).reshape(1, -1)
            k = max(1, int(k))
            scores, ids = idx.search(vec, min(k, idx.ntotal))

            id_score: Dict[int, float] = {
                int(i): float(s) for i, s in zip(ids[0], scores[0]) if int(i) != -1
            }
            if not id_score:
                return []

            store = self._resolve_store()
            rows = store.get_sentiment_documents_by_ingest_ids(list(id_score.keys()))

            results = [
                IndexedDocument(
                    ingest_id=int(row["ingest_id"]),
                    symbol=str(row.get("symbol") or ""),
                    source=str(row.get("source_name") or ""),
                    text=str(row.get("text_content") or ""),
                    as_of=str(row.get("as_of") or ""),
                    score=id_score.get(int(row["ingest_id"]), 0.0),
                )
                for row in rows
            ]
            results.sort(key=lambda d: d.score, reverse=True)
            return results
        except Exception as exc:
            logger.warning("DocumentVectorStore.search failed: %s", exc)
            return []

    # ─────────────────────────────────────────────────────────────────────
    # Eviction
    # ─────────────────────────────────────────────────────────────────────

    def _evict_if_needed(self, store: Any) -> None:
        """FIFO-evict oldest indexed documents past ``RAG_INDEX_MAX_DOCUMENTS``.

        Never raises (CONSTRAINT #6) — an eviction failure just means the
        index grows past the configured cap until the next successful run.
        """
        try:
            from settings import settings  # noqa: PLC0415

            max_docs = int(getattr(settings, "RAG_INDEX_MAX_DOCUMENTS", 5000) or 5000)
            count = store.get_rag_indexed_doc_count()
            if count <= max_docs:
                return
            n_evict = count - max_docs
            oldest = store.get_oldest_rag_indexed_docs(n_evict)
            if not oldest:
                return

            import numpy as np  # noqa: PLC0415

            faiss_rows = [row[1] for row in oldest]
            if self._index is not None:
                self._index.remove_ids(np.array(faiss_rows, dtype="int64"))
            store.delete_rag_indexed_docs([row[0] for row in oldest])
            logger.info(
                "DocumentVectorStore: evicted %d oldest documents (cap=%d).",
                len(oldest), max_docs,
            )
        except Exception as exc:
            logger.warning("DocumentVectorStore._evict_if_needed failed: %s", exc)
