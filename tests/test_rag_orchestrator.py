"""Tests for agents/rag_orchestrator.py::fetch_portfolio_context.

Covers the fix from a hand-rolled sqlite3.connect() against
os.environ.get("DATABASE_URL", ...) to routing through
db_config.create_readonly_db_engine() / settings.DATABASE_URL — the
established, documented pattern this codebase uses for every other DB
reader (see CLAUDE.md's "Credential reads MUST go through settings.X, never
os.environ directly").
"""
from __future__ import annotations

from sqlalchemy import text

from settings import settings
from db_config import create_db_engine
from data.historical_store import _ACCOUNT_SNAPSHOTS_DDL, _ACCOUNT_POSITIONS_DDL
from agents.rag_orchestrator import fetch_portfolio_context


def _seed_db(db_url: str, positions: list[tuple[str, float]]) -> None:
    engine = create_db_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(_ACCOUNT_SNAPSHOTS_DDL))
        conn.execute(text(_ACCOUNT_POSITIONS_DDL))
        conn.execute(
            text(
                "INSERT INTO account_snapshots (snapshot_id, fetched_at, source) "
                "VALUES (1, '2026-08-01T00:00:00Z', 'test')"
            )
        )
        for symbol, qty in positions:
            conn.execute(
                text(
                    "INSERT INTO account_positions (snapshot_id, symbol, qty) "
                    "VALUES (1, :symbol, :qty)"
                ),
                {"symbol": symbol, "qty": qty},
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
