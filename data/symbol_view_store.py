"""SQLAlchemy-backed durable log of symbol views.

This module provides a minimal, isolated store for tracking the last time a symbol was viewed.
Never read by anything under signals/, sizing/, or execution/.
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
    """A log of symbol views."""

    __tablename__ = "symbol_views"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, unique=True, index=True)
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
        """Record or update a view for a symbol."""
        if self._readonly:
            raise RuntimeError("SymbolViewStore is read-only; cannot record view.")

        if not symbol:
            return
        symbol = symbol.strip().upper()
        if not symbol:
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
