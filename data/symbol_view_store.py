"""SQLAlchemy-backed durable log of symbol views for the Weekly Digest feature.

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``data/broker_fills_store.py``'s convention exactly (own ``Base``, own
table, ``session_scope`` for writes).

**Sizing isolation (CONSTRAINT — load-bearing, not incidental):** this module
is never read by anything under ``signals/``, ``sizing/``, or ``execution/``
(enforced by ``tests/test_symbol_view_store.py``'s AST guard).
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


class SymbolView(Base):
    """One symbol view event."""

    __tablename__ = "symbol_views"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    viewed_at = Column(DateTime, nullable=False, index=True)  # naive UTC


class SymbolViewStore:
    """Durable log of symbol views."""

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

    def record_view(self, symbol: str) -> None:
        """Record that a symbol was viewed."""
        if self._readonly:
            raise RuntimeError("SymbolViewStore is read-only; cannot record views.")

        sym_upper = str(symbol).upper().strip()
        if not sym_upper:
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope(self.Session) as session:
            session.add(SymbolView(symbol=sym_upper, viewed_at=now))

    def get_last_viewed(self, symbol: str) -> Optional[datetime]:
        """Return the most recent view timestamp for the symbol."""
        sym_upper = str(symbol).upper().strip()
        if not sym_upper:
            return None

        try:
            session = self.Session()
            try:
                row = (
                    session.query(SymbolView)
                    .filter(SymbolView.symbol == sym_upper)
                    .order_by(SymbolView.viewed_at.desc())
                    .first()
                )
                return row.viewed_at.replace(tzinfo=timezone.utc) if row else None
            finally:
                session.close()
        except Exception as exc:
            logger.warning("SymbolViewStore.get_last_viewed: %s", exc)
            return None

    def get_recently_viewed_symbols(self, days: int) -> List[str]:
        """Return symbols viewed within the last `days`, newest-first."""
        if days <= 0:
            return []

        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            session = self.Session()
            try:
                rows = (
                    session.query(SymbolView.symbol, SymbolView.viewed_at)
                    .filter(SymbolView.viewed_at >= cutoff)
                    .order_by(SymbolView.viewed_at.desc())
                    .all()
                )
                
                seen = set()
                result = []
                for sym, _ in rows:
                    if sym not in seen:
                        seen.add(sym)
                        result.append(sym)
                return result
            finally:
                session.close()
        except Exception as exc:
            logger.warning("SymbolViewStore.get_recently_viewed_symbols: %s", exc)
            return []
