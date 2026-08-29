"""SQLAlchemy-backed durable log of the operator's REAL Robinhood filled equity
orders, keyed by Robinhood's own ``order_id``.

Why this exists: ``data/robinhood_orders.py`` reconstructs closed round-trip
trades from filled orders via pure FIFO lot-matching, but its only persistence
is a daily JSON cache (``cache/robinhood_orders.json``) that is overwritten
wholesale on every fetch and has no history beyond whatever window Robinhood's
own order-history API happens to return. This module gives every fill a
durable, append-only home so the platform's realized-P&L record survives
cache eviction and keeps growing across every ingest, instead of being
re-derived from a shrinking window each time.

**Persist fills, not reconstructed closed trades.** A closed-trade dedup key
is unstable: FIFO pairs a sell with whichever buy lot happens to still be in
the fetch window, and that pairing SHIFTS once an older buy ages out of the
window on a later fetch — the same sell would then reconstruct into a
DIFFERENT ``ClosedTrade`` (different entry_ts/entry_price/pnl) under any key
derived from the trade itself, producing a duplicate, double-counted row.
Fills have a genuine natural key (Robinhood's ``order_id``) and their
persisted union only ever grows, so ``reconstruct_closed_trades()`` — pure,
tested, and UNCHANGED by this module — is simply re-run over the full
persisted fill history on every read. Recomputing FIFO over a few thousand
fills costs microseconds; there is no need for a separate closed-trades table.

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``validation/validation_history_store.py`` / ``sizing/cap_audit_store.py`` /
``desktop/run_history_store.py``'s convention exactly (own ``Base``, own
table, ``session_scope`` for writes, a ``readonly=True`` database-level
engine for read-only consumers like ``pilots/trade_history.py``).

**Sizing isolation (CONSTRAINT — load-bearing, not incidental):** this module
is READ-ONLY with respect to ``transactions_store.py``'s ``trades`` table and
must stay that way. Production sizing (``strategy_engine.py``'s aggregate
Kelly path, ``engine/advisory.py``'s own aggregate path) reads
``TransactionsStore.closed_trades_df()`` unfiltered by strategy — writing the
operator's manual, discretionary Robinhood trades into that table would
silently move live position sizing on every symbol. ``BrokerOrderFill`` /
``BrokerFillsStore`` never import ``transactions_store`` and are never
imported by anything under ``sizing/`` or ``execution/``
(enforced by ``tests/test_broker_fills_store.py``'s AST guard).

Import direction is one-way: this module imports ``data.robinhood_orders``
(``OrderFill``, ``reconstruct_closed_trades``); ``data/robinhood_orders.py``
never imports this module. Persistence happens at the CALLER's call site
(``data/robinhood_login_worker.py``), not inside ``fetch_filled_orders``
itself — avoiding a circular import between the two modules.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()


class BrokerOrderFill(Base):
    """One filled Robinhood equity order, keyed by Robinhood's own order id."""

    __tablename__ = "broker_order_fills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), nullable=False, unique=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(8), nullable=False)  # "buy" | "sell"
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # average execution price, USD/share
    filled_at = Column(DateTime, nullable=False, index=True)  # naive UTC
    first_seen_at = Column(DateTime, nullable=False)  # naive UTC
    last_seen_at = Column(DateTime, nullable=False, index=True)  # naive UTC
    raw_json = Column(Text, nullable=True)  # provenance blob (OrderFill.to_dict())


class BrokerInstrumentSymbol(Base):
    """Instrument-URL -> ticker resolver cache, durable across processes so a
    fresh ingest doesn't re-pay the ``get_symbol_by_url`` network cost for
    every previously-resolved instrument."""

    __tablename__ = "broker_instrument_symbols"

    instrument_url = Column(String(255), primary_key=True)
    symbol = Column(String(20), nullable=True)  # NULL = confirmed unresolvable
    resolved_at = Column(DateTime, nullable=False)  # naive UTC


def _naive_utc(dt: Optional[datetime]) -> datetime:
    """Normalize an aware or naive datetime to naive UTC, defaulting to now."""
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class BrokerFillsStore:
    """Durable, idempotent log of the operator's real Robinhood filled orders.

    ``readonly=True`` builds a DATABASE-LEVEL read-only engine (see
    ``db_config.create_readonly_db_engine``) and skips
    ``Base.metadata.create_all`` -- a readonly instance assumes the tables
    already exist (true once any write-mode store, i.e. the login worker's
    ingest, has run). Write methods RAISE (CONSTRAINT #4 -- never silently
    no-op a write); the caller wraps them in a best-effort try/except. Read
    methods degrade to an empty shape on any failure (CONSTRAINT #6).
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

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def record_fills(self, fills: Sequence[Any]) -> Dict[str, int]:
        """Insert-the-diff by ``order_id``. Idempotent: re-ingesting the same
        fills a second time inserts nothing new and reports 0 ``inserted``.

        A fill with an empty/missing ``order_id`` is skipped and counted --
        never given a fabricated key (CONSTRAINT #4), since a fabricated key
        could collide across genuinely distinct fills.

        On an ``order_id`` collision whose quantity/price/filled_at diverge
        from what's stored (e.g. Robinhood corrected a fill after the fact),
        the LATEST value wins -- a filled order's final cumulative_quantity/
        average_price is authoritative -- and the row is updated in place, a
        WARNING is logged naming both values, and ``divergent`` is
        incremented. Update-in-place keeps re-ingest idempotent even across
        a correction.

        Returns ``{"inserted": n, "updated": n, "skipped_no_order_id": n,
        "divergent": n}``. Raises on a readonly instance or a DB error
        (CONSTRAINT #4 -- never silently no-op a write).
        """
        if self._readonly:
            raise RuntimeError("BrokerFillsStore is read-only; cannot record fills.")

        counts = {"inserted": 0, "updated": 0, "skipped_no_order_id": 0, "divergent": 0}
        if not fills:
            return counts

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(self.Session) as session:
            order_ids = [str(f.order_id) for f in fills if str(f.order_id or "").strip()]
            existing: Dict[str, BrokerOrderFill] = {}
            if order_ids:
                for i in range(0, len(order_ids), 500):
                    chunk = order_ids[i:i + 500]
                    for row in (
                        session.query(BrokerOrderFill)
                        .filter(BrokerOrderFill.order_id.in_(chunk))
                        .all()
                    ):
                        existing[row.order_id] = row

            for f in fills:
                order_id = str(f.order_id or "").strip()
                if not order_id:
                    counts["skipped_no_order_id"] += 1
                    continue

                filled_at = _naive_utc(getattr(f, "timestamp", None))
                row = existing.get(order_id)
                if row is None:
                    session.add(
                        BrokerOrderFill(
                            order_id=order_id,
                            symbol=str(f.symbol).upper(),
                            side=str(f.side).lower(),
                            quantity=float(f.quantity),
                            price=float(f.price),
                            filled_at=filled_at,
                            first_seen_at=now,
                            last_seen_at=now,
                            raw_json=_safe_json_dumps(f),
                        )
                    )
                    counts["inserted"] += 1
                    continue

                diverges = (
                    row.symbol != str(f.symbol).upper()
                    or row.side != str(f.side).lower()
                    or not math.isclose(row.quantity, float(f.quantity), rel_tol=1e-9)
                    or not math.isclose(row.price, float(f.price), rel_tol=1e-9)
                    or row.filled_at != filled_at
                )
                if diverges:
                    logger.warning(
                        "BrokerFillsStore.record_fills: order_id=%s diverged on "
                        "re-fetch (stored qty=%.6f price=%.6f filled_at=%s -> "
                        "new qty=%.6f price=%.6f filled_at=%s); keeping the latest.",
                        order_id, row.quantity, row.price, row.filled_at,
                        f.quantity, f.price, filled_at,
                    )
                    row.symbol = str(f.symbol).upper()
                    row.side = str(f.side).lower()
                    row.quantity = float(f.quantity)
                    row.price = float(f.price)
                    row.filled_at = filled_at
                    row.raw_json = _safe_json_dumps(f)
                    counts["divergent"] += 1
                    counts["updated"] += 1
                row.last_seen_at = now

        return counts

    def record_instrument_symbols(self, mapping: Dict[str, Optional[str]]) -> int:
        """Upsert the instrument-URL -> symbol resolver cache. Returns the
        number of rows written. Raises on a readonly instance or DB error."""
        if self._readonly:
            raise RuntimeError("BrokerFillsStore is read-only; cannot record symbols.")
        if not mapping:
            return 0

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        n = 0
        with session_scope(self.Session) as session:
            keys = list(mapping.keys())
            existing = {}
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                for row in (
                    session.query(BrokerInstrumentSymbol)
                    .filter(BrokerInstrumentSymbol.instrument_url.in_(chunk))
                    .all()
                ):
                    existing[row.instrument_url] = row
            new_objects = []
            for url, symbol in mapping.items():
                sym = str(symbol).upper() if symbol else None
                row = existing.get(url)
                if row is None:
                    new_objects.append(
                        BrokerInstrumentSymbol(instrument_url=url, symbol=sym, resolved_at=now)
                    )
                else:
                    row.symbol = sym
                    row.resolved_at = now
                n += 1
            if new_objects:
                session.add_all(new_objects)
        return n

    # ------------------------------------------------------------------ #
    # Reads -- degrade to an empty shape on any failure (CONSTRAINT #6)
    # ------------------------------------------------------------------ #

    def all_fills(self) -> List[Any]:
        """Every persisted fill as ``data.robinhood_orders.OrderFill``,
        oldest first. Degrades to ``[]`` on any read failure."""
        from data.robinhood_orders import OrderFill

        try:
            session = self.Session()
            try:
                rows = session.query(BrokerOrderFill).order_by(BrokerOrderFill.filled_at.asc()).all()
                out = []
                for r in rows:
                    out.append(
                        OrderFill(
                            symbol=r.symbol,
                            side=r.side,
                            quantity=r.quantity,
                            price=r.price,
                            timestamp=r.filled_at.replace(tzinfo=timezone.utc),
                            order_id=r.order_id,
                        )
                    )
                return out
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("BrokerFillsStore.all_fills: %s", exc)
            return []

    def closed_trades(
        self, *, symbol: Optional[str] = None, limit: Optional[int] = None, offset: int = 0
    ) -> List[Any]:
        """Closed round-trips reconstructed (pure FIFO, unmodified) from every
        persisted fill, most-recent-exit-first, optionally filtered to one
        symbol and paginated. Degrades to ``[]`` on any failure."""
        from data.robinhood_orders import reconstruct_closed_trades

        try:
            fills = self.all_fills()
            if symbol:
                sym_upper = symbol.upper().strip()
                fills = [f for f in fills if f.symbol == sym_upper]
            trades = reconstruct_closed_trades(fills)
            trades.sort(key=lambda t: t.exit_ts, reverse=True)
            if limit is None:
                return trades[offset:]
            return trades[offset : offset + limit]
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("BrokerFillsStore.closed_trades: %s", exc)
            return []

    def closed_trade_count(self, *, symbol: Optional[str] = None) -> int:
        """Total closed-trade count (ignoring pagination). Degrades to 0."""
        try:
            return len(self.closed_trades(symbol=symbol))
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrokerFillsStore.closed_trade_count: %s", exc)
            return 0

    def last_exit_ts_by_symbol(self) -> Dict[str, datetime]:
        """Most recent SELL fill timestamp per symbol (UTC-aware). Drives
        universe retention (§3 of the implementation plan). Degrades to
        ``{}`` on any failure."""
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(BrokerOrderFill)
                    .filter(BrokerOrderFill.side == "sell")
                    .all()
                )
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrokerFillsStore.last_exit_ts_by_symbol: %s", exc)
            return {}

        latest: Dict[str, datetime] = {}
        for r in rows:
            ts = r.filled_at.replace(tzinfo=timezone.utc)
            if r.symbol not in latest or ts > latest[r.symbol]:
                latest[r.symbol] = ts
        return latest

    def distinct_symbols(self) -> List[str]:
        """Every distinct symbol with at least one persisted fill, sorted.
        Degrades to ``[]`` on any failure."""
        try:
            session = self.Session()
            try:
                rows = session.query(BrokerOrderFill.symbol).distinct().all()
                return sorted({r[0] for r in rows})
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrokerFillsStore.distinct_symbols: %s", exc)
            return []

    def last_ingested_at(self) -> Optional[datetime]:
        """Most recent ``last_seen_at`` across all fills, or ``None`` if the
        store is empty / unreachable."""
        try:
            session = self.Session()
            try:
                row = (
                    session.query(BrokerOrderFill)
                    .order_by(BrokerOrderFill.last_seen_at.desc())
                    .first()
                )
                return row.last_seen_at.replace(tzinfo=timezone.utc) if row else None
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrokerFillsStore.last_ingested_at: %s", exc)
            return None

    def instrument_symbol_map(self) -> Dict[str, Optional[str]]:
        """Every persisted instrument-URL -> symbol resolution. Degrades to
        ``{}`` on any failure."""
        try:
            session = self.Session()
            try:
                rows = session.query(BrokerInstrumentSymbol).all()
                return {r.instrument_url: r.symbol for r in rows}
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrokerFillsStore.instrument_symbol_map: %s", exc)
            return {}


def _safe_json_dumps(fill: Any) -> Optional[str]:
    try:
        import json

        return json.dumps(fill.to_dict())
    except Exception:  # noqa: BLE001 - provenance blob is best-effort only
        return None


def ingest_filled_orders(*, force: bool = True) -> Dict[str, int]:
    """Fetch real Robinhood filled orders and persist them.

    Meant to be called from inside the isolated Robinhood login worker
    (``data/robinhood_login_worker.py``), where ``os.environ["RH_LOGIN_WORKER"]
    == "1"`` and a real, authenticated ``robin_stocks`` session exists in this
    process -- ``data.robinhood_orders.fetch_filled_orders`` raises
    ``RobinhoodApprovalRequired`` outside that context, which propagates here
    unmodified (never swallowed) so the caller can distinguish "no session to
    ingest with" from "ingest ran and found nothing."

    Never fabricates data (CONSTRAINT #4): a fetch failure is not swallowed
    into an empty-but-successful result; it propagates. The best-effort
    boundary belongs to the caller (mirroring every other ``ingest_*``/
    ``_record_*_to_db`` best-effort call site in this codebase), not this
    function.

    Returns the ``record_fills`` count dict plus ``"n_fetched"``.
    """
    from data.robinhood_orders import fetch_filled_orders

    fills = fetch_filled_orders(force=force)
    store = BrokerFillsStore()
    counts = store.record_fills(fills)
    counts["n_fetched"] = len(fills)
    return counts


def recently_closed_symbols(
    *, retention_days: int, max_symbols: int, now: Optional[datetime] = None
) -> List[str]:
    """Symbols whose most recent SELL fill is within ``retention_days`` of
    ``now`` (default: current UTC time), newest-exit-first, capped at
    ``max_symbols``. ``retention_days <= 0`` returns ``[]`` (retention off).

    Degrades to ``[]`` on any failure (CONSTRAINT #6) -- a store outage must
    never shrink the analysis universe below what held/watchlist symbols
    alone would produce.
    """
    if retention_days <= 0 or max_symbols <= 0:
        return []
    try:
        as_of = now or datetime.now(timezone.utc)
        cutoff = as_of - timedelta(days=retention_days)
        by_symbol = BrokerFillsStore(readonly=True).last_exit_ts_by_symbol()
        recent = [(sym, ts) for sym, ts in by_symbol.items() if ts >= cutoff]
        recent.sort(key=lambda pair: pair[1], reverse=True)
        return [sym for sym, _ts in recent[:max_symbols]]
    except Exception as exc:  # noqa: BLE001 - dead-letter: never shrinks the universe
        logger.warning("recently_closed_symbols failed (%s) -- universe unaffected.", exc)
        return []
