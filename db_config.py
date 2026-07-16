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

`create_db_engine()` is the WRITE path (every store: transactions_store,
historical_store). `create_readonly_db_engine()` is a SEPARATE, DATABASE-LEVEL
read-only seam — it is not a flag on the write path, so that grepping the name
enumerates the complete set of read-only consumers and a caller can never
silently fall back to a read-write default. The two enforcement mechanisms are
NOT equivalent by default, and this asymmetry is deliberate (CONSTRAINT #4 —
say it, don't gloss it): SQLite uses a `?mode=ro` URI, a hard boundary no
PRAGMA can revert; Postgres, absent `settings.MCP_DATABASE_URL_RO`, uses
`postgresql_readonly=True` (the `default_transaction_read_only` session GUC),
which is defense-in-depth but DEFEASIBLE by any session running arbitrary SQL.
Setting `MCP_DATABASE_URL_RO` to a DSN for a genuinely RESTRICTED role (see
`create_readonly_db_engine`'s docstring for the `CREATE ROLE` script) closes
that gap and makes both backends equally hard boundaries — that role creation
is operator/infra work this codebase cannot and must not perform on its own
(granting/restricting database access is outside what this platform's code is
permitted to do to your infrastructure).

CONSTRAINT #3: a Postgres URL can embed `user:pass@host`, so this module logs
the backend NAME only, never the full URL.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from contextlib import contextmanager
from typing import Any, Iterator

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


def sqlite_readonly_uri(db_path: str) -> str:
    """Build a ``file:…?mode=ro`` URI for ``sqlite3.connect(uri=True)``.

    The escaping is NOT cosmetic. SQLite terminates a URI path at the first
    ``?`` or ``#`` and percent-DECODES the path, so an unescaped ``?``/``#``/
    ``%`` in ``db_path`` silently DROPS ``?mode=ro`` and hands back a
    READ-WRITE connection with no error — a fail-open bug. ``quote(…, safe="/")``
    escapes those characters while leaving path separators intact, and (because
    it does not absolutize) preserves relative-vs-absolute paths so cwd-relative
    callers keep working.
    """
    return "file:" + urllib.parse.quote(db_path, safe="/") + "?mode=ro"


def create_readonly_db_engine(db_url: str | None = None) -> Engine:
    """Construct a DATABASE-LEVEL read-only engine for the resolved backend.

    Deliberately separate from ``create_db_engine`` (the write path) — grep this
    name to enumerate every read-only consumer, and note that read-only can
    never become a silently-omitted default. See the module docstring for the
    SQLite-vs-Postgres enforcement asymmetry.

    - postgresql, ``settings.MCP_DATABASE_URL_RO`` SET: connects DIRECTLY to that
      DSN (its own pool, never derived from the primary write engine) with
      ``postgresql_readonly=True`` layered on top as belt-and-suspenders. If that
      DSN authenticates as a restricted ROLE with no write grants, this is a
      genuine database-ENFORCED boundary — equivalent to SQLite's ``mode=ro`` —
      because even a client running arbitrary SQL cannot write; the ROLE itself
      lacks the privilege. Create such a role yourself (this codebase never
      grants/revokes database privileges on your behalf); a starting point::

          -- Run by a superuser/admin against your Postgres/Supabase database.
          CREATE ROLE mcp_readonly WITH LOGIN PASSWORD '<choose-a-strong-password>';
          GRANT CONNECT ON DATABASE <your_db> TO mcp_readonly;
          GRANT USAGE ON SCHEMA public TO mcp_readonly;
          GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
          -- Cover tables created AFTER this grant (e.g. by a future migration):
          ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;

          Then set (as a secret, never GUI-writable — see gui/env_io.py SECRET_KEYS):
          MCP_DATABASE_URL_RO=postgresql://mcp_readonly:<password>@<host>/<your_db>

    - postgresql, ``MCP_DATABASE_URL_RO`` UNSET: delegates to ``create_db_engine``
      (reusing pool sizing/recycle) and layers ``postgresql_readonly=True`` alone
      — defense-in-depth only (see module docstring). The base engine is
      constructed internally and NEVER handed out — the returned ``OptionEngine``
      shares its pool, so leaking the parent would leak a read-write handle onto
      the same pool.
    - sqlite (file): ``file:…?mode=ro`` URI — enforced by the connection itself,
      not revertible by any PRAGMA. ``MCP_DATABASE_URL_RO`` is ignored here (a
      single-file SQLite db has no role system to point a second DSN at).
    - sqlite (:memory:) / any other backend: RAISES. Never fail open by returning
      an unenforceable read-write engine (contrast ``create_db_engine``'s
      permissive ``else`` branch, which is fine for a write path but would be a
      hole here).
    """
    if db_url is None:
        db_url = resolve_database_url()

    url = make_url(db_url)
    backend = url.get_backend_name()

    if backend == "postgresql":
        ro_dsn = getattr(settings, "MCP_DATABASE_URL_RO", None)
        if ro_dsn and str(ro_dsn).strip():
            engine = create_engine(
                str(ro_dsn).strip(),
                echo=False,
                pool_pre_ping=True,
                pool_size=getattr(settings, "DB_POOL_SIZE", 5),
                max_overflow=getattr(settings, "DB_MAX_OVERFLOW", 10),
                pool_recycle=1800,
            ).execution_options(postgresql_readonly=True)
        else:
            engine = create_db_engine(db_url).execution_options(postgresql_readonly=True)
    elif backend == "sqlite":
        if url.database in (None, "", ":memory:"):
            raise ValueError(
                "create_readonly_db_engine: read-only engine is not meaningful "
                "for in-memory SQLite (an empty, private db)."
            )
        from sqlalchemy.pool import NullPool

        # Build the read-only URL by editing the parsed URL object, NOT by
        # re-parsing a `sqlite:///file:…?mode=ro` string: make_url() would swallow
        # `?mode=ro` into .query, and a later .set(query=…) would then DROP it —
        # a silent fail-open. quote() escapes the path; update_query_dict keeps
        # BOTH mode=ro (into the file: URI) and uri=true (tells pysqlite to pass
        # uri=True to sqlite3.connect). Verified: connect_args carries `?mode=ro`.
        quoted = urllib.parse.quote(url.database, safe="/")
        ro_url = url.set(database=f"file:{quoted}").update_query_dict(
            {"mode": "ro", "uri": "true"}
        )
        engine = create_engine(ro_url, echo=False, pool_pre_ping=True, poolclass=NullPool)

        @event.listens_for(engine, "connect")
        def _set_sqlite_readonly_pragma(dbapi_conn, conn_record):  # noqa: ANN001
            # busy_timeout ONLY. `PRAGMA journal_mode=WAL` is itself a write that
            # raises "attempt to write a readonly database" on any db not ALREADY
            # in WAL mode — it only silently no-ops on one that is, so reusing the
            # write path's hook would pass against the live (WAL) quant_platform.db
            # and explode on every non-WAL tmp_path fixture. busy_timeout is
            # connection-scoped and verified safe under mode=ro.
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    else:
        raise ValueError(
            f"create_readonly_db_engine: read-only engine unsupported for "
            f"backend: {backend}"
        )

    # CONSTRAINT #3: never log the full URL — it can embed credentials.
    logger.info("DB backend (read-only): %s", backend)
    return engine


@contextmanager
def session_scope(session_factory: Any) -> Iterator[Any]:
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


def get_dbapi_connection(raw_conn: Any) -> Any:
    """Retrieve the raw DBAPI connection from SQLAlchemy connection fairy/proxy
    without triggering SADeprecationWarning for accessing connection directly.
    """
    dbapi_conn = getattr(raw_conn, "driver_connection", None)
    if dbapi_conn is None:
        dbapi_conn = getattr(raw_conn, "connection", raw_conn)
    return dbapi_conn
