"""SQLAlchemy-backed durable log of every semantic Related Sector Selection
ranking computed for a target symbol (``sector_selection_engine.py``).

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``sizing/cap_audit_store.py`` / ``transactions_store.py`` / ``desktop/
run_history_store.py``'s convention exactly (own ``Base``, own table,
``session_scope`` for writes, a ``readonly=True`` database-level engine for
read-only consumers such as ``pilots/sector_selection.py``).

Every REAL/nullable column carries ``NULL`` (never a fabricated ``0.0``)
when its underlying computation was unavailable (CONSTRAINT #4) —
``cosine_similarity`` when no embedder/description, ``sector_heat_factor``
when review-channel-degraded or unobserved, ``correlation_coefficient``
when either input is unavailable. ``embedder``/``pooling`` are provenance
columns so a coefficient computed under a different backend configuration
is never silently compared against one that wasn't.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()


class SectorCorrelation(Base):
    __tablename__ = "sector_correlations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    as_of = Column(String(10), nullable=False, index=True)  # trading-day label YYYY-MM-DD
    target_symbol = Column(String(20), nullable=False, index=True)
    sector = Column(String(80), nullable=False)
    cosine_similarity = Column(Float, nullable=True)
    ingestion_volume = Column(Float, nullable=True)  # numNews + Review (pre-SHF)
    sector_heat_factor = Column(Float, nullable=True)
    correlation_coefficient = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)
    selected = Column(Integer, nullable=False, default=0)  # 1 if in top N, else 0
    degraded_reason = Column(String(40), nullable=True)
    embedder = Column(String(20), nullable=True)
    pooling = Column(String(10), nullable=True)
    computed_at = Column(DateTime, nullable=False)


class SectorCorrelationStore:
    """Durable log of Sector Selection correlation computations.

    ``readonly=True`` builds a database-level read-only engine and skips
    ``Base.metadata.create_all`` — a readonly instance assumes the table
    already exists (true once any write-mode store has run at least once).
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

    def record_correlations(
        self, rows: List[Dict[str, Any]], *, as_of: str, target_symbol: str,
    ) -> None:
        """Persist one target symbol's full candidate-sector ranking for
        ``as_of`` in ONE transaction.

        Each item in ``rows`` is a dict with keys: ``sector`` (required),
        ``cosine_similarity``, ``ingestion_volume``, ``sector_heat_factor``,
        ``correlation_coefficient``, ``rank``, ``selected``,
        ``degraded_reason``, ``embedder``, ``pooling`` — all optional
        except ``sector``, defaulting to ``None``/``0`` (never fabricated).

        Write methods intentionally still raise (mirrors ``CapAuditStore``
        — CONSTRAINT #4, never silently no-op a write); the caller wraps
        this in a best-effort try/except so a DB hiccup never blocks the
        engine's own ranking output.
        """
        if self._readonly:
            raise RuntimeError("SectorCorrelationStore is read-only; cannot record correlations.")
        if not rows:
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(self.Session) as session:
            objects = [
                SectorCorrelation(
                    as_of=str(as_of),
                    target_symbol=str(target_symbol).upper(),
                    sector=str(row["sector"]),
                    cosine_similarity=row.get("cosine_similarity"),
                    ingestion_volume=row.get("ingestion_volume"),
                    sector_heat_factor=row.get("sector_heat_factor"),
                    correlation_coefficient=row.get("correlation_coefficient"),
                    rank=row.get("rank"),
                    selected=int(bool(row.get("selected", False))),
                    degraded_reason=row.get("degraded_reason"),
                    embedder=row.get("embedder"),
                    pooling=row.get("pooling"),
                    computed_at=now,
                )
                for row in rows
            ]
            session.add_all(objects)

    def get_latest(self, target_symbol: str) -> List[Dict[str, Any]]:
        """Most-recent ``as_of``'s full ranking for ``target_symbol``,
        ordered by ``rank`` ascending (unranked/NULL rows last).

        Degrades to ``[]`` on any read failure or when nothing has been
        computed yet (CONSTRAINT #6 — never raises)."""
        try:
            session = self.Session()
            try:
                latest_as_of = (
                    session.query(SectorCorrelation.as_of)
                    .filter(SectorCorrelation.target_symbol == str(target_symbol).upper())
                    .order_by(SectorCorrelation.as_of.desc())
                    .limit(1)
                    .scalar()
                )
                if latest_as_of is None:
                    return []
                rows = (
                    session.query(SectorCorrelation)
                    .filter(
                        SectorCorrelation.target_symbol == str(target_symbol).upper(),
                        SectorCorrelation.as_of == latest_as_of,
                    )
                    .all()
                )
                rows.sort(key=lambda r: (r.rank is None, r.rank if r.rank is not None else 0))
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("SectorCorrelationStore.get_latest(%s): %s", target_symbol, exc)
            return []


class _OfflineSectorCorrelationStore:
    """Read-only stand-in used when the configured DB backend is
    unreachable. Mirrors ``sizing.cap_audit_store._OfflineCapAuditStore``:
    read methods degrade to empty results (CONSTRAINT #6); write methods
    intentionally still raise (CONSTRAINT #4 — never fabricate a
    successful write against an unreachable DB)."""

    def record_correlations(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "SectorCorrelationStore is unavailable (DB unreachable); cannot record correlations."
        )

    def get_latest(self, target_symbol: str) -> List[Dict[str, Any]]:
        return []


def _row_to_dict(row: SectorCorrelation) -> Dict[str, Any]:
    return {
        "id": row.id,
        "as_of": row.as_of,
        "target_symbol": row.target_symbol,
        "sector": row.sector,
        "cosine_similarity": row.cosine_similarity,
        "ingestion_volume": row.ingestion_volume,
        "sector_heat_factor": row.sector_heat_factor,
        "correlation_coefficient": row.correlation_coefficient,
        "rank": row.rank,
        "selected": bool(row.selected),
        "degraded_reason": row.degraded_reason,
        "embedder": row.embedder,
        "pooling": row.pooling,
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
    }
