"""SQLAlchemy-backed durable store for live-trade approval proposals -- the
human-in-the-loop gate sitting between ``broker_live_execution_mcp.py``'s
``execute_live_trade`` (which now only PROPOSES an order) and
``confirm_live_trade`` (which submits to the real broker, and only once the
operator has approved).

Why a store instead of the old in-memory ``_pending_orders`` dict: an
in-memory dict has no genuine human-approval step between "an agent asked for
this order" and "the order executes" -- any second tool call, automated or
not, could immediately confirm it. Routing proposal creation, approval, and
consumption through a durable row lets the Pilots PWA (a separate process)
be the thing that actually flips ``status`` to ``"approved"``/``"rejected"``,
which is the real enforcement boundary this module exists to create.

The backend is resolved through ``db_config.py`` (SQLite by default,
Postgres/Supabase when ``DATABASE_URL`` is set), matching
``rlhf_calibration_store.py`` / ``data/paper_account_store.py``'s convention
exactly (own ``Base``, own table, ``session_scope`` for writes, a
``readonly=True`` database-level engine for read-only consumers).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import Column, DateTime, Float, String
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

# 5 minutes -- matches the old _pending_orders dict's TTL exactly.
PROPOSAL_TTL_SECONDS = 300

_PENDING = "pending_approval"
_APPROVED = "approved"
_REJECTED = "rejected"
_EXPIRED = "expired"
_EXECUTING = "executing"
_EXECUTED = "executed"
_FAILED = "failed"


class LiveTradeProposalNotFoundError(Exception):
    """Raised when no proposal exists with the given token."""

    def __init__(self, token: str) -> None:
        super().__init__(f"Live trade proposal not found: {token!r}")
        self.token = token


class LiveTradeProposalAlreadyDecidedError(Exception):
    """Raised by approve_proposal/reject_proposal when the proposal is no
    longer pending_approval (already approved, already rejected, or expired --
    all three are "not pending anymore")."""

    def __init__(self, token: str) -> None:
        super().__init__(f"Live trade proposal already decided: {token!r}")
        self.token = token


class LiveTradeProposal(Base):
    __tablename__ = "live_trade_proposals"

    token = Column(String(64), primary_key=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    qty = Column(Float, nullable=False)
    order_type = Column(String(10), nullable=False)
    limit_price = Column(Float, nullable=True)
    strategy_id = Column(String(50), nullable=False, default="mcp-agent")
    proposed_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String(20), nullable=False, default=_PENDING)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(50), nullable=True)
    broker_order_id = Column(String(100), nullable=True)
    error_message = Column(String(500), nullable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LiveTradeProposalStore:
    """Durable log of live-trade approval proposals.

    ``readonly=True`` builds a DATABASE-LEVEL read-only engine (see
    ``db_config.create_readonly_db_engine``) and skips
    ``Base.metadata.create_all``. Read methods degrade to an empty/neutral
    shape on any failure (CONSTRAINT #6); write methods raise on a readonly
    instance and on any DB failure (CONSTRAINT #4 -- never silently no-op or
    fabricate a successful write).
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
        side: str,
        qty: float,
        order_type: str,
        limit_price: Optional[float] = None,
        strategy_id: str = "mcp-agent",
    ) -> str:
        """Records a new pending-approval live trade proposal. Returns the new
        row's token. Raises ``ValueError`` on bad input, ``RuntimeError`` if
        this instance is readonly."""
        if self._readonly:
            raise RuntimeError(
                "LiveTradeProposalStore is read-only; cannot create a proposal."
            )

        if not symbol or not str(symbol).strip():
            raise ValueError(f"invalid symbol: {symbol!r}")
        if not side or not str(side).strip():
            raise ValueError(f"invalid side: {side!r}")
        try:
            qty_val = float(qty)
        except (TypeError, ValueError):
            raise ValueError(f"qty must be > 0, got {qty!r}")
        if not (qty_val > 0):
            raise ValueError(f"qty must be > 0, got {qty!r}")

        token = uuid.uuid4().hex
        now = _now()
        with session_scope(self.Session) as session:
            proposal = LiveTradeProposal(
                token=token,
                symbol=symbol.upper().strip(),
                side=side.lower().strip(),
                qty=qty_val,
                order_type=order_type.lower().strip() if order_type else "market",
                limit_price=float(limit_price) if limit_price is not None else None,
                strategy_id=strategy_id or "mcp-agent",
                proposed_at=now,
                expires_at=now + timedelta(seconds=PROPOSAL_TTL_SECONDS),
                status=_PENDING,
            )
            session.add(proposal)
        return token

    def _lazily_expire(self, row: LiveTradeProposal) -> LiveTradeProposal:
        """If ``row`` is still ``pending_approval`` OR ``approved`` but past
        its TTL, flip it to ``expired`` and persist. Returns the (possibly
        updated, detached) row. A readonly instance cannot persist the flip
        -- it returns the row with the honest computed status without
        writing (the write-mode store will catch it up on its own next
        read).

        ``approved`` is included deliberately, not just ``pending_approval``:
        ``expires_at`` is never extended on approval (see ``_transition``
        below), so the SAME 5-minute window that bounds "propose -> human
        decides" also bounds "approved -> actually executed". Without this,
        an approval from days ago would stay validly executable forever --
        a stale decision with no freshness bound at all."""
        if row.status not in (_PENDING, _APPROVED) or row.expires_at >= _now():
            return row

        if self._readonly:
            row.status = _EXPIRED
            return row

        with session_scope(self.Session) as session:
            db_row = (
                session.query(LiveTradeProposal)
                .filter(LiveTradeProposal.token == row.token)
                .first()
            )
            if db_row is None:
                return row
            if db_row.status in (_PENDING, _APPROVED) and db_row.expires_at < _now():
                db_row.status = _EXPIRED
            session.flush()
            session.refresh(db_row)
            session.expunge(db_row)
            return db_row

    def _transition(
        self,
        token: str,
        *,
        from_statuses: tuple,
        to_status: str,
        extra_fields: Optional[dict] = None,
    ) -> LiveTradeProposal:
        """Shared read-modify-write state transition: raises
        ``LiveTradeProposalNotFoundError`` if the token doesn't exist,
        ``LiveTradeProposalAlreadyDecidedError`` if the row's current status
        isn't one of ``from_statuses`` (lazily expiring a stale
        pending/approved row first, so an expired proposal is correctly
        reported as "already decided" rather than falsely matching).

        NOT used by ``claim_for_execution`` -- that transition needs a
        single atomic UPDATE (compare-and-swap), not a read-then-write,
        to actually close the double-submission race between concurrent
        ``confirm_live_trade`` calls. This helper is for the single-writer
        approve/reject/mark_executed/mark_failed transitions, where a
        read-then-write is safe because ``claim_for_execution`` is the sole
        gate standing between "approved" and any broker submission.
        """
        if self._readonly:
            raise RuntimeError(
                f"LiveTradeProposalStore is read-only; cannot transition to {to_status!r}."
            )
        with session_scope(self.Session) as session:
            db_row = (
                session.query(LiveTradeProposal)
                .filter(LiveTradeProposal.token == token)
                .first()
            )
            if db_row is None:
                raise LiveTradeProposalNotFoundError(token)
            if db_row.status in (_PENDING, _APPROVED) and db_row.expires_at < _now():
                db_row.status = _EXPIRED
            if db_row.status not in from_statuses:
                raise LiveTradeProposalAlreadyDecidedError(token)
            db_row.status = to_status
            for key, value in (extra_fields or {}).items():
                setattr(db_row, key, value)
            session.flush()
            session.refresh(db_row)
            session.expunge(db_row)
            return db_row

    def approve_proposal(self, token: str, approved_by: str = "operator") -> LiveTradeProposal:
        return self._transition(
            token,
            from_statuses=(_PENDING,),
            to_status=_APPROVED,
            extra_fields={"approved_at": _now(), "approved_by": approved_by},
        )

    def reject_proposal(self, token: str, approved_by: str = "operator") -> LiveTradeProposal:
        return self._transition(token, from_statuses=(_PENDING,), to_status=_REJECTED)

    def claim_for_execution(self, token: str) -> bool:
        """Atomically transitions an approved, non-expired proposal to
        ``executing`` -- the single compare-and-swap operation that makes
        concurrent or retried ``confirm_live_trade`` calls for the same
        token safe. A single ``UPDATE ... WHERE status='approved'`` can only
        ever match the row once; a second concurrent attempt sees 0 rows
        matched, not a false positive.

        Returns ``True`` iff THIS call won the claim. ``False`` means either
        the proposal isn't in a claimable state (not approved, already
        executing/executed/failed, or past its TTL) or another concurrent
        call already claimed it first -- the caller (``confirm_live_trade``)
        re-reads the row afterward to report an honest, current-status
        message either way, so this method deliberately does not raise
        ``LiveTradeProposalNotFoundError``/``LiveTradeProposalAlreadyDecidedError``
        itself.
        """
        if self._readonly:
            raise RuntimeError("LiveTradeProposalStore is read-only; cannot claim a proposal.")
        with session_scope(self.Session) as session:
            updated = (
                session.query(LiveTradeProposal)
                .filter(
                    LiveTradeProposal.token == token,
                    LiveTradeProposal.status == _APPROVED,
                    LiveTradeProposal.expires_at > _now(),
                )
                .update({"status": _EXECUTING}, synchronize_session=False)
            )
            return updated == 1

    def mark_executed(self, token: str, broker_order_id: str) -> None:
        self._transition(
            token,
            from_statuses=(_EXECUTING,),
            to_status=_EXECUTED,
            extra_fields={"broker_order_id": broker_order_id},
        )

    def mark_failed(self, token: str, error_message: str) -> None:
        self._transition(
            token,
            from_statuses=(_EXECUTING,),
            to_status=_FAILED,
            extra_fields={"error_message": error_message},
        )

    # -- reads ------------------------------------------------------------
    # Never raise (CONSTRAINT #6, dead-letter resilience) -- degrade to
    # empty/None on any failure.

    def get_by_token(self, token: str) -> Optional[LiveTradeProposal]:
        try:
            session = self.Session()
            try:
                row = (
                    session.query(LiveTradeProposal)
                    .filter(LiveTradeProposal.token == token)
                    .first()
                )
                if row is None:
                    return None
                session.expunge(row)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to None
            logger.warning("LiveTradeProposalStore.get_by_token(%s): %s", token, exc)
            return None
        return self._lazily_expire(row)

    def get_pending(self, limit: int = 50) -> List[LiveTradeProposal]:
        try:
            session = self.Session()
            try:
                now = _now()
                rows = (
                    session.query(LiveTradeProposal)
                    .filter(
                        LiveTradeProposal.status == _PENDING,
                        LiveTradeProposal.expires_at > now,
                    )
                    .order_by(LiveTradeProposal.proposed_at.desc())
                    .limit(limit)
                    .all()
                )
                for row in rows:
                    session.expunge(row)
                return rows
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
            logger.warning("LiveTradeProposalStore.get_pending: %s", exc)
            return []
