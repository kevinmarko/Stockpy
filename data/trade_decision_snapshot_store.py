"""Forward-only capture of "what did we know at trade-open" for the
Retrospective Learning Loop (Trade Journal).

Why this exists: ``data/paper_account_store.py``'s ``PaperClosedTrade`` row
records what actually happened (entry/exit price, realized PnL, holding
period) but nothing about WHY the trade was opened -- was it a real,
signal-driven entry (conviction, IVR/VRP/trend-bias context) or a manual
Quick-Trade click with no model reasoning behind it at all? Blurring those
two into one narrative would be a direct CONSTRAINT #4 violation (see
CLAUDE.md's "Retrospective Learning Loop" framing) -- presenting a manual
trade's outcome as if a signal justified it, or vice versa.

**This store is deliberately, permanently forward-only.** A snapshot is
written ONLY at the moment a paper position is newly opened (see
``PaperAccountStore``'s call sites), capturing whatever real provenance/
signal context the caller actually has *right now*. There is no backfill
path and none should ever be added: reconstructing a plausible-looking "the
model probably thought X" for a trade that predates this feature (or for an
automated writer this feature hasn't been wired into yet) is fabrication
even when the guess turns out numerically close -- it presents an inference
as a record. A trade with no matching row here means exactly one thing:
"decision context was not captured for this trade" -- rendered as such by
``pilots/retrospective_composer.py``, never silently upgraded to "manual" or
"signal-driven" by inference.

Natural key: ``(symbol, strategy_id, entry_ts)`` -- the exact same triple
``PaperClosedTrade`` carries (copied verbatim from ``PaperPosition.entry_ts``
at close time), so a retrospective composer can look up "was a snapshot
captured for THIS closed trade" with no new join key or schema change to
either existing table. ``entry_ts`` is set exactly once per position-open
episode (``data/paper_account_store.py``'s own convention: left untouched
while averaging in, reset only on a genuine flat->open or flip-through-zero)
so this triple is stable for the life of one open/close cycle.

Own ``Base``/table/``session_scope``/``readonly=True`` convention, matching
``data/broker_fills_store.py`` / ``validation/validation_history_store.py`` /
``desktop/run_history_store.py``. Write methods RAISE (CONSTRAINT #4 -- never
silently no-op a write); the caller (``PaperAccountStore``) wraps every call
in a best-effort try/except so a snapshot-store hiccup never blocks or
rolls back the paper fill it's describing. Read methods degrade to
``None``/``[]`` on any failure, including a cold-start "table doesn't exist
yet" (CONSTRAINT #6).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

_TABLE_NAME = "trade_decision_snapshots"


class TradeDecisionSnapshot(Base):
    """One forward-only capture of decision context at the moment a paper
    position was newly opened. See module docstring for the natural key.
    """

    __tablename__ = _TABLE_NAME

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(64), nullable=False, index=True)
    strategy_id = Column(String(100), nullable=False, index=True)
    pilot_id = Column(String(100), nullable=True)
    # Copied verbatim from PaperPosition.entry_ts at the moment of capture --
    # naive UTC, matching PaperClosedTrade.entry_ts's own convention exactly.
    entry_ts = Column(DateTime, nullable=False, index=True)
    captured_at = Column(DateTime, nullable=False)
    # "manual" | "automated:<source>" (e.g. "automated:options_auto_scan").
    # Never inferred after the fact -- the caller states this explicitly at
    # the moment of capture, or the row is not written at all.
    provenance = Column(String(64), nullable=False)
    # The model's own predicted win probability / signal conviction at entry,
    # when a real one exists (e.g. the Stage 4 ML Meta-Labeler's prob_win, or
    # None for a manual trade / an automated path with no scored conviction).
    # Never fabricated -- NULL, not 0.0, when unavailable (CONSTRAINT #4).
    conviction = Column(Float, nullable=True)
    # Macro/vol regime label if the caller had one available at entry
    # (e.g. "RISK_ON" / "RECESSION" / a VIX-bucket label) -- free text,
    # deliberately not constrained to the HMM's own enum since callers other
    # than the macro engine may supply a coarser or differently-sourced tag.
    regime = Column(String(32), nullable=True)
    # JSON blob of whatever real, named factor values the caller actually had
    # (e.g. {"ivr": 62.3, "vrp": 0.041, "trend_bias": "Bullish", "vix": 18.2,
    # "short_delta": 0.16, "credit_to_width_ratio": 0.34, "strategy":
    # "Put Credit Spread"}). Always a dict of REAL values the caller
    # observed -- never a template with fabricated/interpolated fields.
    factors_json = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)


def _naive_utc(dt: Optional[datetime]) -> datetime:
    """Normalize an aware or naive datetime to naive UTC, defaulting to now."""
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _safe_factors_json(symbol: str, strategy_id: str, factors: Optional[Dict[str, Any]]) -> Optional[str]:
    """JSON-encode a factors dict, dropping (never substituting a
    placeholder for) a value that fails to serialize -- CONSTRAINT #4."""
    if not factors:
        return None
    safe_factors: Dict[str, Any] = {}
    for key, value in factors.items():
        try:
            json.dumps(value)
            safe_factors[key] = value
        except (TypeError, ValueError):
            logger.warning(
                "trade_decision_snapshot(%s/%s): dropping non-JSON-serializable factor %r",
                symbol, strategy_id, key,
            )
    return json.dumps(safe_factors) if safe_factors else None


def build_snapshot_row(
    *,
    symbol: str,
    strategy_id: str,
    entry_ts: datetime,
    provenance: str,
    pilot_id: Optional[str] = None,
    conviction: Optional[float] = None,
    regime: Optional[str] = None,
    factors: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> TradeDecisionSnapshot:
    """Construct (but do not persist) a validated ``TradeDecisionSnapshot``
    ORM object. Exists so a caller that already holds an open SQLAlchemy
    ``Session`` on the SAME physical database (``PaperAccountStore``'s
    ``_maybe_record_decision_snapshot``) can ``session.add()`` it directly
    as part of its OWN transaction, instead of opening a second connection
    mid-transaction -- SQLite allows only one writer at a time, and a
    second writer attempting to write while the first's transaction is
    still open blocks until ``busy_timeout`` elapses and then raises
    ``database is locked`` (confirmed empirically; this is the exact
    write-path counterpart of the read/write contention
    ``_init_transactions_bridge``'s own docstring already documents and
    fixes for the sibling transactions_store bridge by sharing the
    session -- see ``PaperAccountStore.__init__``'s comment for the
    write-path version of that fix).

    Raises ``ValueError`` on missing required fields -- the SAME validation
    ``TradeDecisionSnapshotStore.record_snapshot`` applies, so the two
    write paths (a caller with its own session vs. a bare
    ``record_snapshot`` call) can never silently drift apart.
    """
    if not symbol or not strategy_id or not provenance:
        raise ValueError("build_snapshot_row requires symbol, strategy_id, and provenance.")
    return TradeDecisionSnapshot(
        symbol=symbol.upper(),
        strategy_id=strategy_id,
        pilot_id=pilot_id,
        entry_ts=_naive_utc(entry_ts),
        captured_at=datetime.now(timezone.utc).replace(tzinfo=None),
        provenance=str(provenance),
        conviction=float(conviction) if conviction is not None else None,
        regime=regime,
        factors_json=_safe_factors_json(symbol, strategy_id, factors),
        notes=notes,
    )


class TradeDecisionSnapshotStore:
    """Forward-only decision-context capture. See module docstring."""

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

    def _table_exists(self) -> bool:
        try:
            return inspect(self.engine).has_table(_TABLE_NAME)
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def record_snapshot(
        self,
        *,
        symbol: str,
        strategy_id: str,
        entry_ts: datetime,
        provenance: str,
        pilot_id: Optional[str] = None,
        conviction: Optional[float] = None,
        regime: Optional[str] = None,
        factors: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Persist one forward-only decision snapshot. RAISES on a readonly
        instance or a DB error (CONSTRAINT #4 -- never silently no-op a
        write); the caller is responsible for wrapping this in a
        best-effort try/except so a snapshot failure never blocks the paper
        fill it describes.

        ``provenance`` is required and is never inferred by this store --
        the caller states it explicitly ("manual" or "automated:<source>").
        ``factors`` must be a plain, JSON-serializable dict of real observed
        values; a value that fails to serialize is dropped from the stored
        blob (never substituted with a placeholder) and logged.
        """
        if self._readonly:
            raise RuntimeError("TradeDecisionSnapshotStore is read-only; cannot record a snapshot.")
        row = build_snapshot_row(
            symbol=symbol, strategy_id=strategy_id, entry_ts=entry_ts, provenance=provenance,
            pilot_id=pilot_id, conviction=conviction, regime=regime, factors=factors, notes=notes,
        )
        with session_scope(self.Session) as session:
            session.add(row)

    # ------------------------------------------------------------------ #
    # Reads -- never raise (CONSTRAINT #6)
    # ------------------------------------------------------------------ #

    def get_snapshot(
        self, *, symbol: str, strategy_id: str, entry_ts: datetime
    ) -> Optional[Dict[str, Any]]:
        """Look up the snapshot captured for one specific (symbol,
        strategy_id, entry_ts) trade-open. Returns ``None`` -- never a
        fabricated/inferred record -- when no matching row exists (a
        pre-feature trade, an un-wired automated writer, or a genuine read
        failure). If more than one row somehow matches the same key (should
        not happen in normal operation), the most recently captured one
        wins.
        """
        if not symbol or not strategy_id or entry_ts is None:
            return None
        try:
            if not self._table_exists():
                return None
            with session_scope(self.Session) as session:
                row = (
                    session.query(TradeDecisionSnapshot)
                    .filter_by(symbol=symbol.upper(), strategy_id=strategy_id, entry_ts=_naive_utc(entry_ts))
                    .order_by(TradeDecisionSnapshot.captured_at.desc())
                    .first()
                )
                if row is None:
                    return None
                return _row_to_dict(row)
        except Exception as exc:
            logger.warning("get_snapshot(%s/%s) failed: %s", symbol, strategy_id, exc)
            return None

    def get_snapshots_batch(
        self, keys: List[tuple]
    ) -> Dict[tuple, Dict[str, Any]]:
        """Batch form of ``get_snapshot`` for a retrospective list view --
        avoids one query per row. ``keys`` is a list of
        ``(symbol, strategy_id, entry_ts)`` tuples. Returns a dict keyed by
        the SAME tuples (entry_ts normalized to naive UTC) for whichever
        keys had a matching row; a key with no snapshot is simply absent
        from the result -- never present with a fabricated value.
        """
        result: Dict[tuple, Dict[str, Any]] = {}
        if not keys:
            return result
        try:
            if not self._table_exists():
                return result
            symbols = {str(k[0]).upper() for k in keys if k and k[0]}
            if not symbols:
                return result
            with session_scope(self.Session) as session:
                rows = (
                    session.query(TradeDecisionSnapshot)
                    .filter(TradeDecisionSnapshot.symbol.in_(symbols))
                    .order_by(TradeDecisionSnapshot.captured_at.asc())
                    .all()
                )
                # Last-write-wins per (symbol, strategy_id, entry_ts); ascending
                # capture order means a later duplicate overwrites an earlier one.
                by_key: Dict[tuple, Dict[str, Any]] = {}
                for row in rows:
                    by_key[(row.symbol, row.strategy_id, row.entry_ts)] = _row_to_dict(row)
            for symbol, strategy_id, entry_ts in keys:
                if entry_ts is None:
                    continue
                lookup_key = (str(symbol).upper(), strategy_id, _naive_utc(entry_ts))
                if lookup_key in by_key:
                    result[(symbol, strategy_id, entry_ts)] = by_key[lookup_key]
            return result
        except Exception as exc:
            logger.warning("get_snapshots_batch failed: %s", exc)
            return {}


def _row_to_dict(row: TradeDecisionSnapshot) -> Dict[str, Any]:
    factors: Optional[Dict[str, Any]] = None
    if row.factors_json:
        try:
            factors = json.loads(row.factors_json)
        except (TypeError, ValueError):
            factors = None
    return {
        "symbol": row.symbol,
        "strategy_id": row.strategy_id,
        "pilot_id": row.pilot_id,
        "entry_ts": row.entry_ts,
        "captured_at": row.captured_at,
        "provenance": row.provenance,
        "conviction": row.conviction,
        "regime": row.regime,
        "factors": factors,
        "notes": row.notes,
    }
