"""SQLAlchemy-backed durable log of every OrchestratorDaemon run cycle
(``desktop/daemon_runtime.py::RunRecord``).

Why this exists: ``OrchestratorDaemon._run_history`` (see ``daemon_runtime.py``)
is a plain in-memory dict capped at the last ``run_history_size`` (default 10)
runs -- it lives entirely inside the daemon process and is lost on every
restart. The Pipeline Dashboard's run-history table (``webapp/src/screens/
PipelineDashboard.tsx``) surfaced that ring directly via ``GET /status``, so
an operator investigating "what happened overnight" had nothing once the
daemon restarted. This module gives completed runs a second, durable home so
the dashboard can show history that survives a restart -- the in-memory ring
is unchanged and still backs the live "is a run in flight right now" view.

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``transactions_store.py``'s convention exactly (own ``Base``, own table,
``session_scope`` for writes, a ``readonly=True`` database-level engine for
read-only consumers like ``api/control_api.py``).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id = Column(String(64), primary_key=True)
    state = Column(String(20), nullable=False)
    mode = Column(String(20), nullable=False, default="full")
    reason = Column(String(20), nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    # JSON-serialized reporting/progress.py snapshot (see RunRecord.progress's
    # own docstring) -- TEXT, not a JSON column type, so this works identically
    # on SQLite and Postgres without a dialect-specific column type.
    progress_json = Column(Text, nullable=True)


class RunHistoryStore:
    """Durable CRUD-lite wrapper around the ``pipeline_runs`` table.

    ``readonly=True`` builds a DATABASE-LEVEL read-only engine (see
    ``db_config.create_readonly_db_engine``) and skips
    ``Base.metadata.create_all`` -- a readonly instance assumes the table
    already exists (true once any write-mode store -- the daemon -- has run).
    """

    def __init__(self, db_url: Optional[str] = None, *, readonly: bool = False) -> None:
        db_url = db_url or resolve_database_url()
        self._readonly = readonly
        if readonly:
            from db_config import create_readonly_db_engine

            self.engine = create_readonly_db_engine(db_url)
        else:
            self.engine = create_db_engine(db_url)
            Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_run(self, record: Any) -> None:
        """Upsert a ``desktop.daemon_runtime.RunRecord`` into durable storage.

        Write methods intentionally still raise (mirrors
        ``TransactionsStore`` -- CONSTRAINT #4, never silently no-op a
        write). The caller (``OrchestratorDaemon._run_one_cycle``) wraps this
        in a best-effort try/except so a DB hiccup can never crash the
        daemon or lose the run's already-decided SUCCEEDED/FAILED state --
        only the durable history table lags, matching the progress-snapshot
        precedent in that same method.
        """
        if self._readonly:
            raise RuntimeError("RunHistoryStore is read-only; cannot record a run.")

        state = record.state
        state_value = state.value if hasattr(state, "value") else str(state)
        started_at = record.started_at.replace(tzinfo=None) if record.started_at else None
        finished_at = record.finished_at.replace(tzinfo=None) if record.finished_at else None

        with session_scope(self.Session) as session:
            row = session.get(PipelineRun, record.run_id)
            if row is None:
                row = PipelineRun(run_id=record.run_id)
                session.add(row)
            row.state = state_value
            row.mode = getattr(record, "mode", "full") or "full"
            row.reason = record.reason
            row.started_at = started_at
            row.finished_at = finished_at
            row.duration_seconds = record.duration_seconds
            row.error = record.error
            row.progress_json = (
                json.dumps(record.progress) if record.progress is not None else None
            )

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most-recent-first list of JSON-safe run dicts (same shape as
        ``api/control_api.py::_serialize_run``'s output), read straight from
        durable storage.

        Degrades to ``[]`` -- never a raised exception -- on any read
        failure (dead-letter resilient, CONSTRAINT #6), matching
        ``TransactionsStore``'s read-degrade contract.
        """
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(PipelineRun)
                    .order_by(PipelineRun.started_at.desc())
                    .limit(limit)
                    .all()
                )
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("RunHistoryStore.get_recent: %s", exc)
            return []


def _row_to_dict(row: PipelineRun) -> Dict[str, Any]:
    return {
        "run_id": row.run_id,
        "state": row.state,
        "mode": row.mode,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_seconds": row.duration_seconds,
        "error": row.error,
        "reason": row.reason,
        "progress": json.loads(row.progress_json) if row.progress_json else None,
    }
