"""SQLAlchemy-backed durable log of RLHF Calibration Review Queue proposals --
hypothetical, paper-only AI trade proposals (symbol/action/rationale/
confidence/technical-context) that a human operator rates 1-5 stars with an
optional corrective comment.

Why this is a SEPARATE store, not a TransactionsStore extension: a proposal
here never touches real capital, never reaches ``OrderManager``/``BrokerBase``,
and is never a real fill. ``TransactionsStore`` is the single source of truth
for REAL-trade MAE/MFE evaluation (``evaluation_engine.py``, the calibration
reliability diagrams in ``pilots/calibration.py``) -- mixing hypothetical AI
proposals into that table would silently corrupt every one of those real-trade
statistics with data that was never actually traded. This module has no
foreign key to ``transactions_store.py`` and no P&L/cash-ledger concept by
design; it is a rating queue, nothing more.

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``transactions_store.py`` / ``sizing/cap_audit_store.py``'s convention exactly
(own ``Base``, own table, ``session_scope`` for writes, a ``readonly=True``
database-level engine for read-only consumers).

Gated by ``settings.RLHF_CALIBRATION_ENABLED`` (the write-endpoint master
switch built by a later round on top of this module -- this module itself has
no opinion on that flag, it just persists whatever it's asked to). Auto-
approval (``settings.RLHF_CALIBRATION_AUTO_APPROVE_ENABLED`` /
``RLHF_CALIBRATION_CONFIDENCE_THRESHOLD``) is decided once, here, inside
``create_proposal`` -- not duplicated at any call site.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope
from settings import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

_VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
_VALID_RATINGS = {1, 2, 3, 4, 5}


class ProposalNotFoundError(Exception):
    """Raised by ``submit_review`` when no proposal exists with the given id."""

    def __init__(self, proposal_id: int) -> None:
        super().__init__(f"RLHF calibration proposal not found: {proposal_id!r}")
        self.proposal_id = proposal_id


class ProposalAlreadyReviewedError(Exception):
    """Raised by ``submit_review`` when the proposal has already been reviewed
    (including auto-approved proposals -- a human can't "re-review" one)."""

    def __init__(self, proposal_id: int) -> None:
        super().__init__(f"RLHF calibration proposal already reviewed: {proposal_id!r}")
        self.proposal_id = proposal_id


class RlhfCalibrationProposal(Base):
    __tablename__ = "rlhf_calibration_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    action = Column(String(10), nullable=False)
    quantity = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    rationale = Column(Text, nullable=False)
    # [0,1] fraction -- this repo's convention for every conviction-shaped
    # field (Trade.conviction, DiscoveryCandidate.conviction), NOT 0-100.
    confidence = Column(Float, nullable=False)
    rsi = Column(Float, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    # JSON-encoded string (json.dumps) for any additional context beyond
    # rsi/sentiment_score. Plain string column -- caller passes a dict,
    # _row_to_dict deserializes it back.
    extra_context = Column(Text, nullable=True)
    status = Column(String(10), nullable=False, default="pending")
    human_rating = Column(Integer, nullable=True)
    human_correction = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    auto_approved = Column(Boolean, nullable=False, default=False)
    sft_exported = Column(Boolean, nullable=False, default=False)


class RlhfCalibrationStore:
    """Durable log of RLHF Calibration Review Queue proposals.

    ``readonly=True`` builds a DATABASE-LEVEL read-only engine (see
    ``db_config.create_readonly_db_engine``) and skips
    ``Base.metadata.create_all`` -- a readonly instance assumes the table
    already exists (true once any write-mode store has run at least once).
    Read methods degrade to an empty/neutral shape on any failure, including
    a genuinely missing table (CONSTRAINT #6); write methods raise on a
    readonly instance and on any DB failure (CONSTRAINT #4 -- never silently
    no-op or fabricate a successful write).
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

    # -- writes ---------------------------------------------------------

    def create_proposal(
        self,
        symbol: str,
        action: str,
        rationale: str,
        confidence: float,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        rsi: Optional[float] = None,
        sentiment_score: Optional[float] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Records a new hypothetical AI trade proposal. Returns the new row's id.

        Auto-approval (``settings.RLHF_CALIBRATION_AUTO_APPROVE_ENABLED`` and
        ``confidence >= settings.RLHF_CALIBRATION_CONFIDENCE_THRESHOLD``) is
        decided here, once: the row is created with ``auto_approved=True,
        status="reviewed", reviewed_at=<now>`` and ``human_rating`` stays
        ``None`` (never fabricated -- CONSTRAINT #4). Raises on any DB failure
        or a readonly instance (CONSTRAINT #4 -- write methods never silently
        no-op).
        """
        if self._readonly:
            raise RuntimeError("RlhfCalibrationStore is read-only; cannot create a proposal.")

        try:
            action_upper = action.upper()
        except AttributeError:
            raise ValueError(f"invalid action: {action!r}")
        if action_upper not in _VALID_ACTIONS:
            raise ValueError(f"invalid action: {action!r}")

        try:
            confidence_val = float(confidence)
        except (TypeError, ValueError):
            raise ValueError(f"confidence must be in [0,1], got {confidence!r}")
        if not (0.0 <= confidence_val <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {confidence!r}")

        auto_approved = False
        status = "pending"
        reviewed_at: Optional[datetime] = None
        if (
            settings.RLHF_CALIBRATION_AUTO_APPROVE_ENABLED
            and confidence_val >= settings.RLHF_CALIBRATION_CONFIDENCE_THRESHOLD
        ):
            auto_approved = True
            status = "reviewed"
            reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        with session_scope(self.Session) as session:
            proposal = RlhfCalibrationProposal(
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                symbol=symbol.upper().strip(),
                action=action_upper.strip(),
                quantity=float(quantity) if quantity is not None else None,
                price=float(price) if price is not None else None,
                rationale=rationale,
                confidence=confidence_val,
                rsi=float(rsi) if rsi is not None else None,
                sentiment_score=float(sentiment_score) if sentiment_score is not None else None,
                extra_context=json.dumps(extra_context) if extra_context is not None else None,
                status=status,
                auto_approved=auto_approved,
                reviewed_at=reviewed_at,
            )
            session.add(proposal)
            session.flush()  # populate the autoincrement PK before the session closes
            proposal_id = int(proposal.id)
        return proposal_id

    def submit_review(
        self, id: int, human_rating: int, human_correction: Optional[str] = None
    ) -> Dict[str, Any]:
        """Records a human rating (+ optional corrective comment) for one proposal.

        Raises ``ProposalNotFoundError`` if ``id`` doesn't exist,
        ``ProposalAlreadyReviewedError`` if it's already reviewed (including an
        auto-approved row), and ``ValueError`` if ``human_rating`` isn't 1-5.
        The API layer built in a later round maps these to HTTP 404/409/400.
        """
        if self._readonly:
            raise RuntimeError("RlhfCalibrationStore is read-only; cannot submit a review.")

        with session_scope(self.Session) as session:
            row = (
                session.query(RlhfCalibrationProposal)
                .filter(RlhfCalibrationProposal.id == id)
                .first()
            )
            if row is None:
                raise ProposalNotFoundError(id)
            if row.status == "reviewed":
                raise ProposalAlreadyReviewedError(id)
            if human_rating not in _VALID_RATINGS:
                raise ValueError(f"human_rating must be in 1..5, got {human_rating!r}")

            row.status = "reviewed"
            row.human_rating = int(human_rating)
            row.human_correction = human_correction
            row.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.flush()
            result = _row_to_dict(row)
        return result

    def mark_sft_exported(self, ids: List[int]) -> int:
        """Marks the given proposal ids ``sft_exported=True``. Returns the count
        updated. Raises on a readonly instance or any DB failure."""
        if self._readonly:
            raise RuntimeError(
                "RlhfCalibrationStore is read-only; cannot mark proposals as SFT-exported."
            )
        if not ids:
            return 0

        with session_scope(self.Session) as session:
            updated = (
                session.query(RlhfCalibrationProposal)
                .filter(RlhfCalibrationProposal.id.in_(ids))
                .update({"sft_exported": True}, synchronize_session=False)
            )
        return int(updated)

    # -- reads ------------------------------------------------------------
    # Degrade to an empty/neutral shape on any failure -- never raise
    # (CONSTRAINT #6, dead-letter resilience); matches TransactionsStore's
    # read/write asymmetry.

    def get_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most-recent-first list of pending proposals as plain dicts."""
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(RlhfCalibrationProposal)
                    .filter(RlhfCalibrationProposal.status == "pending")
                    .order_by(RlhfCalibrationProposal.created_at.desc())
                    .limit(limit)
                    .all()
                )
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("RlhfCalibrationStore.get_pending: %s", exc)
            return []

    def get_by_id(self, id: int) -> Optional[Dict[str, Any]]:
        """One proposal as a plain dict, or ``None`` if missing or on error."""
        try:
            session = self.Session()
            try:
                row = (
                    session.query(RlhfCalibrationProposal)
                    .filter(RlhfCalibrationProposal.id == id)
                    .first()
                )
                return _row_to_dict(row) if row is not None else None
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to None
            logger.warning("RlhfCalibrationStore.get_by_id(%s): %s", id, exc)
            return None

    def get_unexported_five_star(self) -> List[Dict[str, Any]]:
        """Reviewed proposals with a 5-star ``human_rating`` not yet marked
        ``sft_exported`` -- the SFT export batch (``POST /rlhf/export-sft``
        in ``api/pilots_api.py``). Most-recent-first, matching ``get_pending``'s
        ordering convention. Degrades to ``[]`` on any failure -- never raises
        (CONSTRAINT #6, same dead-letter contract as every other read method
        on this class)."""
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(RlhfCalibrationProposal)
                    .filter(
                        RlhfCalibrationProposal.human_rating == 5,
                        RlhfCalibrationProposal.sft_exported.is_(False),
                    )
                    .order_by(RlhfCalibrationProposal.created_at.desc())
                    .all()
                )
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("RlhfCalibrationStore.get_unexported_five_star: %s", exc)
            return []

    def get_summary_stats(self) -> Dict[str, Any]:
        """Aggregate queue stats for the review-queue dashboard.

        ``average_human_rating`` is computed ONLY over rows with a non-null
        ``human_rating`` (i.e. excludes auto-approved rows, which never carry
        a rating) -- ``None`` when there are zero rated rows, never a
        fabricated ``0.0`` (CONSTRAINT #4). Degrades to this same shape with
        all-zero/None values on any failure -- never raises.
        """
        try:
            session = self.Session()
            try:
                pending_count = (
                    session.query(RlhfCalibrationProposal)
                    .filter(RlhfCalibrationProposal.status == "pending")
                    .count()
                )
                reviewed_count = (
                    session.query(RlhfCalibrationProposal)
                    .filter(RlhfCalibrationProposal.status == "reviewed")
                    .count()
                )
                auto_approved_count = (
                    session.query(RlhfCalibrationProposal)
                    .filter(RlhfCalibrationProposal.auto_approved.is_(True))
                    .count()
                )
                sft_exported_count = (
                    session.query(RlhfCalibrationProposal)
                    .filter(RlhfCalibrationProposal.sft_exported.is_(True))
                    .count()
                )
                ratings = [
                    r[0]
                    for r in session.query(RlhfCalibrationProposal.human_rating)
                    .filter(RlhfCalibrationProposal.human_rating.isnot(None))
                    .all()
                ]

                average_human_rating = (sum(ratings) / len(ratings)) if ratings else None
                distribution = {str(i): 0 for i in range(1, 6)}
                for rating in ratings:
                    key = str(int(rating))
                    if key in distribution:
                        distribution[key] += 1

                return {
                    "pending_count": pending_count,
                    "reviewed_count": reviewed_count,
                    "average_human_rating": average_human_rating,
                    "rating_distribution": distribution,
                    "auto_approved_count": auto_approved_count,
                    "sft_exported_count": sft_exported_count,
                }
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to zeroed shape
            logger.warning("RlhfCalibrationStore.get_summary_stats: %s", exc)
            return _empty_summary_stats()


class _OfflineRlhfCalibrationStore:
    """Read-only stand-in used when the configured DB backend is unreachable.

    ``RlhfCalibrationStore()`` construction does an eager connection (``Base
    .metadata.create_all``), so a network/DNS outage on a remote backend
    (e.g. a Postgres/Supabase ``DATABASE_URL``) raises before a single query
    is ever made. Mirrors ``transactions_store._OfflineTransactionsStore`` /
    ``sizing.cap_audit_store._OfflineCapAuditStore``: read methods degrade to
    empty/neutral results; write methods intentionally still raise
    (CONSTRAINT #4 -- never fabricate a successful write against an
    unreachable DB).
    """

    def create_proposal(self, *args, **kwargs) -> int:
        raise RuntimeError(
            "RlhfCalibrationStore is unavailable (DB unreachable); cannot create a proposal."
        )

    def submit_review(self, *args, **kwargs) -> Dict[str, Any]:
        raise RuntimeError(
            "RlhfCalibrationStore is unavailable (DB unreachable); cannot submit a review."
        )

    def mark_sft_exported(self, ids: List[int]) -> int:
        raise RuntimeError(
            "RlhfCalibrationStore is unavailable (DB unreachable); cannot mark proposals "
            "as SFT-exported."
        )

    def get_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        return []

    def get_by_id(self, id: int) -> Optional[Dict[str, Any]]:
        return None

    def get_unexported_five_star(self) -> List[Dict[str, Any]]:
        return []

    def get_summary_stats(self) -> Dict[str, Any]:
        return _empty_summary_stats()


def _empty_summary_stats() -> Dict[str, Any]:
    return {
        "pending_count": 0,
        "reviewed_count": 0,
        "average_human_rating": None,
        "rating_distribution": {str(i): 0 for i in range(1, 6)},
        "auto_approved_count": 0,
        "sft_exported_count": 0,
    }


def _row_to_dict(row: RlhfCalibrationProposal) -> Dict[str, Any]:
    extra_context: Optional[Dict[str, Any]] = None
    if row.extra_context:
        try:
            extra_context = json.loads(row.extra_context)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "RlhfCalibrationStore: malformed extra_context for id=%s: %s", row.id, exc
            )
            extra_context = None

    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "symbol": row.symbol,
        "action": row.action,
        "quantity": row.quantity,
        "price": row.price,
        "rationale": row.rationale,
        "confidence": row.confidence,
        "rsi": row.rsi,
        "sentiment_score": row.sentiment_score,
        "extra_context": extra_context,
        "status": row.status,
        "human_rating": row.human_rating,
        "human_correction": row.human_correction,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "auto_approved": bool(row.auto_approved),
        "sft_exported": bool(row.sft_exported),
    }
