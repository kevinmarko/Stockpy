"""SQLAlchemy-backed durable log of every ``StrategyValidationHarness.run()``
call (``validation/harness.py``).

Why this exists: the harness already writes ``reports/<strategy>_validation_summary.json``
(overwritten every run) and appends one row per run to
``reports/history/<strategy>_validation_history.jsonl`` (``_append_validation_history``) --
but both live under the repo's ``reports/`` directory, which is **worktree-local**: this repo
runs many simultaneous git worktrees, and an untracked file written in one worktree is
invisible from every other one (the exact class of bug documented for
``settings.LOCAL_DATA_ROOT`` -- see that field's own docstring and ``db_config.py``'s module
docstring). A validation run in one worktree was therefore invisible to
``pilots/validation_trend.py`` / the Pilots PWA's Validation Trend chart running against a
different worktree's API process. This module gives every validation run a second, durable
home so that view can survive both a restart AND a different worktree -- the JSON/JSONL/HTML
file outputs are UNCHANGED and remain the primary raw-report artifacts.

The backend is resolved through ``db_config.py`` (SQLite by default, Postgres/Supabase when
``DATABASE_URL`` is set), matching ``desktop/run_history_store.py`` / ``sizing/cap_audit_store.py``
/ ``data/sector_correlation_store.py`` / ``transactions_store.py``'s convention exactly (own
``Base``, own table, ``session_scope`` for writes, a ``readonly=True`` database-level engine
for read-only consumers like ``pilots/validation_trend.py``).

Unlike ``desktop/run_history_store.py``'s ``pipeline_runs`` (upserted by ``run_id`` -- one row
per in-flight/completed cycle), this table is **append-only**, one new row per harness run,
matching ``reports/history/*.jsonl``'s own semantics -- a strategy validated 40 times has 40
rows, not one row overwritten 40 times.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(128), nullable=False, index=True)
    recorded_at = Column(DateTime, nullable=False, index=True)
    report_date = Column(String(10), nullable=True)
    start_date = Column(String(10), nullable=True)
    end_date = Column(String(10), nullable=True)
    deployable = Column(Boolean, nullable=True)
    family_deployable = Column(Boolean, nullable=True)
    family_bh_significant = Column(Boolean, nullable=True)
    is_options_selling = Column(Boolean, nullable=True)
    stress_gate_passed = Column(Boolean, nullable=True)
    pbo = Column(Float, nullable=True)
    dsr = Column(Float, nullable=True)
    sharpe = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    n_trials = Column(Integer, nullable=True)
    # Full ValidationReport.to_summary_dict() payload -- TEXT, not a JSON column
    # type, so this works identically on SQLite and Postgres. Promoted scalar
    # columns above exist for indexed/filtered queries; this blob is the
    # authoritative record (equity/benchmark/macro curves,
    # family_multiple_testing detail, etc. are never lost even though they
    # aren't individually columned) -- same shape as
    # desktop/run_history_store.py's PipelineRun.progress_json.
    summary_json = Column(Text, nullable=True)


class ValidationHistoryStore:
    """Durable append-only log wrapper around the ``validation_runs`` table.

    ``readonly=True`` builds a DATABASE-LEVEL read-only engine (see
    ``db_config.create_readonly_db_engine``) and skips ``Base.metadata.create_all``
    -- a readonly instance assumes the table already exists (true once any
    write-mode store, i.e. the validation harness, has run).
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

    def record_run(self, summary: Dict[str, Any]) -> None:
        """Insert one new row from a ``ValidationReport.to_summary_dict()`` dict.

        Always inserts (never upserts) -- one row per harness run, matching
        ``reports/history/*.jsonl``'s append-only semantics. Write methods
        intentionally still raise (mirrors ``RunHistoryStore``/``TransactionsStore``
        -- CONSTRAINT #4, never silently no-op a write); the caller
        (``StrategyValidationHarness._record_validation_run_to_db``) wraps this
        in a best-effort try/except so a DB hiccup can never abort an
        otherwise-successful validation run.
        """
        if self._readonly:
            raise RuntimeError("ValidationHistoryStore is read-only; cannot record a run.")

        row = ValidationRun(
            strategy_id=str(summary.get("strategy_id") or ""),
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            report_date=summary.get("report_date"),
            start_date=summary.get("start_date"),
            end_date=summary.get("end_date"),
            deployable=summary.get("deployable"),
            family_deployable=summary.get("family_deployable"),
            family_bh_significant=summary.get("family_bh_significant"),
            is_options_selling=summary.get("is_options_selling"),
            stress_gate_passed=summary.get("stress_gate_passed"),
            pbo=_safe_float(summary.get("pbo")),
            dsr=_safe_float(summary.get("dsr")),
            sharpe=_safe_float(summary.get("sharpe")),
            max_drawdown=_safe_float(summary.get("max_drawdown")),
            n_trials=summary.get("n_trials"),
            summary_json=json.dumps(summary),
        )
        with session_scope(self.Session) as session:
            session.add(row)

    def get_recent(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Most-recent-first list of JSON-safe run dicts, optionally filtered
        to one strategy.

        Degrades to ``[]`` -- never a raised exception -- on any read
        failure (dead-letter resilient, CONSTRAINT #6), matching
        ``RunHistoryStore.get_recent``.
        """
        try:
            session = self.Session()
            try:
                query = session.query(ValidationRun)
                if strategy_id:
                    query = query.filter(ValidationRun.strategy_id == strategy_id)
                rows = query.order_by(ValidationRun.recorded_at.desc()).limit(limit).all()
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("ValidationHistoryStore.get_recent: %s", exc)
            return []

    def get_latest_per_strategy(self) -> Dict[str, Dict[str, Any]]:
        """One row per ``strategy_id`` -- the most recently recorded run --
        keyed by strategy id. Backs the "current snapshot" cross-strategy
        table. Degrades to ``{}`` on any read failure (CONSTRAINT #6).
        """
        try:
            session = self.Session()
            try:
                rows = session.query(ValidationRun).order_by(
                    ValidationRun.strategy_id, ValidationRun.recorded_at.desc()
                ).all()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to {}
            logger.warning("ValidationHistoryStore.get_latest_per_strategy: %s", exc)
            return {}

        latest: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            if r.strategy_id not in latest:
                latest[r.strategy_id] = _row_to_dict(r)
        return latest


def _safe_float(value: Any) -> Optional[float]:
    """Finite float, else ``None`` -- never a fabricated 0.0 (CONSTRAINT #4)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    import math

    return f if math.isfinite(f) else None


def _row_to_dict(row: ValidationRun) -> Dict[str, Any]:
    d = {
        "strategy_id": row.strategy_id,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "report_date": row.report_date,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "deployable": row.deployable,
        "family_deployable": row.family_deployable,
        "family_bh_significant": row.family_bh_significant,
        "is_options_selling": row.is_options_selling,
        "stress_gate_passed": row.stress_gate_passed,
        "pbo": row.pbo,
        "dsr": row.dsr,
        "sharpe": row.sharpe,
        "max_drawdown": row.max_drawdown,
        "n_trials": row.n_trials,
    }
    if row.summary_json:
        try:
            full = json.loads(row.summary_json)
            if isinstance(full, dict):
                # Full snapshot fields (equity_curve, family_multiple_testing,
                # etc.) win where present; the promoted scalar columns above
                # remain as a fallback for any key the blob happens to omit.
                d.update(full)
        except (TypeError, ValueError) as exc:
            logger.debug("ValidationHistoryStore: corrupt summary_json for row %s: %s", row.id, exc)
    return d
