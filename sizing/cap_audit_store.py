"""SQLAlchemy-backed durable log of every position-sizing capping event
(``sizing.position_sizer.SizingDecision.was_capped`` / ``.binding_constraint``,
plus the cycle-wide ``PortfolioCapResult``).

Why this exists: before this module, ``was_capped``/``binding_constraint``
existed only as transient per-cycle telemetry inside ``dashboard_df`` /
``state_snapshot.json`` -- overwritten every cycle, with no history an
operator (or the cap-aware escalation rule in ``sizing/position_sizer.py``)
could query after the fact. This module gives every capping event a durable,
timestamped home so:

  1. The GUI / audit trail can show "which names have been hitting a ceiling,
     and how often" over time, not just this cycle's snapshot.
  2. The cap-aware escalation rule (``settings.SIZING_CAP_ESCALATION_ENABLED``)
     has something real to read: ``get_consecutive_capped_cycles()`` builds a
     ``sizing.position_sizer.CapEventSummary`` from durable history so a name
     that has been capped for N consecutive cycles can be down-weighted, per
     ``size_position()``'s escalation hook.

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``transactions_store.py`` / ``desktop/run_history_store.py``'s convention
exactly (own ``Base``, own table, ``session_scope`` for writes, a
``readonly=True`` database-level engine for read-only consumers).

Gated by ``settings.SIZING_CAP_AUDIT_ENABLED`` (default ``True``) -- callers
should check the flag before writing (see ``pipeline/production_steps.py``'s
call site); this module itself has no opinion on the flag, it just persists
whatever it's asked to.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

if TYPE_CHECKING:
    # Type-hint only -- the real import stays lazy (inside
    # get_consecutive_capped_cycles) to avoid a circular import at module
    # load (sizing.position_sizer does not import this module, but keeping
    # the runtime import lazy matches this file's own documented rationale).
    from sizing.position_sizer import CapEventSummary

logger = logging.getLogger(__name__)

Base = declarative_base()


class SizingCapEvent(Base):
    __tablename__ = "sizing_cap_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    # Identifies the run cycle this event belongs to (e.g. an ISO timestamp or
    # UUID the caller assigns once per cycle) -- lets a reader group events by
    # cycle without relying on timestamp proximity alone. Nullable: a caller
    # that doesn't track cycle identity can still log individual events.
    cycle_id = Column(String(64), nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    # Matches sizing.kelly's strategy_id concept (Stage 1.7 per-strategy
    # bootstrap path); None for the global-aggregate sizing path.
    strategy_id = Column(String(64), nullable=True)
    raw_weight = Column(Float, nullable=True)
    final_weight = Column(Float, nullable=True)
    binding_constraint = Column(String(40), nullable=True)
    was_capped = Column(Boolean, nullable=False, default=False)


class CapAuditStore:
    """Durable log of position-sizing capping events.

    ``readonly=True`` builds a DATABASE-LEVEL read-only engine (see
    ``db_config.create_readonly_db_engine``) and skips
    ``Base.metadata.create_all`` -- a readonly instance assumes the table
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

    def record_cap_events(self, events: List[Dict[str, Any]], *, cycle_id: Optional[str] = None) -> None:
        """Persist a whole cycle's capping events in ONE transaction.

        Each item in ``events`` is a dict with keys: ``symbol`` (required),
        ``strategy_id``, ``raw_weight``, ``final_weight``,
        ``binding_constraint``, ``was_capped`` (required), ``timestamp``
        (defaults to now if omitted). ``cycle_id`` is applied to every event
        that doesn't supply its own.

        Write methods intentionally still raise (mirrors
        ``RunHistoryStore``/``TransactionsStore`` -- CONSTRAINT #4, never
        silently no-op a write). The caller (e.g.
        ``pipeline/production_steps.py``) wraps this in a best-effort
        try/except so a DB hiccup can never affect the run's own sizing
        decisions -- only the durable audit trail lags.
        """
        if self._readonly:
            raise RuntimeError("CapAuditStore is read-only; cannot record events.")
        if not events:
            return

        with session_scope(self.Session) as session:
            for ev in events:
                symbol = ev["symbol"]
                ts = ev.get("timestamp") or datetime.now(timezone.utc)
                if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                session.add(SizingCapEvent(
                    timestamp=ts,
                    cycle_id=ev.get("cycle_id", cycle_id),
                    symbol=str(symbol).upper(),
                    strategy_id=ev.get("strategy_id"),
                    raw_weight=ev.get("raw_weight"),
                    final_weight=ev.get("final_weight"),
                    binding_constraint=ev.get("binding_constraint"),
                    was_capped=bool(ev.get("was_capped", False)),
                ))

    def get_recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Most-recent-first list of JSON-safe capping-event dicts.

        Degrades to ``[]`` -- never a raised exception -- on any read
        failure (dead-letter resilient, CONSTRAINT #6), matching
        ``RunHistoryStore``'s read-degrade contract.
        """
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(SizingCapEvent)
                    .order_by(SizingCapEvent.timestamp.desc())
                    .limit(limit)
                    .all()
                )
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("CapAuditStore.get_recent: %s", exc)
            return []

    def get_recent_for_symbol(
        self, symbol: str, strategy_id: Optional[str] = None, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Most-recent-first capping events for one (symbol, strategy_id).

        ``strategy_id=None`` matches ONLY events recorded with no
        strategy_id (the global-aggregate sizing path) -- it does not mean
        "any strategy". Degrades to ``[]`` on any read failure.
        """
        try:
            session = self.Session()
            try:
                query = session.query(SizingCapEvent).filter(
                    SizingCapEvent.symbol == str(symbol).upper(),
                    SizingCapEvent.strategy_id == strategy_id,
                )
                rows = query.order_by(SizingCapEvent.timestamp.desc()).limit(limit).all()
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("CapAuditStore.get_recent_for_symbol(%s): %s", symbol, exc)
            return []

    def get_consecutive_capped_cycles(
        self, symbol: str, strategy_id: Optional[str] = None, *, lookback: int = 30,
    ) -> "CapEventSummary":
        """Builds a ``sizing.position_sizer.CapEventSummary`` from durable
        history for the cap-aware escalation rule.

        Counts how many of the MOST RECENT events (up to ``lookback``) were
        capped, stopping at the first non-capped event walking backwards from
        now (i.e. a genuinely CONSECUTIVE run, not a raw count over the
        window). Degrades to a neutral ``CapEventSummary(0)`` -- never raises
        -- on any read failure or when no history exists, so a DB outage
        never itself triggers escalation.
        """
        from sizing.position_sizer import CapEventSummary  # local import: avoid a cycle at module load

        events = self.get_recent_for_symbol(symbol, strategy_id=strategy_id, limit=lookback)
        if not events:
            return CapEventSummary(consecutive_capped_cycles=0)

        consecutive = 0
        last_binding: Optional[str] = None
        for ev in events:  # already most-recent-first
            if not ev.get("was_capped"):
                break
            consecutive += 1
            if last_binding is None:
                last_binding = ev.get("binding_constraint")

        return CapEventSummary(consecutive_capped_cycles=consecutive, last_binding_constraint=last_binding)


class _OfflineCapAuditStore:
    """Read-only stand-in used when the configured DB backend is unreachable.

    ``CapAuditStore()`` construction does an eager connection (``Base
    .metadata.create_all``), so a network/DNS outage on a remote backend
    (e.g. a Postgres/Supabase ``DATABASE_URL``) raises before a single query
    is ever made. Mirrors ``transactions_store._OfflineTransactionsStore``:
    read methods degrade to empty/neutral results (a DB outage must never
    itself trigger cap-aware escalation, nor abort sizing for a symbol --
    CONSTRAINT #6); write methods intentionally still raise (CONSTRAINT #4 --
    never fabricate a successful write against an unreachable DB).
    """

    def record_cap_events(self, *args, **kwargs) -> None:
        raise RuntimeError("CapAuditStore is unavailable (DB unreachable); cannot record cap events.")

    def get_recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        return []

    def get_recent_for_symbol(self, symbol: str, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return []

    def get_consecutive_capped_cycles(self, symbol: str, strategy_id: Optional[str] = None, *, lookback: int = 30):
        from sizing.position_sizer import CapEventSummary

        return CapEventSummary(consecutive_capped_cycles=0)


def _row_to_dict(row: SizingCapEvent) -> Dict[str, Any]:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "cycle_id": row.cycle_id,
        "symbol": row.symbol,
        "strategy_id": row.strategy_id,
        "raw_weight": row.raw_weight,
        "final_weight": row.final_weight,
        "binding_constraint": row.binding_constraint,
        "was_capped": bool(row.was_capped),
    }
