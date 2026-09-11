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
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

# A real ticker/symbol is always short (the longest legitimate values in
# this codebase — options OCC symbols, "BRK.B"-style share classes — are
# well under this). Guards against ever attempting to persist an
# unbounded-length string (e.g. an adversarial/malformed path segment
# reaching record_view() before api/data_api.py::explain_ticker's own
# length/shape validation runs) into the `symbol` column below. SQLite
# does not enforce VARCHAR length and would silently accept it, but a
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
        (upsert-by-symbol — see module docstring)."""
        if self._readonly:
            raise RuntimeError("SymbolViewStore is read-only; cannot record view.")

        if not symbol:
            return
        symbol = symbol.strip().upper()
        if not symbol or len(symbol) > _MAX_SYMBOL_LENGTH:
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(self.Session) as session:
            row = session.query(SymbolView).filter(SymbolView.symbol == symbol).first()
            if row is None:
                session.add(SymbolView(symbol=symbol, viewed_at=now))
            else:
                row.viewed_at = now

    def get_recently_viewed_symbols(self, days: int = 14) -> List[str]:
        """Get symbols viewed within the last `days` days, most recent first."""
        if days <= 0:
            return []
            
        try:
            session = self.Session()
            try:
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
                rows = (
                    session.query(SymbolView.symbol)
                    .filter(SymbolView.viewed_at >= cutoff)
                    .order_by(SymbolView.viewed_at.desc())
                    .all()
                )
                return [r[0] for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("SymbolViewStore.get_recently_viewed_symbols: %s", exc)
            return []
