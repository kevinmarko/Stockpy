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

import sqlite3
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import db_config
from db_config import (
    create_db_engine,
    create_readonly_db_engine,
    resolve_database_url,
    session_scope,
)
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
# create_readonly_db_engine() - the DATABASE-LEVEL read-only seam
# ---------------------------------------------------------------------------

def _make_wal_db(path):
    """Create a file DB via create_db_engine (WAL, matching production)."""
    engine = create_db_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE T (x INTEGER)"))
        conn.execute(text("INSERT INTO T VALUES (5)"))
        conn.commit()
    engine.dispose()


def test_create_readonly_db_engine_file_sqlite_blocks_writes(tmp_path):
    db_path = tmp_path / "ro.db"
    _make_wal_db(db_path)
    engine = create_readonly_db_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM T")).scalar() == 5
        with pytest.raises(OperationalError, match="readonly"):
            conn.execute(text("CREATE TABLE Z (a INTEGER)"))


def test_create_readonly_db_engine_does_not_set_wal_on_non_wal_db(tmp_path):
    """Regression for the WAL trap: `PRAGMA journal_mode=WAL` is a write and
    would raise on this non-WAL db if the read-only hook wrongly issued it.
    The db is built with plain sqlite3 (delete-mode journal), NOT create_db_engine.
    """
    db_path = tmp_path / "plain.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE T (x INTEGER)")
    conn.execute("INSERT INTO T VALUES (7)")
    conn.commit()
    conn.close()
    assert not (tmp_path / "plain.db-wal").exists()  # confirm it is non-WAL

    engine = create_readonly_db_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        # If the hook tried journal_mode=WAL, opening/connecting would raise here.
        assert conn.execute(text("SELECT x FROM T")).scalar() == 7


def test_create_readonly_db_engine_sets_busy_timeout(tmp_path):
    db_path = tmp_path / "ro.db"
    _make_wal_db(db_path)
    engine = create_readonly_db_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_create_readonly_db_engine_escapes_percent_in_path(tmp_path):
    """A '%' (or space) in the path must round-trip. Without urllib escaping,
    SQLite percent-decodes the URI path and opens the wrong/no file."""
    weird_dir = tmp_path / "100%da ta"
    weird_dir.mkdir()
    db_path = weird_dir / "q.db"
    _make_wal_db(db_path)
    engine = create_readonly_db_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM T")).scalar() == 5


def test_create_readonly_db_engine_uri_contains_mode_ro(tmp_path):
    """Pin the fail-open case: the actual connect string handed to pysqlite must
    carry `?mode=ro` and be a `file:` URI. A dropped mode=ro is silent otherwise."""
    db_path = tmp_path / "ro.db"
    _make_wal_db(db_path)
    engine = create_readonly_db_engine(f"sqlite:///{db_path}")
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    assert args[0].startswith("file:")
    assert args[0].endswith("?mode=ro")
    assert kwargs.get("uri") is True


def test_create_readonly_db_engine_postgres_sets_readonly_option():
    """Construct-only (never connects): the engine carries postgresql_readonly."""
    engine = create_readonly_db_engine("postgresql://user:pass@localhost/testdb")
    assert engine.dialect.name == "postgresql"
    assert engine.get_execution_options() == {"postgresql_readonly": True}


def test_create_readonly_db_engine_postgres_unset_ro_dsn_uses_primary_url(monkeypatch):
    """MCP_DATABASE_URL_RO unset (default) -> today's behavior: delegates to the
    primary DATABASE_URL with postgresql_readonly layered on (defense-in-depth,
    not a hard boundary — see module docstring)."""
    monkeypatch.setattr(settings, "MCP_DATABASE_URL_RO", None)
    engine = create_readonly_db_engine("postgresql://user:pass@primaryhost/testdb")
    assert "primaryhost" in str(engine.url)
    assert engine.get_execution_options() == {"postgresql_readonly": True}


def test_create_readonly_db_engine_postgres_prefers_restricted_role_dsn(monkeypatch):
    """MCP_DATABASE_URL_RO set -> connects DIRECTLY to that DSN (own pool,
    NOT derived from the primary write engine) rather than the primary
    DATABASE_URL. This is the genuine database-ENFORCED boundary: if the DSN
    authenticates as a role with no write grants, no session-level GUC can
    revert it — unlike the postgresql_readonly-only fallback above."""
    monkeypatch.setattr(
        settings, "MCP_DATABASE_URL_RO",
        "postgresql://mcp_readonly:secretpw@restrictedhost/testdb",
    )
    engine = create_readonly_db_engine("postgresql://user:pass@primaryhost/testdb")
    assert "restrictedhost" in str(engine.url)
    assert "primaryhost" not in str(engine.url)
    assert engine.get_execution_options() == {"postgresql_readonly": True}


def test_create_readonly_db_engine_postgres_ro_dsn_never_logs_secret(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(
        settings, "MCP_DATABASE_URL_RO",
        "postgresql://mcp_readonly:supersecretpw@restrictedhost/testdb",
    )
    create_readonly_db_engine("postgresql://user:pass@primaryhost/testdb")
    assert "supersecretpw" not in caplog.text


def test_create_readonly_db_engine_sqlite_ignores_ro_dsn(monkeypatch, tmp_path):
    """A single-file SQLite db has no role system — MCP_DATABASE_URL_RO must be
    a no-op on the sqlite branch (mode=ro is already the hard boundary there)."""
    monkeypatch.setattr(
        settings, "MCP_DATABASE_URL_RO", "postgresql://mcp_readonly:x@h/db"
    )
    db_path = tmp_path / "ro.db"
    _make_wal_db(db_path)
    engine = create_readonly_db_engine(f"sqlite:///{db_path}")
    assert engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM T")).scalar() == 5


def test_create_readonly_db_engine_rejects_memory_sqlite():
    with pytest.raises(ValueError):
        create_readonly_db_engine("sqlite:///:memory:")


def test_create_readonly_db_engine_rejects_unknown_backend():
    """Fail closed: never return an unenforceable read-write engine."""
    with pytest.raises(ValueError):
        create_readonly_db_engine("mysql://user:pass@localhost/testdb")


def test_create_readonly_db_engine_never_logs_raw_url(caplog):
    caplog.set_level("INFO")
    create_readonly_db_engine("postgresql://user:supersecretpw@localhost/testdb")
    assert "supersecretpw" not in caplog.text


def test_create_readonly_db_engine_never_logs_raw_sqlite_path(caplog, tmp_path):
    caplog.set_level("INFO")
    db_path = tmp_path / "secretlocation.db"
    _make_wal_db(db_path)
    caplog.clear()
    create_readonly_db_engine(f"sqlite:///{db_path}")
    assert "secretlocation" not in caplog.text


def test_create_db_engine_still_writes_after_readonly_added(tmp_path):
    """The write path must be entirely untouched by the read-only addition."""
    db_path = tmp_path / "rw.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE T (x INTEGER)"))
        conn.execute(text("INSERT INTO T VALUES (11)"))
        conn.commit()
        assert conn.execute(text("SELECT x FROM T")).scalar() == 11


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
