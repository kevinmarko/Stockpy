"""
InvestYo Quant Platform - Dual-Backend DB Config Tests
=========================================================================
Covers db_config.py: resolve_database_url() precedence, create_db_engine()
dialect-aware construction (SQLite WAL pragma vs. Postgres pooling), and
session_scope() commit/rollback/close lifecycle.

No live Postgres server is used anywhere -- Postgres coverage is
construct-only (engine creation never connects). SQLite coverage uses
:memory: and a tmp_path-backed file DB, both fully offline.
"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

import db_config
from db_config import create_db_engine, resolve_database_url, session_scope
from settings import settings


# ---------------------------------------------------------------------------
# resolve_database_url()
# ---------------------------------------------------------------------------

def test_resolve_database_url_returns_sqlite_default_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    assert resolve_database_url() == db_config.DEFAULT_DATABASE_URL
    assert resolve_database_url().startswith("sqlite:///")


def test_resolve_database_url_returns_custom_value_when_set(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@host/db")
    assert resolve_database_url() == "postgresql://u:p@host/db"


def test_resolve_database_url_treats_blank_string_as_unset(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "   ")
    assert resolve_database_url() == db_config.DEFAULT_DATABASE_URL


# ---------------------------------------------------------------------------
# create_db_engine() - SQLite
# ---------------------------------------------------------------------------

def test_create_db_engine_memory_sqlite_works():
    engine = create_db_engine("sqlite:///:memory:")
    assert engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1


def test_create_db_engine_memory_sqlite_has_no_wal():
    """SQLite :memory: DBs ignore WAL -- journal_mode should not come back 'wal'."""
    engine = create_db_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() != "wal"


def test_create_db_engine_file_sqlite_enables_wal(tmp_path):
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_db_engine(db_url)
    assert engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() == "wal"


def test_create_db_engine_defaults_to_resolve_database_url(monkeypatch, tmp_path):
    db_path = tmp_path / "resolved.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_db_engine()  # db_url=None -> resolve_database_url()
    assert engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


# ---------------------------------------------------------------------------
# create_db_engine() - Postgres (construct-only, never connects)
# ---------------------------------------------------------------------------

def test_create_db_engine_postgres_constructs_without_connecting():
    engine = create_db_engine("postgresql://user:pass@localhost/testdb")
    assert engine.dialect.name == "postgresql"
    # Constructed successfully; we never call .connect() here since there is
    # no live Postgres server in the test environment.


def test_create_db_engine_postgres_url_repr_hides_password():
    engine = create_db_engine("postgresql://user:pass@localhost/testdb")
    # SQLAlchemy's default URL repr/str masks the password.
    assert "pass" not in str(engine.url)


def test_create_db_engine_never_logs_raw_url(caplog):
    caplog.set_level("INFO")
    create_db_engine("postgresql://user:supersecretpw@localhost/testdb")
    assert "supersecretpw" not in caplog.text


def test_create_db_engine_never_logs_raw_sqlite_path(caplog, tmp_path):
    caplog.set_level("INFO")
    db_path = tmp_path / "secretlocation.db"
    create_db_engine(f"sqlite:///{db_path}")
    assert "secretlocation" not in caplog.text


# ---------------------------------------------------------------------------
# session_scope()
# ---------------------------------------------------------------------------

def test_session_scope_commits_and_closes_on_clean_exit():
    mock_session = MagicMock()
    session_factory = lambda: mock_session

    with session_scope(session_factory) as session:
        assert session is mock_session

    mock_session.commit.assert_called_once()
    mock_session.rollback.assert_not_called()
    mock_session.close.assert_called_once()


def test_session_scope_rolls_back_and_closes_on_exception():
    mock_session = MagicMock()
    session_factory = lambda: mock_session

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        with session_scope(session_factory) as session:
            assert session is mock_session
            raise _BoomError("something went wrong")

    mock_session.rollback.assert_called_once()
    mock_session.commit.assert_not_called()
    mock_session.close.assert_called_once()
