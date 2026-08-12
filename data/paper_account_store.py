"""
InvestYo Quant Platform - Paper Account Store
=============================================
SQLite store tracking virtual cash balance, open positions, and order history
across process restarts for the FMP-based paper trading engine.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import Column, Integer, String, Float, DateTime, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import resolve_database_url, create_db_engine, session_scope
from settings import settings
from data import fmp_client
from execution.broker_base import AccountSnapshot, PositionSnapshot, OrderResult, OrderStatus

logger = logging.getLogger(__name__)

# Position quantities near zero after a full sell can carry float noise
# (e.g. 1e-13 rather than exactly 0.0) -- see CLAUDE.md's "Degenerate-std
# guard convention": never compare a computed float to 0 with ==.
_QTY_EPSILON = 1e-9

Base = declarative_base()

class PaperAccount(Base):
    __tablename__ = 'paper_account'
    
    id = Column(Integer, primary_key=True)  # always 1
    cash_balance = Column(Float, nullable=False)


class PaperPosition(Base):
    __tablename__ = 'paper_positions'
    
    symbol = Column(String(10), primary_key=True)
    qty = Column(Float, nullable=False)
    avg_entry_price = Column(Float, nullable=False)


class PaperOrder(Base):
    __tablename__ = 'paper_orders'
    
    client_order_id = Column(String(100), primary_key=True)
    broker_order_id = Column(String(100), nullable=True)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    qty = Column(Float, nullable=False)
    filled_qty = Column(Float, nullable=False, default=0.0)
    filled_avg_price = Column(Float, nullable=True)
    status = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class PaperAccountStore:
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
        
        if not readonly:
            self._ensure_account_exists()

    def _ensure_account_exists(self):
        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).first()
            if not acc:
                acc = PaperAccount(id=1, cash_balance=settings.FMP_PAPER_STARTING_CASH)
                session.add(acc)

    def get_account(self) -> AccountSnapshot:
        """Returns account equity, cash, buying_power (same as cash here)."""
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_account"):
                    return AccountSnapshot(equity=0.0, cash=0.0, buying_power=0.0)
            except Exception:
                return AccountSnapshot(equity=0.0, cash=0.0, buying_power=0.0)

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).first()
            cash = float(acc.cash_balance) if acc else 0.0

            positions = session.query(PaperPosition).filter(PaperPosition.qty != 0).all()
            
            equity = cash
            if positions:
                symbols = [p.symbol for p in positions]
                try:
                    quotes_resp = fmp_client.batch_quote(symbols)
                    # batch_quote typically returns list of dicts: [{'symbol': 'AAPL', 'price': 150.0}, ...]
                    prices = {q.get("symbol", "").upper(): q.get("price", 0.0) for q in quotes_resp if isinstance(q, dict)}
                except Exception as e:
                    logger.error(f"Failed to fetch quotes for paper account: {e}")
                    prices = {}

                for p in positions:
                    price = prices.get(p.symbol.upper(), p.avg_entry_price)
                    equity += (float(p.qty) * float(price))

        return AccountSnapshot(equity=equity, cash=cash, buying_power=cash)

    def get_open_positions(self) -> List[PositionSnapshot]:
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_positions"):
                    return []
            except Exception:
                return []

        results = []
        with session_scope(self.Session) as session:
            positions = session.query(PaperPosition).filter(PaperPosition.qty != 0).all()
            if not positions:
                return []

            symbols = [p.symbol for p in positions]
            try:
                quotes_resp = fmp_client.batch_quote(symbols)
                prices = {q.get("symbol", "").upper(): q.get("price", 0.0) for q in quotes_resp if isinstance(q, dict)}
            except Exception as e:
                logger.error(f"Failed to fetch quotes for paper positions: {e}")
                prices = {}

            for p in positions:
                current_price = prices.get(p.symbol.upper(), p.avg_entry_price)
                market_value = float(p.qty) * float(current_price)
                unrealized_pl = market_value - (float(p.qty) * float(p.avg_entry_price))
                
                results.append(PositionSnapshot(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    market_value=market_value,
                    unrealized_pl=unrealized_pl
                ))
        return results

    def reset_account(self) -> None:
        """
        Deletes all PaperPosition and PaperOrder rows. Resets cash balance to FMP_PAPER_STARTING_CASH.
        """
        if self._readonly:
            raise RuntimeError("Cannot reset account in readonly mode.")
            
        with session_scope(self.Session) as session:
            session.query(PaperPosition).delete()
            session.query(PaperOrder).delete()
            
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if acc:
                acc.cash_balance = settings.FMP_PAPER_STARTING_CASH
            else:
                acc = PaperAccount(id=1, cash_balance=settings.FMP_PAPER_STARTING_CASH)
                session.add(acc)

    def apply_fill(
        self, 
        client_order_id: str,
        symbol: str, 
        side: str, 
        qty: float, 
        fill_price: float, 
        commission_and_fees: float,
        status: str = OrderStatus.FILLED
    ) -> bool:
        """
        Updates cash and position. Returns True if successful, False if insufficient funds/inventory.
        Records the order.
        """
        if self._readonly:
            raise RuntimeError("Cannot apply fill in readonly mode.")
            
        side = side.lower().strip()
        cost_basis_impact = qty * fill_price
        
        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if not acc:
                return False
                
            pos = session.query(PaperPosition).filter_by(symbol=symbol.upper()).with_for_update().first()
            current_qty = pos.qty if pos else 0.0
            
            if side == "buy":
                total_cost = cost_basis_impact + commission_and_fees
                if acc.cash_balance < total_cost:
                    logger.warning(f"Insufficient funds for paper buy of {qty} {symbol}: cash={acc.cash_balance}, cost={total_cost}")
                    # Still record rejection
                    self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED)
                    return False
                    
                acc.cash_balance -= total_cost
                
                if pos:
                    new_qty = pos.qty + qty
                    pos.avg_entry_price = ((pos.qty * pos.avg_entry_price) + cost_basis_impact) / new_qty
                    pos.qty = new_qty
                else:
                    pos = PaperPosition(symbol=symbol.upper(), qty=qty, avg_entry_price=fill_price)
                    session.add(pos)
                    
            elif side == "sell":
                if current_qty < qty - _QTY_EPSILON:
                    logger.warning(f"Insufficient inventory for paper sell of {qty} {symbol}: pos={current_qty}")
                    self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED)
                    return False
                    
                total_proceeds = cost_basis_impact - commission_and_fees
                acc.cash_balance += total_proceeds
                
                pos.qty -= qty
                if abs(pos.qty) < _QTY_EPSILON:
                    session.delete(pos)
                    
            else:
                return False

            self._insert_order(session, client_order_id, symbol, side, qty, qty, fill_price, status)
            return True

    def _insert_order(self, session, client_order_id, symbol, side, qty, filled_qty, fill_price, status):
        # We use a derived broker_order_id
        broker_order_id = f"FMP-{client_order_id}"
        
        po = session.query(PaperOrder).filter_by(client_order_id=client_order_id).first()
        if not po:
            po = PaperOrder(
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                symbol=symbol.upper(),
                side=side,
                qty=qty,
                filled_qty=filled_qty,
                filled_avg_price=fill_price,
                status=status,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None)
            )
            session.add(po)
        else:
            po.status = status
            po.filled_qty = filled_qty
            po.filled_avg_price = fill_price

    def get_orders(self, status: Optional[str] = None, limit: int = 100) -> List[OrderResult]:
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_orders"):
                    return []
            except Exception:
                return []

        results = []
        with session_scope(self.Session) as session:
            q = session.query(PaperOrder)
            if status:
                q = q.filter_by(status=status)
            q = q.order_by(PaperOrder.timestamp.desc()).limit(limit)
            
            for po in q.all():
                ts = po.timestamp.replace(tzinfo=timezone.utc)
                results.append(OrderResult(
                    client_order_id=po.client_order_id,
                    broker_order_id=po.broker_order_id,
                    status=OrderStatus(po.status),
                    filled_qty=po.filled_qty,
                    filled_avg_price=po.filled_avg_price,
                    submitted_at=ts,
                    filled_at=ts if po.filled_qty > 0 else None,
                    error_message=None
                ))
        return results

    def get_full_orders(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_orders"):
                    return []
            except Exception:
                return []

        results = []
        with session_scope(self.Session) as session:
            q = session.query(PaperOrder)
            if status:
                q = q.filter_by(status=status)
            q = q.order_by(PaperOrder.timestamp.desc()).limit(limit)
            
            for po in q.all():
                ts = po.timestamp.replace(tzinfo=timezone.utc)
                results.append({
                    "order_id": po.client_order_id,
                    "symbol": po.symbol,
                    "side": po.side.upper(),
                    "qty": po.qty,
                    "price": po.filled_avg_price or 0.0,
                    "status": po.status,
                    "filled_qty": po.filled_qty,
                    "filled_avg_price": po.filled_avg_price,
                    "created_at": ts.isoformat()
                })
        return results

    def reset_account(self) -> None:
        """
        Deletes all PaperPosition and PaperOrder rows. Resets cash balance to FMP_PAPER_STARTING_CASH.
        """
        if self._readonly:
            raise RuntimeError("Cannot reset account in readonly mode.")
            
        with session_scope(self.Session) as session:
            session.query(PaperPosition).delete()
            session.query(PaperOrder).delete()
            
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if acc:
                acc.cash_balance = settings.FMP_PAPER_STARTING_CASH
            else:
                acc = PaperAccount(id=1, cash_balance=settings.FMP_PAPER_STARTING_CASH)
                session.add(acc)
