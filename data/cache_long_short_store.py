"""SQLAlchemy-backed store for Cache Long/Short advisory strategy positions and tax lots."""

import logging
import os
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, List, Dict
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, inspect, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dataclasses import dataclass

from db_config import resolve_database_url, create_db_engine, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()

@dataclass(frozen=True)
class TaxLotDTO:
    lot_id: int
    position_id: int
    acquisition_date: datetime
    cost_basis_per_share: float
    quantity: float
    status: str
    realized_pnl: Optional[float]
    close_date: Optional[datetime]
    tlh_approved: int = 0

@dataclass(frozen=True)
class CacheLongShortPositionDTO:
    id: int
    ticker: str
    position_type: str
    opened_at: datetime
    closed_at: Optional[datetime]
    status: str

class CacheLongShortPosition(Base):
    __tablename__ = 'cache_ls_positions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False)
    position_type = Column(String(10), nullable=False) # 'long' or 'short'
    opened_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    closed_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default='open')

class CacheLongShortTaxLot(Base):
    __tablename__ = 'cache_ls_tax_lots'
    
    lot_id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey('cache_ls_positions.id'), nullable=False)
    acquisition_date = Column(DateTime, nullable=False)
    cost_basis_per_share = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, default='open') # 'open' or 'closed'
    realized_pnl = Column(Float, nullable=True)
    close_date = Column(DateTime, nullable=True)
    tlh_approved = Column(Integer, nullable=False, default=0) # 0 = false, 1 = true

class SecurityProxy(Base):
    __tablename__ = 'cache_ls_security_proxies'
    
    primary_ticker = Column(String(10), primary_key=True)
    proxy_ticker = Column(String(10), nullable=False)
    correlation_coefficient = Column(Float, nullable=False)
    computed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class CacheLongShortStore:
    def __init__(self, db_url: Optional[str] = None, *, readonly: bool = False):
        db_url = db_url or resolve_database_url()
        self._readonly = readonly
        if readonly:
            from db_config import create_readonly_db_engine
            self.engine = create_readonly_db_engine(db_url)
        else:
            self.engine = create_db_engine(db_url)
            Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_position(self, ticker: str, position_type: str) -> int:
        if self._readonly:
            raise RuntimeError("Cannot write to readonly store")
        with session_scope(self.Session) as session:
            pos = CacheLongShortPosition(
                ticker=ticker.upper().strip(),
                position_type=position_type.lower().strip()
            )
            session.add(pos)
            session.flush()
            return int(pos.id)

    def record_tax_lot(self, position_id: int, acquisition_date: datetime, cost_basis_per_share: float, quantity: float) -> int:
        if self._readonly:
            raise RuntimeError("Cannot write to readonly store")
        with session_scope(self.Session) as session:
            naive_dt = acquisition_date.replace(tzinfo=None)
            lot = CacheLongShortTaxLot(
                position_id=position_id,
                acquisition_date=naive_dt,
                cost_basis_per_share=float(cost_basis_per_share),
                quantity=float(quantity)
            )
            session.add(lot)
            session.flush()
            return int(lot.lot_id)

    def close_tax_lot(self, lot_id: int, realized_pnl: float, close_date: datetime) -> None:
        if self._readonly:
            raise RuntimeError("Cannot write to readonly store")
        with session_scope(self.Session) as session:
            lot = session.query(CacheLongShortTaxLot).filter(CacheLongShortTaxLot.lot_id == lot_id).first()
            if not lot:
                raise ValueError(f"Lot {lot_id} not found")
            lot.status = 'closed'
            lot.realized_pnl = float(realized_pnl)
            lot.close_date = close_date.replace(tzinfo=None)

    def approve_tax_lots(self, lot_ids: List[int]) -> None:
        if self._readonly:
            raise RuntimeError("Cannot write to readonly store")
        with session_scope(self.Session) as session:
            session.query(CacheLongShortTaxLot).filter(CacheLongShortTaxLot.lot_id.in_(lot_ids)).update({"tlh_approved": 1}, synchronize_session=False)

    def get_open_positions(self) -> List[CacheLongShortPositionDTO]:
        session = self.Session()
        try:
            positions = session.query(CacheLongShortPosition).filter(CacheLongShortPosition.status == 'open').all()
            return [CacheLongShortPositionDTO(p.id, p.ticker, p.position_type, p.opened_at, p.closed_at, p.status) for p in positions]
        except Exception as exc:
            logger.debug("get_open_positions: %s", exc)
            return []
        finally:
            session.close()

    def get_open_tax_lots(self, ticker: Optional[str] = None) -> List[TaxLotDTO]:
        session = self.Session()
        try:
            query = session.query(CacheLongShortTaxLot).join(CacheLongShortPosition).filter(CacheLongShortTaxLot.status == 'open')
            if ticker:
                query = query.filter(CacheLongShortPosition.ticker == ticker.upper().strip())
            lots = query.all()
            return [TaxLotDTO(l.lot_id, l.position_id, l.acquisition_date, l.cost_basis_per_share, l.quantity, l.status, l.realized_pnl, l.close_date, l.tlh_approved) for l in lots]
        except Exception as exc:
            logger.debug("get_open_tax_lots: %s", exc)
            return []
        finally:
            session.close()

    def get_closed_lots_since(self, cutoff_date: datetime) -> List[TaxLotDTO]:
        session = self.Session()
        try:
            naive_cutoff = cutoff_date.replace(tzinfo=None)
            lots = session.query(CacheLongShortTaxLot).filter(
                CacheLongShortTaxLot.status == 'closed',
                CacheLongShortTaxLot.close_date >= naive_cutoff
            ).all()
            return [TaxLotDTO(l.lot_id, l.position_id, l.acquisition_date, l.cost_basis_per_share, l.quantity, l.status, l.realized_pnl, l.close_date, l.tlh_approved) for l in lots]
        except Exception as exc:
            logger.debug("get_closed_lots_since: %s", exc)
            return []
        finally:
            session.close()

    def upsert_security_proxy(self, primary_ticker: str, proxy_ticker: str, correlation_coefficient: float) -> None:
        if self._readonly:
            raise RuntimeError("Cannot write to readonly store")
        with session_scope(self.Session) as session:
            primary_ticker = primary_ticker.upper().strip()
            proxy = session.query(SecurityProxy).filter(SecurityProxy.primary_ticker == primary_ticker).first()
            if proxy:
                proxy.proxy_ticker = proxy_ticker.upper().strip()
                proxy.correlation_coefficient = float(correlation_coefficient)
                proxy.computed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                proxy = SecurityProxy(
                    primary_ticker=primary_ticker,
                    proxy_ticker=proxy_ticker.upper().strip(),
                    correlation_coefficient=float(correlation_coefficient)
                )
                session.add(proxy)

    def get_security_proxy(self, ticker: str) -> Optional[dict]:
        session = self.Session()
        try:
            proxy = session.query(SecurityProxy).filter(SecurityProxy.primary_ticker == ticker.upper().strip()).first()
            if proxy:
                return {
                    "primary_ticker": proxy.primary_ticker,
                    "proxy_ticker": proxy.proxy_ticker,
                    "correlation_coefficient": proxy.correlation_coefficient,
                    "computed_at": proxy.computed_at
                }
            return None
        except Exception as exc:
            logger.debug("get_security_proxy: %s", exc)
            return None
        finally:
            session.close()

    def tax_bank(self) -> float:
        session = self.Session()
        try:
            val = session.query(func.sum(CacheLongShortTaxLot.realized_pnl)).filter(CacheLongShortTaxLot.status == 'closed', CacheLongShortTaxLot.realized_pnl < 0).scalar()
            return abs(float(val)) if val else 0.0
        except Exception as exc:
            logger.debug("tax_bank: %s", exc)
            return 0.0
        finally:
            session.close()

    def exposure_summary(self) -> dict:
        session = self.Session()
        try:
            # We approximate exposure by sum of cost basis * quantity for open lots
            # (In a real implementation we might fetch current prices, but cost basis is a reasonable proxy for summary if prices aren't available, or we just return the invested amount)
            # We'll group by position_type.
            lots = session.query(CacheLongShortTaxLot.quantity, CacheLongShortTaxLot.cost_basis_per_share, CacheLongShortPosition.position_type)\
                .join(CacheLongShortPosition)\
                .filter(CacheLongShortTaxLot.status == 'open').all()
            
            long_exp = sum(l.quantity * l.cost_basis_per_share for l in lots if l.position_type == 'long')
            short_exp = sum(l.quantity * l.cost_basis_per_share for l in lots if l.position_type == 'short')
            return {
                "long_exposure": long_exp,
                "short_exposure": short_exp,
                "net_exposure": long_exp - short_exp,
                "gross_exposure": long_exp + short_exp
            }
        except Exception as exc:
            logger.debug("exposure_summary: %s", exc)
            return {"long_exposure": 0.0, "short_exposure": 0.0, "net_exposure": 0.0, "gross_exposure": 0.0}
        finally:
            session.close()
