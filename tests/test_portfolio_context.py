"""
tests/test_portfolio_context.py
=================================
Unit tests for ``engine.portfolio_context`` (Phase 2 PR3, 3c — Retrieval +
Contextualization).

Coverage
--------
* Flag off (``RAG_PORTFOLIO_CONTEXT_ENABLED=False``, the real pydantic
  default) -> exposure-only result, ZERO embedding/retrieval/LLM calls
  attempted (mocked and asserted not-called).
* Embedding/retrieval + LLM failure -> falls back to exposure-only, never
  raises.
* Embedding failure alone does not block the (still best-effort) LLM step —
  documents the independent-steps contract mirrored from
  ``engine.advisory.enrich_with_llm_rationale``.
* Full mocked success path -> note fields populated, correct call counts.
* PIT-safety: a retrieved document whose ``as_of`` is in the future relative
  to ``now`` is excluded from both the result AND the LLM prompt — no
  lookahead leakage into "today's" note.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from data.rag_index import IndexedDocument
from engine.portfolio_context import (
    PortfolioContextResult,
    _filter_pit_safe,
    generate_portfolio_context_note,
)
from settings import settings


def _position(symbol: str, market_value: float):
    return SimpleNamespace(symbol=symbol, market_value=market_value)


def _snapshot(positions: dict, total_equity: float):
    return SimpleNamespace(positions=positions, total_equity=total_equity)


class FakeEmbeddingProvider:
    def __init__(self, vector=None):
        self.vector = vector or [1.0, 0.0]
        self.call_count = 0

    def embed_texts(self, texts):
        self.call_count += 1
        return [self.vector]


class FailingEmbeddingProvider:
    def __init__(self):
        self.call_count = 0

    def embed_texts(self, texts):
        self.call_count += 1
        raise RuntimeError("embedding boom")


class FakeVectorStore:
    def __init__(self, docs):
        self._docs = docs
        self.search_calls: list = []

    def search(self, query_embedding, k=5):
        self.search_calls.append((query_embedding, k))
        return self._docs


class FakeLLMProvider:
    def __init__(self, note=None):
        self.note = note
        self.call_count = 0
        self.last_args = None

    def call_structured(self, system, user, schema_model):
        self.call_count += 1
        self.last_args = (system, user, schema_model)
        return self.note


class FailingLLMProvider:
    def __init__(self):
        self.call_count = 0

    def call_structured(self, *a, **k):
        self.call_count += 1
        raise RuntimeError("llm boom")


@pytest.fixture(autouse=True)
def _reset_sector_cache():
    from engine.portfolio_exposure import reset_sector_map_cache

    reset_sector_map_cache()
    yield
    reset_sector_map_cache()


class TestFlagOff:
    def test_flag_off_returns_exposure_only_zero_calls(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", False)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)

        embedding_provider = FakeEmbeddingProvider()
        vector_store = FakeVectorStore([])
        llm_provider = FakeLLMProvider()

        result = generate_portfolio_context_note(
            snapshot,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            llm_provider=llm_provider,
        )

        assert isinstance(result, PortfolioContextResult)
        assert result.context_note is None
        assert result.sector_exposure  # deterministic exposure summary always present
        assert result.total_equity == pytest.approx(1000.0)

        # The whole point of the flag: nothing downstream is even attempted.
        assert embedding_provider.call_count == 0
        assert vector_store.search_calls == []
        assert llm_provider.call_count == 0

    def test_default_setting_is_false(self):
        # Pin the real pydantic default so "flag off" isn't a test-only fiction.
        assert settings.RAG_PORTFOLIO_CONTEXT_ENABLED is False


class TestEmptyPortfolio:
    def test_empty_positions_skips_retrieval_and_llm(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        snapshot = _snapshot({}, 0.0)
        embedding_provider = FakeEmbeddingProvider()
        llm_provider = FakeLLMProvider()

        result = generate_portfolio_context_note(
            snapshot, embedding_provider=embedding_provider, llm_provider=llm_provider
        )

        assert result.sector_exposure == {}
        assert result.context_note is None
        assert embedding_provider.call_count == 0
        assert llm_provider.call_count == 0


class TestFailureFallback:
    def test_retrieval_and_llm_failure_falls_back_to_exposure_only_no_exception(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)

        result = generate_portfolio_context_note(
            snapshot,
            embedding_provider=FailingEmbeddingProvider(),
            vector_store=FakeVectorStore([]),
            llm_provider=FailingLLMProvider(),
        )

        # Never raises; degrades all the way to the deterministic summary.
        assert result.context_note is None
        assert result.sector_exposure
        assert result.retrieved_document_count == 0

    def test_embedding_failure_does_not_block_the_independent_llm_step(self, monkeypatch):
        # Mirrors engine.advisory.enrich_with_llm_rationale's independent-steps
        # contract: a retrieval failure degrades to an empty document list and
        # the function CONTINUES to the (still best-effort) LLM call.
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)
        note = SimpleNamespace(
            headline="h", tailwind_or_headwind="neutral", rationale="r", affected_sectors=[]
        )
        llm_provider = FakeLLMProvider(note=note)

        result = generate_portfolio_context_note(
            snapshot,
            embedding_provider=FailingEmbeddingProvider(),
            vector_store=FakeVectorStore([]),
            llm_provider=llm_provider,
        )

        assert result.retrieved_document_count == 0
        assert result.context_note is note
        assert llm_provider.call_count == 1

    def test_search_step_exception_falls_back_gracefully(self, monkeypatch):
        class BoomVectorStore:
            def search(self, *a, **k):
                raise RuntimeError("faiss boom")

        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)

        result = generate_portfolio_context_note(
            snapshot,
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=BoomVectorStore(),
            llm_provider=FakeLLMProvider(note=None),
        )

        assert result.retrieved_document_count == 0
        assert result.sector_exposure


class TestFullSuccessPath:
    def test_full_mocked_success_populates_note(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)

        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        doc = IndexedDocument(
            ingest_id=1,
            symbol="AAPL",
            source="finnhub",
            text="Apple beats earnings expectations",
            as_of=(now - timedelta(days=1)).isoformat(),
            score=0.9,
        )
        embedding_provider = FakeEmbeddingProvider()
        vector_store = FakeVectorStore([doc])
        note = SimpleNamespace(
            headline="Tech concentration",
            tailwind_or_headwind="tailwind",
            rationale="Recent Apple earnings beat supports the tech overweight.",
            affected_sectors=["Technology"],
        )
        llm_provider = FakeLLMProvider(note=note)

        result = generate_portfolio_context_note(
            snapshot,
            now=now,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            llm_provider=llm_provider,
        )

        assert result.context_note is note
        assert result.retrieved_document_count == 1
        assert result.retrieved_symbols == ["AAPL"]
        assert embedding_provider.call_count == 1
        assert llm_provider.call_count == 1
        assert len(vector_store.search_calls) == 1
        # RAG_RETRIEVAL_TOP_K is threaded through to the search call.
        _, k = vector_store.search_calls[0]
        assert k == settings.RAG_RETRIEVAL_TOP_K

    def test_llm_prompt_cites_exposure_and_retrieved_text(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)

        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        doc = IndexedDocument(
            ingest_id=1,
            symbol="AAPL",
            source="finnhub",
            text="Apple beats earnings expectations",
            as_of=(now - timedelta(days=1)).isoformat(),
            score=0.9,
        )
        llm_provider = FakeLLMProvider(note=None)

        generate_portfolio_context_note(
            snapshot,
            now=now,
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore([doc]),
            llm_provider=llm_provider,
        )

        system, user, schema_model = llm_provider.last_args
        assert "Apple beats earnings expectations" in user
        assert "Technology" in user  # AAPL's real sector from ticker_sectors.csv
        from llm.schemas import PortfolioContextNote

        assert schema_model is PortfolioContextNote


class TestPitSafety:
    def test_future_document_excluded_from_result_and_prompt(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_PORTFOLIO_CONTEXT_ENABLED", True)
        positions = {"AAPL": _position("AAPL", 1000.0)}
        snapshot = _snapshot(positions, 1000.0)

        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        past_doc = IndexedDocument(
            ingest_id=1, symbol="AAPL", source="finnhub",
            text="genuine past headline", as_of=(now - timedelta(days=1)).isoformat(), score=0.9,
        )
        future_doc = IndexedDocument(
            ingest_id=2, symbol="AAPL", source="finnhub",
            text="leaked future headline", as_of=(now + timedelta(days=1)).isoformat(), score=0.99,
        )
        llm_provider = FakeLLMProvider(note=None)

        result = generate_portfolio_context_note(
            snapshot,
            now=now,
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore([future_doc, past_doc]),
            llm_provider=llm_provider,
        )

        # Only the past document survives the PIT filter.
        assert result.retrieved_document_count == 1

        system, user, schema_model = llm_provider.last_args
        assert "leaked future headline" not in user
        assert "genuine past headline" in user

    def test_unparsable_as_of_excluded_fail_closed(self):
        docs = [
            IndexedDocument(
                ingest_id=1, symbol="AAPL", source="x", text="bad date",
                as_of="not-a-date", score=1.0,
            ),
        ]
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        assert _filter_pit_safe(docs, now) == []

    def test_naive_as_of_treated_as_utc(self):
        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        docs = [
            IndexedDocument(
                ingest_id=1, symbol="AAPL", source="x", text="naive past",
                as_of="2026-07-22T12:00:00", score=1.0,  # no tz suffix
            ),
        ]
        result = _filter_pit_safe(docs, now)
        assert len(result) == 1

    def test_exactly_now_is_included_not_excluded(self):
        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        docs = [
            IndexedDocument(
                ingest_id=1, symbol="AAPL", source="x", text="exactly now",
                as_of=now.isoformat(), score=1.0,
            ),
        ]
        assert len(_filter_pit_safe(docs, now)) == 1
