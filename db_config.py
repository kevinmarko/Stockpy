"""Dual-backend database configuration seam.

This module is the single source of truth for how the platform resolves its
database URL, constructs its SQLAlchemy engine, and manages session lifecycle.

By default the platform runs on a local SQLite file (`quant_platform.db` at the
repo root) with WAL journaling for concurrency. Setting `DATABASE_URL` (e.g. a
`postgresql://...` DSN) transparently switches every store onto Postgres with a
pre-pinged connection pool. Backend-specific engine tuning (SQLite PRAGMAs,
Postgres pool sizing) lives here so callers never have to branch on backend.

`session_scope()` is the canonical connection-lifecycle helper: commit on
success, rollback on exception, always close.

CONSTRAINT #3: a Postgres URL can embed `user:pass@host`, so this module logs
the backend NAME only, never the full URL.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from settings import settings

logger = logging.getLogger(__name__)

# db_config.py lives at the repo root, so this resolves to <repo_root>.
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_FILE = os.path.join(DB_DIR, "quant_platform.db")
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_FILE}"


def resolve_database_url() -> str:
    """Return the configured DATABASE_URL, or the default SQLite file URL."""
    configured = getattr(settings, "DATABASE_URL", None)
    if configured and str(configured).strip():
        return str(configured).strip()
    return DEFAULT_DATABASE_URL


def create_db_engine(db_url: str | None = None) -> Engine:
    """Construct a SQLAlchemy engine tuned for the resolved backend.

    - postgresql: pre-pinged connection pool with sizing + recycle.
    - sqlite (file): pre-ping + a connect-time PRAGMA hook (WAL, busy_timeout).
    - sqlite (:memory:): pre-ping only (no file, no PRAGMA hook).
    - any other backend: plain pre-pinged engine.
    """
    if db_url is None:
        db_url = resolve_database_url()

    url = make_url(db_url)
    backend = url.get_backend_name()

    if backend == "postgresql":
        engine = create_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=getattr(settings, "DB_POOL_SIZE", 5),
            max_overflow=getattr(settings, "DB_MAX_OVERFLOW", 10),
            pool_recycle=1800,
        )
    elif backend == "sqlite":
        # SQLite's default pool does not accept pool_size/max_overflow.
        # Use NullPool for file-based SQLite to prevent cached connections in the pool
        # from bypassing test mocks (e.g. patched sqlite3.connect) or holding file locks.
        from sqlalchemy.pool import NullPool
        is_memory = url.database in (None, "", ":memory:")
        poolclass = None if is_memory else NullPool
        engine = create_engine(db_url, echo=False, pool_pre_ping=True, poolclass=poolclass)
        if not is_memory:

            @event.listens_for(engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, conn_record):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
    else:
        engine = create_engine(db_url, echo=False, pool_pre_ping=True)

    # CONSTRAINT #3: never log the full URL — it can embed credentials.
    logger.info("DB backend: %s", backend)
    return engine


@contextmanager
def session_scope(session_factory):
    """Provide a transactional scope around a series of operations."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
