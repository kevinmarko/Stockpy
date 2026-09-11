"""SQLAlchemy-backed durable log of symbol views.

This module provides a minimal, isolated store for tracking the last time a
symbol was viewed. Never read by anything under signals/, sizing/, or
execution/ (enforced by ``tests/test_symbol_view_store.py::
test_symbol_view_store_import_guard``, an AST-based guard over those three
directories).

**Deliberate deviation from the original plan** (``.claude/
weekly-digest_implementation_plan.md``'s WP-A row calls for "a minimal,
own-Base/table/session_scope store recording (symbol, viewed_at)", and
``.claude/weekly-digest_task.md`` describes it as an "append-only log"):
this store is upsert-by-symbol (one row per symbol, ``viewed_at`` updated in
place on a repeat view), NOT append-only (one new row per view). The only
consumer of this data, ``get_recently_viewed_symbols()``, only ever needs
"was this symbol viewed within the last N days" for the weekly digest's
personalization filter — full view history (how many times, or exactly
when, a symbol was viewed on prior occasions) has no consumer anywhere in
this codebase. Upsert-by-symbol is functionally equivalent for that single
need, is simpler, and — for a single-operator tool with an unbounded
universe of symbols that could each be viewed arbitrarily many times over
the platform's lifetime — avoids unbounded table growth that a true
append-only log would have with zero corresponding benefit. If a future
consumer needs real view history (view counts, a timeline, etc.), this
table would need to become genuinely append-only (a plain INSERT per view,
with ``get_recently_viewed_symbols`` becoming a
``MAX(viewed_at) GROUP BY symbol`` query) rather than reusing this one as-is.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

# A real ticker/symbol passed to this store's only two call sites
# (api/data_api.py::explain_ticker's `symbol` path param,
# api/pilots_api.py::get_symbol_detail's `ticker` path param) is always a
# plain equity/index ticker, never an option contract — neither endpoint
# is reachable with an OCC-format option symbol (those are handled
# elsewhere, e.g. the options chain/order-ticket endpoints). 20 is
# generous for that real domain (even an unusually long share-class
# ticker like "BRK.B" is far under it). Guards against ever attempting to
# persist an unbounded-length string (e.g. an adversarial/malformed path
# segment reaching record_view() before the endpoint's own length/shape
# validation runs) into the `symbol` column below. SQLite does not
# enforce VARCHAR length and would silently accept it, but a
# Postgres-backed deployment (settings.DATABASE_URL) would raise a
# DataError on insert — caught by both call sites' broad try/except
# (CONSTRAINT #6) either way, so this is a defensive skip, not a
# correctness requirement.
_MAX_SYMBOL_LENGTH = 20


class SymbolView(Base):
    """Most-recent-view log, one row per symbol (see module docstring for
    why this is upsert-by-symbol rather than a true append-only log)."""

    __tablename__ = "symbol_views"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(_MAX_SYMBOL_LENGTH), nullable=False, unique=True, index=True)
    viewed_at = Column(DateTime, nullable=False, index=True)  # naive UTC


class SymbolViewStore:
    """Durable most-recent-view store, keyed by symbol (upsert, not
    append-only — see module docstring)."""

    def __init__(self, db_url: Optional[str] = None, *, readonly: bool = False) -> None:
        # A fresh store is constructed per-request at both call sites
        # (api/data_api.py::explain_ticker, api/pilots_api.py::
        # get_symbol_detail) rather than cached as a module-level
        # singleton — this matches the established, universal convention
        # in this codebase (HistoricalStore(), PaperAccountStore(), etc.
        # are likewise constructed fresh in every api/*.py handler that
        # needs one; no store in either service uses a module-level
        # cache), not a one-off inconsistency.
        db_url = db_url or resolve_database_url()
        self._readonly = readonly
        if readonly:
            from db_config import create_readonly_db_engine
            self.engine = create_readonly_db_engine(db_url)
        else:
            self.engine = create_db_engine(db_url)
            Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_view(self, symbol: str) -> None:
        """Record or update the most-recent-view timestamp for a symbol
        (upsert-by-symbol — see module docstring).

        The upsert itself is a SELECT-then-INSERT/UPDATE, not a single
        atomic statement (SQLAlchemy's ORM has no dialect-portable
        ``INSERT ... ON CONFLICT`` short enough to justify a per-dialect
        branch for one small table). That leaves a real check-then-act
        race window on ``symbol`` (``unique=True``): two near-simultaneous
        first-ever views of the same brand-new symbol (a realistic
        scenario — both call sites are plain ``def`` FastAPI handlers
        dispatched to a thread pool, so genuine thread-level concurrency
        is possible) can both see ``row is None`` and both attempt
        ``session.add(...)``; the loser's ``session.commit()`` (inside
        ``session_scope``, which propagates rather than swallows) raises
        ``IntegrityError`` on the UNIQUE constraint. Rather than let that
        propagate and silently drop the loser's view (the only thing
        stopping that before this fix was both callers' own broad
        ``except Exception``), retry once as an UPDATE against the
        winner's now-committed row.
        """
        if self._readonly:
            raise RuntimeError("SymbolViewStore is read-only; cannot record view.")

        if not symbol:
            return
        symbol = symbol.strip().upper()
        if not symbol or len(symbol) > _MAX_SYMBOL_LENGTH:
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            with session_scope(self.Session) as session:
                row = session.query(SymbolView).filter(SymbolView.symbol == symbol).first()
                if row is None:
                    session.add(SymbolView(symbol=symbol, viewed_at=now))
                else:
                    row.viewed_at = now
        except IntegrityError:
            # Lost the INSERT race — a concurrent record_view() call for
            # this same symbol committed first. Retry as an UPDATE against
            # its row instead of losing this view.
            with session_scope(self.Session) as session:
                row = session.query(SymbolView).filter(SymbolView.symbol == symbol).first()
                if row is not None:
                    row.viewed_at = now
                else:
                    # Vanishingly unlikely (the winner's row would have to
                    # be deleted between our failed insert and this retry)
                    # -- fall back to inserting again; if this also races,
                    # the caller's own broad except (CONSTRAINT #6,
                    # best-effort) degrades exactly as it did before this
                    # fix, rather than retrying indefinitely.
                    session.add(SymbolView(symbol=symbol, viewed_at=now))

    def get_recently_viewed_symbols(self, days: int = 14) -> List[str]:
        """Get symbols viewed within the last `days` days, most recent first.

        Never raises (CONSTRAINT #6) -- including for a non-comparable
        ``days`` (e.g. ``None``), which the old code let through a bare
        ``days <= 0`` check sitting OUTSIDE the try/except below; that
        comparison itself is now inside it.
        """
        try:
            if days <= 0:
                return []
            session = self.Session()
            try:
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
                rows = (
                    session.query(SymbolView.symbol)
                    # Secondary sort key so two views landing in the same
                    # timestamp tick (a plausible tie on a coarse clock, or
                    # a tight batch of calls) still resolve to a
                    # deterministic "most recent first" order rather than
                    # whatever order SQLite happens to return ties in.
                    .order_by(SymbolView.viewed_at.desc(), SymbolView.id.desc())
                    .filter(SymbolView.viewed_at >= cutoff)
                    .all()
                )
                return [r[0] for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("SymbolViewStore.get_recently_viewed_symbols: %s", exc)
            return []
