from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text, UniqueConstraint, func
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


#: ``TrendsDownloadBatch.status`` values. ``complete`` means every planned
#: window came back. ``partial`` means some did (the daemon does not stitch
#: these). ``failed`` means none did.
BATCH_COMPLETE = "complete"
BATCH_PARTIAL = "partial"
BATCH_FAILED = "failed"


class TrendsDownloadBatch(Base):
    """One download pass for one query term: the set of overlapping windows
    the daemon fetched together.

    Before this table existed every window got a random ``uuid4`` id and
    nothing linked the windows of one download, so a term's raw rows piled up
    across every refresh (96 windows for AAL in the live DB) with no way to
    tell which six belonged together. Raw rows now carry
    ``window_id = f"{batch_id}/{window_start}/{window_end}"``, so a batch's
    windows are found by id prefix and ``raw_trends_downloads`` needs no new
    column (``create_all`` adds this table; no migration).
    """

    __tablename__ = "trends_download_batches"

    batch_id = Column(String(64), primary_key=True)
    query_term = Column(String(255), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    windows_expected = Column(Integer, nullable=False)
    windows_fetched = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)
    reason = Column(Text, nullable=True)


def batch_window_id(batch_id: str, window_start: date, window_end: date) -> str:
    """The deterministic ``raw_trends_downloads.window_id`` of one window in a batch."""
    return f"{batch_id}/{window_start.isoformat()}/{window_end.isoformat()}"


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

    def save_stitched_series(
        self,
        query_term: str,
        series: list[dict],
        stitched_at: datetime,
        *,
        replace: bool = False,
    ) -> None:
        """Persist a stitched series for ``query_term``.

        ``replace=True`` deletes the term's existing rows and inserts the new
        series in one transaction. Use it for a fresh stitch: each stitch has
        its own scale, so upserting only the new stitch's dates would leave
        older dates on an older, incompatible scale (the live DB had 36 such
        orphan days for AAL, a level step in the stored series). A reader
        never sees a half-written series: the delete and inserts commit
        together or not at all.

        ``replace=False`` (the default) keeps the original per-date upsert.
        """
        if self._readonly:
            raise RuntimeError("TrendsStore is read-only")
        if replace and not series:
            # Replacing with nothing would wipe the term's stored series.
            # Refuse rather than leave the dashboard with no data at all.
            logger.warning(
                "TrendsStore.save_stitched_series: refusing replace=True with an "
                "empty series for %s; stored series left unchanged", query_term,
            )
            return
        with session_scope(self.Session) as session:
            if replace:
                session.query(StitchedGoogleTrends).filter_by(query_term=query_term).delete(
                    synchronize_session=False
                )
                session.add_all(
                    StitchedGoogleTrends(
                        query_term=query_term,
                        date=item["date"],
                        value=item["value"],
                        stitched_at=stitched_at,
                    )
                    for item in series
                )
                return
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

    # ── Download batches ──────────────────────────────────────────────────

    def record_batch(
        self,
        *,
        batch_id: str,
        query_term: str,
        started_at: datetime,
        finished_at: datetime,
        windows_expected: int,
        windows: list[tuple[str, list[dict]]],
        status: str,
        reason: Optional[str] = None,
    ) -> None:
        """Write one download batch and its raw windows in one transaction.

        ``windows`` is ``[(window_id, [{"date", "value"}, ...]), ...]`` with
        ids from ``batch_window_id``. The ids are new for every batch, so rows
        are inserted directly rather than upserted one query per row like
        ``insert_raw_window``.
        """
        if self._readonly:
            raise RuntimeError("TrendsStore is read-only")
        with session_scope(self.Session) as session:
            session.add(TrendsDownloadBatch(
                batch_id=batch_id,
                query_term=query_term,
                started_at=started_at,
                finished_at=finished_at,
                windows_expected=windows_expected,
                windows_fetched=len(windows),
                status=status,
                reason=reason,
            ))
            for window_id, rows in windows:
                session.add_all(
                    RawTrendsDownload(
                        query_term=query_term,
                        window_id=window_id,
                        date=row["date"],
                        value=row["value"],
                        downloaded_at=finished_at,
                    )
                    for row in rows
                )

    def latest_batch(self, query_term: str, *, complete_only: bool = True) -> Optional[dict]:
        """The most recent batch for a term as a dict, or ``None``.

        ``complete_only`` skips partial and failed batches. Never raises: a
        missing table or a DB error reads as ``None``.
        """
        try:
            session = self.Session()
            try:
                query = session.query(TrendsDownloadBatch).filter_by(query_term=query_term)
                if complete_only:
                    query = query.filter(TrendsDownloadBatch.status == BATCH_COMPLETE)
                row = query.order_by(TrendsDownloadBatch.started_at.desc()).first()
                return _batch_to_dict(row) if row is not None else None
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.latest_batch: %s", exc)
            return None

    def load_batch_windows(self, batch_id: str) -> list[RawTrendsDownload]:
        """The raw rows of one batch, ordered by window id then date (so each
        window's rows are contiguous and windows come in chronological
        order, because ids embed ISO dates). ``[]`` on any error."""
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(RawTrendsDownload)
                    .filter(RawTrendsDownload.window_id.like(f"{batch_id}/%"))
                    .order_by(RawTrendsDownload.window_id.asc(), RawTrendsDownload.date.asc())
                    .all()
                )
                session.expunge_all()
                return rows
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.load_batch_windows: %s", exc)
            return []

    def last_attempts(self) -> dict[str, dict[str, Optional[datetime]]]:
        """Per term, the start time of its latest batch of any status
        (``"attempt"``) and of its latest complete batch (``"complete"``).

        The daemon derives its refresh schedule from this, so the schedule
        survives a restart (the old in-memory throttle refetched everything
        on every daemon start). ``{}`` on any error.
        """
        try:
            session = self.Session()
            try:
                out: dict[str, dict[str, Optional[datetime]]] = {}
                for term, ts in (
                    session.query(TrendsDownloadBatch.query_term, func.max(TrendsDownloadBatch.started_at))
                    .group_by(TrendsDownloadBatch.query_term)
                    .all()
                ):
                    out[term] = {"attempt": ts, "complete": None}
                for term, ts in (
                    session.query(TrendsDownloadBatch.query_term, func.max(TrendsDownloadBatch.started_at))
                    .filter(TrendsDownloadBatch.status == BATCH_COMPLETE)
                    .group_by(TrendsDownloadBatch.query_term)
                    .all()
                ):
                    out.setdefault(term, {"attempt": ts, "complete": None})["complete"] = ts
                return out
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.last_attempts: %s", exc)
            return {}

    def list_terms_with_batches(self) -> list[str]:
        """Terms with at least one complete batch, sorted. ``[]`` on any error."""
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(TrendsDownloadBatch.query_term)
                    .filter(TrendsDownloadBatch.status == BATCH_COMPLETE)
                    .distinct()
                    .all()
                )
                return sorted(r[0] for r in rows)
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TrendsStore.list_terms_with_batches: %s", exc)
            return []

    def prune_raw_batches(self, query_term: str, keep: int) -> int:
        """Bound a term's raw data to its newest ``keep`` batches of each kind.

        - Complete batches: the newest ``keep`` are kept.
        - Partial/failed batches: the newest ``keep`` are kept, and once a
          complete batch exists, any older than the oldest kept complete one
          are dropped too. This bounds growth for a term that never completes
          (Google has no data for it), which an earlier version missed.
        - Legacy raw rows written before batches existed (``window_id`` a bare
          ``uuid4`` with no ``/``) are deleted only once at least one complete
          batch exists, so a term is never left with no raw data.

        ``keep < 1`` is treated as 1. Returns the number of raw rows deleted.
        """
        if self._readonly:
            raise RuntimeError("TrendsStore is read-only")
        keep = max(1, int(keep))
        with session_scope(self.Session) as session:
            batches = (
                session.query(TrendsDownloadBatch)
                .filter_by(query_term=query_term)
                .order_by(TrendsDownloadBatch.started_at.desc())
                .all()
            )
            complete = [b for b in batches if b.status == BATCH_COMPLETE]
            other = [b for b in batches if b.status != BATCH_COMPLETE]
            doomed = [b.batch_id for b in complete[keep:]]
            cutoff = complete[: keep][-1].started_at if complete else None
            for i, b in enumerate(other):
                if i >= keep or (cutoff is not None and b.started_at < cutoff):
                    doomed.append(b.batch_id)

            deleted = 0
            for batch_id in doomed:
                deleted += (
                    session.query(RawTrendsDownload)
                    .filter(RawTrendsDownload.query_term == query_term)
                    .filter(RawTrendsDownload.window_id.like(f"{batch_id}/%"))
                    .delete(synchronize_session=False)
                )
            if doomed:
                session.query(TrendsDownloadBatch).filter(
                    TrendsDownloadBatch.batch_id.in_(doomed)
                ).delete(synchronize_session=False)
            if complete:
                deleted += (
                    session.query(RawTrendsDownload)
                    .filter(RawTrendsDownload.query_term == query_term)
                    .filter(~RawTrendsDownload.window_id.like("%/%"))
                    .delete(synchronize_session=False)
                )
            return deleted


def _batch_to_dict(row: TrendsDownloadBatch) -> dict[str, Any]:
    return {
        "batch_id": row.batch_id,
        "query_term": row.query_term,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "windows_expected": row.windows_expected,
        "windows_fetched": row.windows_fetched,
        "status": row.status,
        "reason": row.reason,
    }
