"""
tests/test_rag_index.py
========================
Unit tests for ``data.rag_index`` (Phase 2 PR3, 3b — Embedded FAISS Vector
Store).

Coverage
--------
* ``faiss`` import failure (forced via ``sys.modules["faiss"] = None``, which
  makes any ``import faiss`` raise ``ImportError`` regardless of whether the
  real package is actually installed in this environment): every
  ``DocumentVectorStore`` public method no-ops / returns ``None``/``[]``
  gracefully, never raises.
* When ``faiss`` IS actually installed (verified below): a real round-trip
  index → persist → reload → search over tiny synthetic embeddings, plus
  ``RAG_INDEX_MAX_DOCUMENTS`` FIFO eviction behavior.
* Per-document best-effort semantics: one bad embedding never aborts the
  rest of the batch.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from data.rag_index import DocumentVectorStore, IndexedDocument, _doc_hash, _faiss_available

# Availability check via find_spec(), NOT `import faiss`, deliberately.
# pytest imports every collected test module up front during collection
# (before any test in the whole suite runs) -- an eager module-level
# `import faiss` here would load faiss's bundled libomp.dylib into the
# process at collection time, well before the per-test bodies below get a
# chance to run. That collides with lightgbm's own (Homebrew-resolved)
# libomp.dylib the first time a *real* lightgbm model is unpickled
# elsewhere in the suite (ml.meta_bootstrap.bootstrap_meta_registry(),
# exercised by tests/test_advisory_pause_gate.py's real, non-mocked
# main.run_once()) and reliably segfaults the whole pytest process.
# find_spec() locates the module without executing it, so it is safe to
# call at collection time; the real `import faiss` still happens lazily,
# at test-execution time, inside data.rag_index's own methods (its
# established lazy-import convention) when TestRealFaissRoundTrip's tests
# actually run. See
# docs/known_issues/lightgbm_faiss_libomp_collision_segfault.md.
_FAISS_INSTALLED = importlib.util.find_spec("faiss") is not None


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRagStore:
    """In-memory stand-in for the ``HistoricalStore`` rag_indexed_docs API."""

    def __init__(self, documents):
        self._pending = {d["ingest_id"]: d for d in documents}
        self._indexed: dict = {}  # ingest_id -> (doc_hash, faiss_row) — insertion-ordered

    def get_unindexed_sentiment_documents(self, since):
        return [d for iid, d in self._pending.items() if iid not in self._indexed]

    def record_rag_indexed_doc(self, ingest_id, doc_hash, faiss_row, indexed_at=None):
        self._indexed[ingest_id] = (doc_hash, faiss_row)
        return True

    def get_rag_indexed_doc_count(self):
        return len(self._indexed)

    def get_oldest_rag_indexed_docs(self, n):
        # Insertion order == chronological order for this fake (deterministic,
        # unlike relying on real-clock ISO timestamps at test speed).
        items = list(self._indexed.items())[:n]
        return [(iid, meta[1]) for iid, meta in items]

    def delete_rag_indexed_docs(self, ingest_ids):
        for iid in ingest_ids:
            self._indexed.pop(iid, None)
        return True

    def get_sentiment_documents_by_ingest_ids(self, ingest_ids):
        return [self._pending[i] for i in ingest_ids if i in self._pending]


class FakeEmbeddingProvider:
    """Deterministic embed_texts(): looks up a fixed vector per text string."""

    def __init__(self, vector_by_text: dict, *, fail_on: set = frozenset()):
        self._vector_by_text = vector_by_text
        self._fail_on = fail_on
        self.calls: list = []

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        assert len(texts) == 1, "DocumentVectorStore embeds one document at a time"
        text = texts[0]
        if text in self._fail_on:
            return None
        vec = self._vector_by_text.get(text)
        if vec is None:
            return None
        return [vec]


def _doc(ingest_id, text, symbol="AAPL", source="test_source", as_of="2026-07-01T12:00:00+00:00"):
    return {
        "ingest_id": ingest_id,
        "as_of": as_of,
        "trading_day": "2026-07-01",
        "symbol": symbol,
        "source_name": source,
        "text_content": text,
    }


# ---------------------------------------------------------------------------
# faiss unavailable -- forced import failure
# ---------------------------------------------------------------------------


@pytest.fixture
def force_faiss_unavailable(monkeypatch):
    """Force every ``import faiss`` to raise ImportError, regardless of
    whether faiss-cpu is actually installed in this venv -- a None entry in
    sys.modules makes the import statement itself fail."""
    monkeypatch.setitem(sys.modules, "faiss", None)
    yield


class TestFaissUnavailable:
    def test_faiss_available_reports_false(self, force_faiss_unavailable):
        assert _faiss_available() is False

    def test_index_new_documents_is_a_noop(self, force_faiss_unavailable, tmp_path):
        docs = [_doc(1, "doc one"), _doc(2, "doc two")]
        store = FakeRagStore(docs)
        provider = FakeEmbeddingProvider({"doc one": [1.0, 0.0], "doc two": [0.0, 1.0]})
        vs = DocumentVectorStore(
            index_path=str(tmp_path / "index.faiss"),
            store=store,
            embedding_provider=provider,
        )

        from datetime import datetime, timezone

        count = vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))

        assert count == 0
        assert provider.calls == []  # never even reached the embedding call
        assert store.get_rag_indexed_doc_count() == 0

    def test_search_returns_empty_list(self, force_faiss_unavailable, tmp_path):
        store = FakeRagStore([])
        vs = DocumentVectorStore(index_path=str(tmp_path / "index.faiss"), store=store)

        results = vs.search([1.0, 0.0, 0.0], k=5)

        assert results == []

    def test_search_with_empty_query_never_raises(self, force_faiss_unavailable, tmp_path):
        vs = DocumentVectorStore(index_path=str(tmp_path / "index.faiss"))
        assert vs.search([], k=5) == []
        assert vs.search(None, k=5) == []


class TestNeverRaisesOnBadInputRegardlessOfFaiss:
    def test_search_empty_query_no_faiss_check_needed(self, tmp_path):
        vs = DocumentVectorStore(index_path=str(tmp_path / "index.faiss"))
        assert vs.search([], k=5) == []

    def test_doc_hash_is_stable_and_never_raises(self):
        h1 = _doc_hash("hello world")
        h2 = _doc_hash("hello world")
        h3 = _doc_hash("different text")
        assert h1 == h2
        assert h1 != h3
        assert _doc_hash("") is not None
        assert _doc_hash(None) is not None  # coerced via `or ""` internally


# ---------------------------------------------------------------------------
# Real faiss round-trip (only runs when faiss-cpu is actually installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FAISS_INSTALLED, reason="faiss-cpu not installed in this environment")
class TestRealFaissRoundTrip:
    def test_index_and_search_round_trip(self, tmp_path):
        docs = [
            _doc(1, "doc-a", symbol="AAPL"),
            _doc(2, "doc-b", symbol="MSFT"),
            _doc(3, "doc-c", symbol="TSLA"),
        ]
        store = FakeRagStore(docs)
        provider = FakeEmbeddingProvider(
            {
                "doc-a": [1.0, 0.0, 0.0, 0.0],
                "doc-b": [0.0, 1.0, 0.0, 0.0],
                "doc-c": [0.0, 0.0, 1.0, 0.0],
            }
        )
        vs = DocumentVectorStore(
            index_path=str(tmp_path / "index.faiss"),
            store=store,
            embedding_provider=provider,
        )

        from datetime import datetime, timezone

        count = vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))

        assert count == 3
        assert store.get_rag_indexed_doc_count() == 3

        # Query near doc-a's vector -- nearest neighbor must be ingest_id=1 / AAPL.
        results = vs.search([0.9, 0.05, 0.0, 0.0], k=1)
        assert len(results) == 1
        assert isinstance(results[0], IndexedDocument)
        assert results[0].ingest_id == 1
        assert results[0].symbol == "AAPL"

        # A second call with no new pending documents indexes nothing more.
        count2 = vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert count2 == 0

    def test_persistence_across_instances(self, tmp_path):
        index_path = str(tmp_path / "index.faiss")
        docs = [_doc(10, "persisted-doc", symbol="NVDA")]
        store = FakeRagStore(docs)
        provider = FakeEmbeddingProvider({"persisted-doc": [1.0, 0.0]})

        vs1 = DocumentVectorStore(index_path=index_path, store=store, embedding_provider=provider)
        from datetime import datetime, timezone

        assert vs1.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc)) == 1

        # A fresh instance pointed at the same path (and the same store, so
        # metadata hydration still works) must load the persisted index from
        # disk and be able to search it immediately.
        vs2 = DocumentVectorStore(index_path=index_path, store=store)
        results = vs2.search([1.0, 0.0], k=1)
        assert len(results) == 1
        assert results[0].ingest_id == 10
        assert results[0].symbol == "NVDA"

    def test_best_effort_skips_bad_document_continues_batch(self, tmp_path):
        docs = [_doc(1, "good-doc"), _doc(2, "bad-doc"), _doc(3, "another-good-doc")]
        store = FakeRagStore(docs)
        provider = FakeEmbeddingProvider(
            {"good-doc": [1.0, 0.0], "another-good-doc": [0.0, 1.0]},
            fail_on={"bad-doc"},
        )
        vs = DocumentVectorStore(
            index_path=str(tmp_path / "index.faiss"), store=store, embedding_provider=provider
        )

        from datetime import datetime, timezone

        count = vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))

        # Only the 2 embeddable documents were indexed; the batch was not aborted.
        assert count == 2
        assert store.get_rag_indexed_doc_count() == 2
        assert 2 not in store._indexed  # the failed doc was never recorded

    def test_no_embedding_provider_is_a_noop(self, tmp_path):
        docs = [_doc(1, "doc-a")]
        store = FakeRagStore(docs)
        vs = DocumentVectorStore(
            index_path=str(tmp_path / "index.faiss"), store=store, embedding_provider=None
        )
        # Force resolution to fail (no real provider configured in test env).
        vs._embedding_provider = None

        from datetime import datetime, timezone
        from unittest.mock import patch

        with patch("llm.router.get_embedding_provider", return_value=None):
            count = vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))

        assert count == 0
        assert store.get_rag_indexed_doc_count() == 0

    def test_eviction_respects_max_documents(self, tmp_path, monkeypatch):
        from settings import settings

        monkeypatch.setattr(settings, "RAG_INDEX_MAX_DOCUMENTS", 2)

        docs = [_doc(i, f"doc-{i}") for i in range(1, 5)]  # 4 documents, cap of 2
        store = FakeRagStore(docs)
        vector_map = {f"doc-{i}": [float(i), 0.0, 0.0] for i in range(1, 5)}
        provider = FakeEmbeddingProvider(vector_map)
        vs = DocumentVectorStore(
            index_path=str(tmp_path / "index.faiss"), store=store, embedding_provider=provider
        )

        from datetime import datetime, timezone

        count = vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))

        assert count == 4
        # Eviction must bring the tracked count back down to the cap.
        assert store.get_rag_indexed_doc_count() == 2
        # FIFO: the oldest ingest_ids (1, 2) were evicted; 3 and 4 remain.
        remaining_ids = set(store._indexed.keys())
        assert remaining_ids == {3, 4}
        # The FAISS index itself must also have shrunk to match.
        assert vs._index.ntotal == 2

    def test_search_dimension_mismatch_returns_empty(self, tmp_path):
        docs = [_doc(1, "doc-a")]
        store = FakeRagStore(docs)
        provider = FakeEmbeddingProvider({"doc-a": [1.0, 0.0, 0.0]})  # 3-dim
        vs = DocumentVectorStore(
            index_path=str(tmp_path / "index.faiss"), store=store, embedding_provider=provider
        )
        from datetime import datetime, timezone

        vs.index_new_documents(since=datetime(2020, 1, 1, tzinfo=timezone.utc))

        # Query with a mismatched dimensionality (2-dim vs. the 3-dim index).
        results = vs.search([1.0, 0.0], k=1)
        assert results == []
