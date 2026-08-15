"""
InvestYo Quant Platform - Execution Audit Store
================================================
SQLite / SQLAlchemy persistent audit log for every routed and executed order,
storing execution quality telemetry required for SEC Rule 606 & 605 reporting,
best execution analysis, and broker routing audits.

Stores order ID, symbol, venue, order type (Market, Marketable Limit,
Non-Marketable Limit, Other), routing timestamp, fill price, NBBO at routing
time (nbbo_bid, nbbo_ask), executed shares, maker/taker fee/rebate ($), and
price improvement ($).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

# SEC Rule 606 canonical order categories
ORDER_CATEGORY_MARKET = "Market"
ORDER_CATEGORY_MARKETABLE_LIMIT = "Marketable Limit"
ORDER_CATEGORY_NON_MARKETABLE_LIMIT = "Non-Marketable Limit"
ORDER_CATEGORY_OTHER = "Other"

ORDER_CATEGORIES = [
    ORDER_CATEGORY_MARKET,
    ORDER_CATEGORY_MARKETABLE_LIMIT,
    ORDER_CATEGORY_NON_MARKETABLE_LIMIT,
    ORDER_CATEGORY_OTHER,
]


class ExecutionAuditRecord(Base):
    """SQLAlchemy model for the persistent `execution_audit_records` table."""

    __tablename__ = "execution_audit_records"
    __table_args__ = (
        Index("ix_exec_audit_ts_venue_type", "routing_timestamp", "venue", "order_type"),
        Index("ix_exec_audit_ts_is_option", "routing_timestamp", "is_option"),
        Index("ix_exec_audit_symbol_ts", "symbol", "routing_timestamp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(100), nullable=False, index=True)
    client_order_id = Column(String(100), nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=True)  # 'buy' or 'sell'
    venue = Column(String(50), nullable=False, index=True)
    order_type = Column(String(30), nullable=False, index=True)  # Market, Marketable Limit, Non-Marketable Limit, Other
    routing_timestamp = Column(DateTime, nullable=False, index=True)
    fill_price = Column(Float, nullable=True)
    nbbo_bid = Column(Float, nullable=True)
    nbbo_ask = Column(Float, nullable=True)
    executed_shares = Column(Float, nullable=False, default=0.0)
    maker_taker_fee_rebate = Column(Float, nullable=False, default=0.0)  # Net rebate (+) or fee (-) in $
    price_improvement = Column(Float, nullable=False, default=0.0)  # Total price improvement in $
    is_option = Column(Boolean, nullable=False, default=False)
    notes = Column(String(255), nullable=True)


def calculate_price_improvement(
    side: str,
    fill_price: Optional[float],
    nbbo_bid: Optional[float],
    nbbo_ask: Optional[float],
    shares: float,
) -> float:
    """Calculate total price improvement ($) for an executed order.

    - For BUY: if fill_price < nbbo_ask, improvement = (nbbo_ask - fill_price) * shares.
    - For SELL: if fill_price > nbbo_bid, improvement = (fill_price - nbbo_bid) * shares.
    - Otherwise (at or worse than NBBO, or missing NBBO/price): returns 0.0.
    """
    if fill_price is None or shares <= 0:
        return 0.0

    side_clean = str(side).strip().lower()
    improvement_per_share = 0.0

    if side_clean == "buy" and nbbo_ask is not None and math.isfinite(nbbo_ask):
        if fill_price < nbbo_ask:
            improvement_per_share = nbbo_ask - fill_price
    elif side_clean == "sell" and nbbo_bid is not None and math.isfinite(nbbo_bid):
        if fill_price > nbbo_bid:
            improvement_per_share = fill_price - nbbo_bid

    if improvement_per_share > 0:
        return round(float(improvement_per_share * shares), 6)
    return 0.0


def classify_limit_order(
    side: str,
    limit_price: float,
    nbbo_bid: Optional[float],
    nbbo_ask: Optional[float],
) -> str:
    """Classify a limit order as 'Marketable Limit' or 'Non-Marketable Limit'
    based on NBBO at the time of order routing.
    """
    if limit_price is None:
        return ORDER_CATEGORY_OTHER

    side_clean = str(side).strip().lower()
    if side_clean == "buy":
        if nbbo_ask is not None and math.isfinite(nbbo_ask) and limit_price >= nbbo_ask:
            return ORDER_CATEGORY_MARKETABLE_LIMIT
        return ORDER_CATEGORY_NON_MARKETABLE_LIMIT
    elif side_clean == "sell":
        if nbbo_bid is not None and math.isfinite(nbbo_bid) and limit_price <= nbbo_bid:
            return ORDER_CATEGORY_MARKETABLE_LIMIT
        return ORDER_CATEGORY_NON_MARKETABLE_LIMIT

    return ORDER_CATEGORY_NON_MARKETABLE_LIMIT


def normalize_order_type(order_type: str) -> str:
    """Normalize any string representation of order type into one of the 4
    canonical SEC Rule 606 categories.
    """
    if not order_type:
        return ORDER_CATEGORY_OTHER

    ot = str(order_type).strip().lower().replace("_", " ").replace("-", " ")
    if ot in ("market", "mkt"):
        return ORDER_CATEGORY_MARKET
    elif "marketable" in ot and "non" not in ot:
        return ORDER_CATEGORY_MARKETABLE_LIMIT
    elif "non marketable" in ot or "nonmarketable" in ot:
        return ORDER_CATEGORY_NON_MARKETABLE_LIMIT
    elif ot == "limit":
        return ORDER_CATEGORY_NON_MARKETABLE_LIMIT
    elif ot in ("other", "stop", "stop limit", "trailing stop"):
        return ORDER_CATEGORY_OTHER

    return ORDER_CATEGORY_OTHER


def get_quarter_date_range(year: int, quarter: int) -> Tuple[datetime, datetime]:
    """Return the UTC (start_datetime, end_datetime) range for a given year and quarter (1-4)."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"Quarter must be 1, 2, 3, or 4; got {quarter}")

    if quarter == 1:
        start = datetime(year, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(year, 3, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    elif quarter == 2:
        start = datetime(year, 4, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(year, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
    elif quarter == 3:
        start = datetime(year, 7, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(year, 9, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)
    else:  # quarter == 4
        start = datetime(year, 10, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)

    return start, end


def _coerce_dt(ts: Any) -> datetime:
    """Coerce various timestamp formats into a naive UTC datetime."""
    if ts is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if isinstance(ts, datetime):
        if ts.tzinfo is not None:
            return ts.astimezone(timezone.utc).replace(tzinfo=None)
        return ts
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _opt_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely coerce a value to float or return default."""
    if val is None or val == "":
        return default
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _row_to_dict(row: ExecutionAuditRecord) -> Dict[str, Any]:
    """Convert an ExecutionAuditRecord ORM row to a plain dictionary."""
    return {
        "id": row.id,
        "order_id": row.order_id,
        "client_order_id": row.client_order_id,
        "symbol": row.symbol,
        "side": row.side,
        "venue": row.venue,
        "order_type": row.order_type,
        "routing_timestamp": row.routing_timestamp.isoformat() if row.routing_timestamp else None,
        "fill_price": row.fill_price,
        "nbbo_bid": row.nbbo_bid,
        "nbbo_ask": row.nbbo_ask,
        "executed_shares": row.executed_shares,
        "maker_taker_fee_rebate": row.maker_taker_fee_rebate,
        "price_improvement": row.price_improvement,
        "is_option": bool(row.is_option),
        "notes": row.notes,
    }


class ExecutionAuditStore:
    """Persistent storage engine for execution audit records backing SEC Rule 606
    reporting and best execution analytics.
    """

    def __init__(
        self,
        db_url: Optional[str] = None,
        *,
        readonly: bool = False,
        sqlite_path: Optional[str] = None,
    ) -> None:
        if sqlite_path:
            if sqlite_path == ":memory:":
                db_url = "sqlite:///:memory:"
            else:
                db_url = f"sqlite:///{sqlite_path}"

        self.db_url = db_url or resolve_database_url()
        self._readonly = readonly

        if readonly:
            from db_config import create_readonly_db_engine
            self.engine = create_readonly_db_engine(self.db_url)
        else:
            self.engine = create_db_engine(self.db_url)
            Base.metadata.create_all(self.engine)

        self.Session = sessionmaker(bind=self.engine)

    def record_audit(self, audit_record: Union[Dict[str, Any], ExecutionAuditRecord]) -> int:
        """Insert a single execution audit record into the persistent store.
        Returns the inserted record's database ID.
        """
        if self._readonly:
            raise RuntimeError("ExecutionAuditStore is read-only; cannot record audit.")

        with session_scope(self.Session) as session:
            rec = self._build_record_obj(audit_record)
            session.add(rec)
            session.flush()
            rec_id = rec.id
            return rec_id

    def bulk_insert_audits(
        self,
        records: List[Union[Dict[str, Any], ExecutionAuditRecord]],
        batch_size: int = 1000,
    ) -> int:
        """High-throughput batch insertion of execution audit records.

        Optimized with chunked multi-row mapping insertion and single transaction scope.
        Compatible with SQLite WAL pragma tuning and PostgreSQL batch COPY/INSERT operations.
        Returns the count of successfully persisted records.
        """
        if self._readonly:
            raise RuntimeError("ExecutionAuditStore is read-only; cannot record audits.")
        if not records:
            return 0

        # Build list of normalized dictionaries for high-throughput bulk insertion
        dict_records = [self._build_record_dict(r) for r in records]

        total_inserted = 0
        with session_scope(self.Session) as session:
            for i in range(0, len(dict_records), batch_size):
                chunk = dict_records[i : i + batch_size]
                session.bulk_insert_mappings(ExecutionAuditRecord, chunk)
                total_inserted += len(chunk)

        return total_inserted

    def record_audits(
        self,
        records: List[Union[Dict[str, Any], ExecutionAuditRecord]],
        batch_size: int = 1000,
    ) -> int:
        """Insert a batch of execution audit records in a single transaction.
        Returns the count of successfully persisted records.
        """
        return self.bulk_insert_audits(records, batch_size=batch_size)

    def _build_record_dict(self, data: Union[Dict[str, Any], ExecutionAuditRecord]) -> Dict[str, Any]:
        """Construct a normalized dictionary for ExecutionAuditRecord bulk insertion."""
        if isinstance(data, ExecutionAuditRecord):
            return {
                "order_id": data.order_id,
                "client_order_id": data.client_order_id,
                "symbol": data.symbol,
                "side": data.side,
                "venue": data.venue,
                "order_type": data.order_type,
                "routing_timestamp": data.routing_timestamp,
                "fill_price": data.fill_price,
                "nbbo_bid": data.nbbo_bid,
                "nbbo_ask": data.nbbo_ask,
                "executed_shares": data.executed_shares,
                "maker_taker_fee_rebate": data.maker_taker_fee_rebate,
                "price_improvement": data.price_improvement,
                "is_option": bool(data.is_option),
                "notes": data.notes,
            }

        d = dict(data or {})
        raw_order_type = d.get("order_type")
        normalized_ot = normalize_order_type(str(raw_order_type)) if raw_order_type else ORDER_CATEGORY_OTHER

        side = str(d.get("side", "")).lower() if d.get("side") else None
        fill_price = _opt_float(d.get("fill_price"))
        nbbo_bid = _opt_float(d.get("nbbo_bid"))
        nbbo_ask = _opt_float(d.get("nbbo_ask"))
        executed_shares = float(_opt_float(d.get("executed_shares"), 0.0) or 0.0)

        # Compute price improvement if not explicitly provided
        if "price_improvement" in d and d["price_improvement"] is not None:
            price_improvement = float(_opt_float(d["price_improvement"], 0.0) or 0.0)
        else:
            price_improvement = calculate_price_improvement(
                side=side or "",
                fill_price=fill_price,
                nbbo_bid=nbbo_bid,
                nbbo_ask=nbbo_ask,
                shares=executed_shares,
            )

        fee_rebate = float(_opt_float(d.get("maker_taker_fee_rebate"), 0.0) or 0.0)
        routing_ts = _coerce_dt(d.get("routing_timestamp"))

        return {
            "order_id": str(d.get("order_id") or d.get("client_order_id") or "UNKNOWN"),
            "client_order_id": str(d["client_order_id"]) if d.get("client_order_id") else None,
            "symbol": str(d.get("symbol", "")).upper().strip(),
            "side": side,
            "venue": str(d.get("venue", "UNKNOWN")).upper().strip(),
            "order_type": normalized_ot,
            "routing_timestamp": routing_ts,
            "fill_price": fill_price,
            "nbbo_bid": nbbo_bid,
            "nbbo_ask": nbbo_ask,
            "executed_shares": executed_shares,
            "maker_taker_fee_rebate": fee_rebate,
            "price_improvement": price_improvement,
            "is_option": bool(d.get("is_option", False)),
            "notes": str(d["notes"]) if d.get("notes") else None,
        }

    def _build_record_obj(self, data: Union[Dict[str, Any], ExecutionAuditRecord]) -> ExecutionAuditRecord:
        """Construct an ExecutionAuditRecord entity from dict or instance."""
        if isinstance(data, ExecutionAuditRecord):
            return data
        record_dict = self._build_record_dict(data)
        return ExecutionAuditRecord(**record_dict)

    def get_records(
        self,
        symbol: Optional[str] = None,
        venue: Optional[str] = None,
        order_type: Optional[str] = None,
        side: Optional[str] = None,
        start_time: Optional[Union[datetime, str]] = None,
        end_time: Optional[Union[datetime, str]] = None,
        is_option: Optional[bool] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Query execution audit records matching the specified criteria."""
        try:
            session = self.Session()
            try:
                query = session.query(ExecutionAuditRecord)

                if symbol:
                    query = query.filter(ExecutionAuditRecord.symbol == str(symbol).upper().strip())
                if venue:
                    query = query.filter(ExecutionAuditRecord.venue == str(venue).upper().strip())
                if order_type:
                    query = query.filter(ExecutionAuditRecord.order_type == normalize_order_type(order_type))
                if side:
                    query = query.filter(ExecutionAuditRecord.side == str(side).lower().strip())
                if is_option is not None:
                    query = query.filter(ExecutionAuditRecord.is_option == bool(is_option))
                if start_time is not None:
                    dt_start = _coerce_dt(start_time)
                    query = query.filter(ExecutionAuditRecord.routing_timestamp >= dt_start)
                if end_time is not None:
                    dt_end = _coerce_dt(end_time)
                    query = query.filter(ExecutionAuditRecord.routing_timestamp <= dt_end)

                query = query.order_by(ExecutionAuditRecord.routing_timestamp.asc())
                if limit:
                    query = query.limit(limit)

                rows = query.all()
                return [_row_to_dict(r) for r in rows]
            finally:
                session.close()
        except Exception as exc:
            logger.warning("ExecutionAuditStore.get_records failed: %s", exc)
            return []

    def get_records_for_quarter(
        self,
        year: int,
        quarter: int,
        is_option: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch all execution audit records for a calendar quarter."""
        start_dt, end_dt = get_quarter_date_range(year, quarter)
        return self.get_records(start_time=start_dt, end_time=end_dt, is_option=is_option)

    def get_records_for_date_range(
        self,
        start_date: Union[datetime, str],
        end_date: Union[datetime, str],
        is_option: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch all execution audit records within a custom date range."""
        return self.get_records(start_time=start_date, end_time=end_date, is_option=is_option)

    def get_all_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve all recorded audit entries."""
        return self.get_records(limit=limit)

    def count(self) -> int:
        """Return the total number of execution audit records stored."""
        try:
            session = self.Session()
            try:
                return session.query(ExecutionAuditRecord).count()
            finally:
                session.close()
        except Exception as exc:
            logger.warning("ExecutionAuditStore.count failed: %s", exc)
            return 0

    def clear_records(self) -> int:
        """Delete all execution audit records. Raises if readonly."""
        if self._readonly:
            raise RuntimeError("ExecutionAuditStore is read-only; cannot clear records.")

        with session_scope(self.Session) as session:
            deleted = session.query(ExecutionAuditRecord).delete()
            return deleted
