from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()


class RawTrendsDownload(Base):
    __tablename__ = "raw_trends_downloads"
    __table_args__ = (
        UniqueConstraint("query_term", "window_id", "date", name="uq_raw_trends_downloads_term_window_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_term = Column(String(255), nullable=False, index=True)
    window_id = Column(String(255), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    value = Column(Float, nullable=False)
    downloaded_at = Column(DateTime, nullable=False)


class StitchedGoogleTrends(Base):
    __tablename__ = "stitched_google_trends"

    query_term = Column(String(255), primary_key=True)
    date = Column(Date, primary_key=True)
    value = Column(Float, nullable=False)
    stitched_at = Column(DateTime, nullable=False)


class TrendsStore:
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

    def insert_raw_window(self, query_term: str, window_id: str, data: list[dict], downloaded_at: datetime) -> None:
        """Upsert one raw-download window's rows, keyed on
        ``(query_term, window_id, date)`` (see ``RawTrendsDownload``'s
        ``UniqueConstraint``). A re-download of an already-stored window
        (e.g. a daemon cycle re-fetching an overlapping window) updates the
        existing row's value/downloaded_at in place instead of appending a
        duplicate -- mirrors ``save_stitched_series``'s query-then-update-or-
        insert idiom below for consistency within this module.
        """
        if self._readonly:
            raise RuntimeError("TrendsStore is read-only")
        with session_scope(self.Session) as session:
            for item in data:
                existing = session.query(RawTrendsDownload).filter_by(
                    query_term=query_term, window_id=window_id, date=item["date"]
                ).first()
                if existing:
                    existing.value = item["value"]
                    existing.downloaded_at = downloaded_at
                else:
                    session.add(RawTrendsDownload(
                        query_term=query_term,
                        window_id=window_id,
                        date=item["date"],
                        value=item["value"],
                        downloaded_at=downloaded_at
                    ))

    def load_raw_windows(self, query_term: str) -> list[RawTrendsDownload]:
        try:
            session = self.Session()
            try:
                rows = session.query(RawTrendsDownload).filter_by(query_term=query_term).order_by(RawTrendsDownload.date.asc()).all()
                session.expunge_all()
                return rows
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.load_raw_windows: %s", exc)
            return []

    def get_query_terms_with_raw_windows(self) -> list[str]:
        """Returns distinct ``query_term`` values with at least one raw window
        on file, ordered alphabetically for a deterministic pick.

        Used by diagnostic/demo consumers (e.g. ``GET /data/trends/stitch-demo``)
        that need to discover which symbol(s) the opt-in daemon job has actually
        populated, rather than guessing a single hardcoded symbol that may never
        match the real, operator-configured ingested universe (``settings.
        DEFAULT_TICKERS`` has no fixed member -- see ``desktop/daemon_runtime.py``'s
        ``maybe_refresh_google_trends``, the sole writer of this table).
        """
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(RawTrendsDownload.query_term)
                    .distinct()
                    .order_by(RawTrendsDownload.query_term.asc())
                    .all()
                )
                return [r[0] for r in rows]
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.get_query_terms_with_raw_windows: %s", exc)
            return []

    def save_stitched_series(self, query_term: str, series: list[dict], stitched_at: datetime) -> None:
        if self._readonly:
            raise RuntimeError("TrendsStore is read-only")
        with session_scope(self.Session) as session:
            for item in series:
                existing = session.query(StitchedGoogleTrends).filter_by(query_term=query_term, date=item["date"]).first()
                if existing:
                    existing.value = item["value"]
                    existing.stitched_at = stitched_at
                else:
                    session.add(StitchedGoogleTrends(
                        query_term=query_term,
                        date=item["date"],
                        value=item["value"],
                        stitched_at=stitched_at
                    ))

    def get_stitched_series(self, query_term: str, as_of: Optional[datetime] = None) -> list[dict]:
        try:
            session = self.Session()
            try:
                query = session.query(StitchedGoogleTrends).filter_by(query_term=query_term)
                if as_of:
                    query = query.filter(StitchedGoogleTrends.stitched_at <= as_of)
                rows = query.order_by(StitchedGoogleTrends.date.asc()).all()
                return [{"date": r.date, "value": r.value} for r in rows]
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.get_stitched_series: %s", exc)
            return []
