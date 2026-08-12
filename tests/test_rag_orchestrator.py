"""Tests for agents/rag_orchestrator.py.

Covers four fixes:
- fetch_portfolio_context: routing through db_config.create_readonly_db_engine()
  / settings.DATABASE_URL instead of a hand-rolled sqlite3.connect() against
  os.environ.get("DATABASE_URL", ...), and filtering to the LATEST
  account_snapshots row only (was previously reading every historical
  snapshot's positions at once).
- retrieve_documents: no longer falls back to a random query embedding when
  sentence-transformers isn't installed (that retrieved semantically
  arbitrary documents and presented them as evidence) -- returns no
  documents instead, the same graceful-degrade shape every other optional
  dependency in this codebase uses.
- retrieve_documents / relevance_filter: renamed "source_score" ->
  "relevance_score" -- Qdrant's search score is semantic similarity, not a
  measure of source credibility/trustworthiness.
- generate_analysis: routed through llm/router.py's real
  get_rationale_provider() + LLMProvider.call_structured() contract instead
  of a nonexistent get_provider()/.generate(prompt) API that always raised.
"""
from __future__ import annotations

from typing import Optional, Type

from pydantic import BaseModel
from sqlalchemy import text

from settings import settings
from db_config import create_db_engine
from data.historical_store import _ACCOUNT_SNAPSHOTS_DDL, _ACCOUNT_POSITIONS_DDL
import agents.rag_orchestrator as rag_orchestrator
from agents.rag_orchestrator import (
    fetch_portfolio_context,
    generate_analysis,
    relevance_filter,
    retrieve_documents,
)


def _seed_db(db_url: str, positions: list[tuple[str, float]]) -> None:
    _seed_snapshots(db_url, [("2026-08-01T00:00:00Z", positions)])


def _seed_snapshots(db_url: str, snapshots: list[tuple[str, list[tuple[str, float]]]]) -> None:
    """Seed one or more (fetched_at, positions) snapshots, in the order given.
    snapshot_id is assigned 1..N in that same order."""
    engine = create_db_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_ACCOUNT_SNAPSHOTS_DDL))
        conn.execute(text(_ACCOUNT_POSITIONS_DDL))
        for snapshot_id, (fetched_at, positions) in enumerate(snapshots, start=1):
            conn.execute(
                text(
                    "INSERT INTO account_snapshots (snapshot_id, fetched_at, source) "
                    "VALUES (:snapshot_id, :fetched_at, 'test')"
                ),
                {"snapshot_id": snapshot_id, "fetched_at": fetched_at},
            )
            for symbol, qty in positions:
                conn.execute(
                    text(
                        "INSERT INTO account_positions (snapshot_id, symbol, qty) "
                        "VALUES (:snapshot_id, :symbol, :qty)"
                    ),
                    {"snapshot_id": snapshot_id, "symbol": symbol, "qty": qty},
                )
    engine.dispose()


class TestFetchPortfolioContext:
    def test_reads_held_positions_via_settings_database_url(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        db_url = f"sqlite:///{db_path}"
        monkeypatch.setattr(settings, "DATABASE_URL", db_url)
        _seed_db(db_url, [("NVDA", 10.0), ("ZZZZ", 0.0)])

        result = fetch_portfolio_context({"query": "test"})

        assert result == {"portfolio_context": ["NVDA (qty=10.0)"]}

    def test_ignores_os_environ_database_url(self, tmp_path, monkeypatch):
        """The old implementation read os.environ.get('DATABASE_URL', ...)
        directly. pydantic-settings' env_file loading does NOT populate real
        os.environ, so a value only set there (not via settings.DATABASE_URL)
        must have zero effect on which DB this function reads."""
        real_db = tmp_path / "real.db"
        real_url = f"sqlite:///{real_db}"
        monkeypatch.setattr(settings, "DATABASE_URL", real_url)
        _seed_db(real_url, [("AAPL", 5.0)])

        decoy_db = tmp_path / "decoy.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{decoy_db}")

        result = fetch_portfolio_context({"query": "test"})

        assert result == {"portfolio_context": ["AAPL (qty=5.0)"]}

    def test_missing_db_degrades_to_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'does_not_exist.db'}"
        )
        result = fetch_portfolio_context({"query": "test"})
        assert result == {"portfolio_context": []}

    def test_only_latest_snapshot_positions_returned(self, tmp_path, monkeypatch):
        """account_positions rows are linked to a snapshot_id FK -- one row
        per position per snapshot. Without a snapshot_id filter, closed
        positions and duplicate per-symbol quantities from EVERY historical
        snapshot would all be returned at once."""
        db_path = tmp_path / "test.db"
        db_url = f"sqlite:///{db_path}"
        monkeypatch.setattr(settings, "DATABASE_URL", db_url)
        _seed_snapshots(db_url, [
            ("2026-07-01T00:00:00Z", [("AAPL", 20.0), ("TSLA", 5.0)]),  # older: TSLA since closed
            ("2026-08-01T00:00:00Z", [("AAPL", 10.0), ("NVDA", 3.0)]),  # latest
        ])

        result = fetch_portfolio_context({"query": "test"})

        assert set(result["portfolio_context"]) == {"AAPL (qty=10.0)", "NVDA (qty=3.0)"}
        assert not any("TSLA" in p for p in result["portfolio_context"]), (
            "TSLA was only held in the older snapshot and must not appear"
        )
        assert not any("qty=20.0" in p for p in result["portfolio_context"]), (
            "AAPL's stale 20.0 qty from the older snapshot must not appear"
        )

    def test_no_account_snapshots_yet_degrades_to_empty(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        db_url = f"sqlite:///{db_path}"
        monkeypatch.setattr(settings, "DATABASE_URL", db_url)
        engine = create_db_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text(_ACCOUNT_SNAPSHOTS_DDL))
            conn.execute(text(_ACCOUNT_POSITIONS_DDL))
        engine.dispose()

        result = fetch_portfolio_context({"query": "test"})
        assert result == {"portfolio_context": []}


class _FakeHit:
    def __init__(self, score, title="", content="", ticker=""):
        self.score = score
        self.payload = {"title": title, "content": content, "ticker": ticker}

class _FakeResponse:
    def __init__(self, points):
        self.points = points


class TestRetrieveDocuments:
    def test_no_sentence_transformers_returns_no_documents_not_random_search(self, monkeypatch):
        """The fix: a missing embedding model must degrade to zero documents,
        never a semantically-arbitrary random-vector search presented as
        real retrieval."""
        monkeypatch.setattr(rag_orchestrator, "_QDRANT_AVAILABLE", True)

        search_called = {"n": 0}

        class _FakeQdrantClient:
            def __init__(self, url, timeout):
                pass

            def query_points(self, **kwargs):
                search_called["n"] += 1
                return _FakeResponse([_FakeHit(0.9)])

        monkeypatch.setattr(rag_orchestrator, "QdrantClient", _FakeQdrantClient)

        import builtins
        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("no sentence_transformers installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocking_import)

        result = retrieve_documents({"query": "what's happening with my energy holdings"})

        assert result == {"retrieved_docs": []}
        assert search_called["n"] == 0, "must never search Qdrant with a meaningless random vector"

    def test_qdrant_unavailable_returns_no_documents(self, monkeypatch):
        monkeypatch.setattr(rag_orchestrator, "_QDRANT_AVAILABLE", False)
        result = retrieve_documents({"query": "test"})
        assert result == {"retrieved_docs": []}

    def test_hits_labeled_relevance_score_not_source_score(self, monkeypatch):
        monkeypatch.setattr(rag_orchestrator, "_QDRANT_AVAILABLE", True)

        class _FakeQdrantClient:
            def __init__(self, url, timeout):
                pass

            def query_points(self, **kwargs):
                return _FakeResponse([_FakeHit(0.87, title="Oil prices rally", ticker="XOM")])

        monkeypatch.setattr(rag_orchestrator, "QdrantClient", _FakeQdrantClient)

        class _FakeSTModel:
            def encode(self, text):
                class _Vec:
                    def tolist(self_inner):
                        return [0.0] * 384
                return _Vec()

        class _FakeSentenceTransformers:
            SentenceTransformer = lambda *a, **k: _FakeSTModel()

        import sys
        monkeypatch.setitem(sys.modules, "sentence_transformers", _FakeSentenceTransformers)

        result = retrieve_documents({"query": "oil prices"})

        assert len(result["retrieved_docs"]) == 1
        doc = result["retrieved_docs"][0]
        assert doc["relevance_score"] == 0.87
        assert "source_score" not in doc


class TestRelevanceFilter:
    def test_keeps_only_docs_at_or_above_threshold(self):
        state = {
            "retrieved_docs": [
                {"relevance_score": 0.8, "title": "relevant"},
                {"relevance_score": 0.2, "title": "not relevant"},
                {"relevance_score": 0.5, "title": "boundary"},
            ]
        }
        result = relevance_filter(state)
        titles = {d["title"] for d in result["relevant_docs"]}
        assert titles == {"relevant", "boundary"}


class _FakeLLMProvider:
    def __init__(self, response: Optional[BaseModel]):
        self._response = response
        self.calls: list[tuple] = []

    def call_structured(self, system: str, user: str, schema_model: Type[BaseModel]):
        self.calls.append((system, user, schema_model))
        return self._response


class TestGenerateAnalysis:
    def test_no_provider_configured(self, monkeypatch):
        import llm.router as llm_router
        monkeypatch.setattr(llm_router, "get_rationale_provider", lambda: None)

        result = generate_analysis({"query": "test", "relevant_docs": [], "portfolio_context": []})
        assert "No LLM provider configured" in result["final_analysis"]

    def test_calls_call_structured_and_extracts_analysis(self, monkeypatch):
        from agents.rag_orchestrator import _RAGAnalysisOutput
        fake = _FakeLLMProvider(_RAGAnalysisOutput(analysis="Energy holdings look resilient."))

        import llm.router as llm_router
        monkeypatch.setattr(llm_router, "get_rationale_provider", lambda: fake)

        result = generate_analysis({
            "query": "How are my energy holdings?",
            "relevant_docs": [{"ticker": "XOM", "title": "Oil rallies", "content": "..."}],
            "portfolio_context": ["XOM (qty=10.0)"],
        })

        assert result["final_analysis"] == "Energy holdings look resilient."
        assert len(fake.calls) == 1
        system, user, schema_model = fake.calls[0]
        assert schema_model is _RAGAnalysisOutput
        assert "XOM" in user

    def test_provider_returns_none_degrades_honestly(self, monkeypatch):
        fake = _FakeLLMProvider(None)
        import llm.router as llm_router
        monkeypatch.setattr(llm_router, "get_rationale_provider", lambda: fake)

        result = generate_analysis({"query": "test", "relevant_docs": [], "portfolio_context": []})
        assert "no usable response" in result["final_analysis"]

    def test_exception_does_not_leak_raw_message(self, monkeypatch):
        class _BoomProvider:
            def call_structured(self, system, user, schema_model):
                raise RuntimeError("internal detail: /secret/path leaked")

        import llm.router as llm_router
        monkeypatch.setattr(llm_router, "get_rationale_provider", lambda: _BoomProvider())

        result = generate_analysis({"query": "test", "relevant_docs": [], "portfolio_context": []})
        assert "/secret/path" not in result["final_analysis"]
        assert "internal detail" not in result["final_analysis"]
        assert "LLM error" in result["final_analysis"]
