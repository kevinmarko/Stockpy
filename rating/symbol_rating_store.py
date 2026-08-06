"""SQLAlchemy-backed durable log of every symbol's per-cycle rating
(``rating.symbol_rating.classify_tier``'s GOOD/BAD verdict on that cycle's
``final_score``, plus whether the symbol was held at the time).

Why this exists: the platform already computes a per-symbol 0-100
``final_score`` and a 4-tier Action Signal every cycle
(``strategy_engine.py::evaluate_security()``), but that number is transient
-- overwritten every cycle in ``dashboard_df`` / ``state_snapshot.json``,
with no history a later cycle (or an operator) can query. This module gives
every rating event a durable, timestamped home so:

  1. A downstream pipeline-wiring task can ask "how many consecutive BAD
     cycles has this symbol had" (``get_consecutive_bad_cycles``) and, when
     ``settings.SYMBOL_RATING_AUTO_DROP_ENABLED`` is on, subtract a
     long-BAD, non-held symbol from the resolved tracked universe
     (``get_excluded_symbols``) -- exactly the ``sizing/cap_audit_store.py``
     precedent for ``SIZING_CAP_ESCALATION_ENABLED``, applied to rating
     history instead of sizing-cap history.
  2. An operator (or a later API/webapp surface) can inspect a symbol's
     rating history (``get_recent``) and manually clear a bad streak
     (``reinclude``) without losing the audit trail of why it was excluded.

This module itself performs no classification and holds no opinion on
``settings.SYMBOL_RATING_ENABLED`` (the write-gating flag, mirroring
``SIZING_CAP_AUDIT_ENABLED``'s role for ``sizing/cap_audit_store.py``) or
``settings.SYMBOL_RATING_AUTO_DROP_ENABLED``/``SYMBOL_RATING_DROP_THRESHOLD_CYCLES``
(the auto-drop behavior flags) -- it just persists and reads back whatever
it's asked to. Checking those flags before writing, and acting on
``get_excluded_symbols``'s result, is a downstream caller's job
(``pipeline/production_steps.py``, ``data/portfolio_sync.py`` -- both
explicitly out of scope for this module).

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``sizing/cap_audit_store.py`` / ``transactions_store.py`` /
``desktop/run_history_store.py``'s convention exactly (own ``Base``, own
table, ``session_scope`` for writes, a ``readonly=True`` database-level
engine for read-only consumers that skips ``Base.metadata.create_all``).

Write methods (``record_ratings``, ``reinclude``) intentionally still raise
on failure (CONSTRAINT #4 -- never silently no-op a write); the caller is
expected to wrap them in its own best-effort try/except, exactly like
``CapAuditStore.record_cap_events``'s documented contract, so a DB hiccup
can never affect the run's own scoring/sizing decisions -- only the durable
rating history lags. Read methods (``get_consecutive_bad_cycles``,
``get_excluded_symbols``, ``get_recent``) degrade to ``0``/``set()``/``[]``
and NEVER raise (CONSTRAINT #6) -- ``get_excluded_symbols`` in particular
sits on the universe-resolution hot path a downstream task will build, and a
DB outage there must fail OPEN (return an empty exclusion set, i.e. exclude
nobody) rather than fail closed and silently shrink the tracked universe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

_VALID_TIERS = ("GOOD", "BAD")

# Safety bound for the per-symbol history scan in get_consecutive_bad_cycles
# / get_excluded_symbols -- this should never realistically be hit (a
# consecutive-BAD streak this long would already have triggered auto-drop
# many cycles earlier whenever that feature is enabled), but it avoids an
# unbounded table scan against a pathological/never-pruned history.
_MAX_STREAK_SCAN_ROWS = 500


class SymbolRatingEvent(Base):
    __tablename__ = "symbol_rating_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    # Identifies the run cycle this event belongs to, mirroring
    # SizingCapEvent.cycle_id -- nullable so a caller that doesn't track
    # cycle identity can still log an individual rating.
    cycle_id = Column(String(64), nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    score = Column(Float, nullable=False)
    # Descriptive only -- the STRONG BUY/BUY/HOLD/RISK REDUCE label from
    # strategy_engine.py, carried along for context. NOT used for
    # classification; `tier` (below) is the authoritative GOOD/BAD verdict,
    # produced by rating.symbol_rating.classify_tier from `score` alone.
    action_signal = Column(String(20), nullable=True)
    tier = Column(String(10), nullable=False)  # "GOOD" | "BAD"
    is_held = Column(Boolean, nullable=False, default=False)


class SymbolRatingStore:
    """Durable per-symbol rating history.

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

    def record_ratings(self, events: List[Dict[str, Any]], *, cycle_id: Optional[str] = None) -> None:
        """Persist a whole cycle's rating events in ONE transaction.

        Each item in ``events`` is a dict with keys: ``symbol`` (required),
        ``score`` (required float), ``action_signal`` (optional str),
        ``tier`` (required, must be exactly ``"GOOD"`` or ``"BAD"`` --
        raises ``ValueError`` rather than silently coercing an unexpected
        value, since a mis-typed tier would corrupt the consecutive-BAD
        streak this table exists to answer correctly), ``is_held`` (required
        bool), ``timestamp`` (defaults to ``datetime.now(timezone.utc)``,
        stripped to naive-UTC before storage -- same convention as
        ``CapAuditStore.record_cap_events``).

        ``cycle_id`` is applied to every event that doesn't supply its own
        (matching ``record_cap_events``'s per-event-override behavior).

        Raises ``RuntimeError`` if this store is ``readonly=True``. No-ops
        on an empty ``events`` list. Write methods intentionally still raise
        on failure (CONSTRAINT #4) -- the caller wraps this in its own
        best-effort try/except so a DB hiccup can never affect the run's own
        scoring decisions, only the durable rating history.
        """
        if self._readonly:
            raise RuntimeError("SymbolRatingStore is read-only; cannot record ratings.")
        if not events:
            return

        with session_scope(self.Session) as session:
            for ev in events:
                symbol = ev["symbol"]
                score = float(ev["score"])
                tier = ev["tier"]
                if tier not in _VALID_TIERS:
                    raise ValueError(f"tier must be one of {_VALID_TIERS!r}, got {tier!r}")
                is_held = ev["is_held"]
                ts = ev.get("timestamp") or datetime.now(timezone.utc)
                if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                session.add(SymbolRatingEvent(
                    timestamp=ts,
                    cycle_id=ev.get("cycle_id", cycle_id),
                    symbol=str(symbol).upper(),
                    score=score,
                    action_signal=ev.get("action_signal"),
                    tier=tier,
                    is_held=bool(is_held),
                ))

    def get_consecutive_bad_cycles(self, symbol: str) -> int:
        """How many of the symbol's MOST RECENT rating events are BAD,
        counted back-to-back from the newest until the first GOOD row (or
        exhaustion of history).

        Ordered by ``id`` DESC, not ``timestamp`` DESC -- ``id`` is
        autoincrement and therefore an unambiguous write order, whereas
        timestamps are caller-supplied and could theoretically collide or
        skew (e.g. two events recorded with the same wall-clock second, or a
        backfill script writing historical timestamps out of insertion
        order). ``id`` order is authoritative for "most recent" throughout
        this module.

        Returns 0 when the most recent row is GOOD, or when the symbol has
        no rating history at all. Scan is capped at the most recent
        ``_MAX_STREAK_SCAN_ROWS`` rows for the symbol (see that constant's
        comment). Degrades to 0 -- never raises -- on any DB read failure
        (CONSTRAINT #6), logged via ``logger.warning``.
        """
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(SymbolRatingEvent)
                    .filter(SymbolRatingEvent.symbol == str(symbol).upper())
                    .order_by(SymbolRatingEvent.id.desc())
                    .limit(_MAX_STREAK_SCAN_ROWS)
                    .all()
                )
                consecutive = 0
                for row in rows:
                    if row.tier != "BAD":
                        break
                    consecutive += 1
                return consecutive
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to 0
            logger.warning("SymbolRatingStore.get_consecutive_bad_cycles(%s): %s", symbol, exc)
            return 0

    def get_excluded_symbols(
        self, *, threshold_cycles: int, known_symbols: Optional[Iterable[str]] = None,
    ) -> "set[str]":
        """The set of symbols currently eligible for auto-drop: a
        consecutive-BAD streak of at least ``threshold_cycles`` AND a most
        recent rating event with ``is_held=False``.

        Pass ``known_symbols`` (the caller's already-resolved universe) to
        avoid a full distinct-symbol table scan; when omitted, every
        distinct symbol ever rated is considered.

        This is read on the universe-resolution hot path a downstream task
        will build, so it degrades to ``set()`` -- never raises -- on ANY
        failure (CONSTRAINT #6): a DB hiccup here must fail OPEN (exclude
        nobody) rather than fail closed and silently shrink the tracked
        universe. Note this is the mirror image of ``sizing.kelly``-style
        risk controls, where failing open would be the *unsafe* direction --
        here, "don't know" must default to "keep tracking it", exactly the
        same CONSTRAINT #4/#6 reasoning ``rating.symbol_rating.classify_tier``
        applies to a NaN score.
        """
        try:
            session = self.Session()
            try:
                if known_symbols is not None:
                    symbols = {str(s).upper() for s in known_symbols}
                else:
                    symbols = {
                        row[0] for row in session.query(SymbolRatingEvent.symbol).distinct().all()
                    }

                excluded: "set[str]" = set()
                for symbol in symbols:
                    rows = (
                        session.query(SymbolRatingEvent)
                        .filter(SymbolRatingEvent.symbol == symbol)
                        .order_by(SymbolRatingEvent.id.desc())
                        .limit(_MAX_STREAK_SCAN_ROWS)
                        .all()
                    )
                    if not rows:
                        continue
                    if rows[0].is_held:
                        continue  # currently held -- never excluded, see rating.symbol_rating.should_exclude
                    consecutive = 0
                    for row in rows:
                        if row.tier != "BAD":
                            break
                        consecutive += 1
                    if consecutive >= threshold_cycles:
                        excluded.add(symbol)
                return excluded
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to set() (fail open)
            logger.warning("SymbolRatingStore.get_excluded_symbols: %s", exc)
            return set()

    def reinclude(self, symbol: str) -> None:
        """Manual-override escape hatch: immediately break a symbol's
        consecutive-BAD streak without deleting its rating history.

        Inserts one synthetic ``SymbolRatingEvent`` row with
        ``tier="GOOD"``, ``score=50.0`` (neutral -- not a fabricated real
        score, CONSTRAINT #4 note: this value is explicitly synthetic and
        identifiable as such via ``cycle_id="manual_reinclude"``), and
        ``is_held=False``. The next ``get_consecutive_bad_cycles`` call for
        this symbol sees this row first (highest ``id``) and returns 0.

        Deliberately does NOT delete or edit any prior row: the real history
        of why the symbol was excluded stays intact for audit purposes --
        only a new event masking it forward is added.

        Raises ``RuntimeError`` if this store is ``readonly=True`` (mirrors
        ``record_ratings``).
        """
        if self._readonly:
            raise RuntimeError("SymbolRatingStore is read-only; cannot reinclude.")

        with session_scope(self.Session) as session:
            session.add(SymbolRatingEvent(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                cycle_id="manual_reinclude",
                symbol=str(symbol).upper(),
                score=50.0,
                action_signal=None,
                tier="GOOD",
                is_held=False,
            ))

    def get_recent(self, symbol: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Most-recent-first (by ``id`` DESC -- see ``get_consecutive_bad_cycles``'s
        docstring for why ``id``, not ``timestamp``, is this module's
        authoritative recency order) list of JSON-safe rating-event dicts,
        optionally filtered to one symbol.

        Degrades to ``[]`` -- never raises -- on any read failure
        (CONSTRAINT #6), mirroring ``CapAuditStore.get_recent`` exactly.
        """
        try:
            session = self.Session()
            try:
                query = session.query(SymbolRatingEvent)
                if symbol is not None:
                    query = query.filter(SymbolRatingEvent.symbol == str(symbol).upper())
                rows = query.order_by(SymbolRatingEvent.id.desc()).limit(limit).all()
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("SymbolRatingStore.get_recent: %s", exc)
            return []


def _row_to_dict(row: SymbolRatingEvent) -> Dict[str, Any]:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "cycle_id": row.cycle_id,
        "symbol": row.symbol,
        "score": row.score,
        "action_signal": row.action_signal,
        "tier": row.tier,
        "is_held": bool(row.is_held),
    }
