import os
import pandas as pd
from datetime import datetime
from typing import Optional, Union
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DB_DIR, "quant_platform.db")
DATABASE_URL = f"sqlite:///{DB_FILE}"

Base = declarative_base()

class Trade(Base):
    __tablename__ = 'trades'
    
    trade_id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)  # 'long' or 'short'
    entry_ts = Column(DateTime, nullable=False, default=datetime.utcnow)
    entry_price = Column(Float, nullable=False)
    exit_ts = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    shares = Column(Float, nullable=False)
    strategy = Column(String(50), nullable=True)
    notes = Column(String(255), nullable=True)

class TransactionsStore:
    def __init__(self, db_url: str = DATABASE_URL):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_ts: datetime,
        entry_price: float,
        shares: float,
        strategy: Optional[str] = None,
        notes: Optional[str] = None
    ) -> int:
        """Records a new open trade. Returns the trade_id."""
        session = self.Session()
        try:
            # Ensure naive datetime for SQL consistency
            naive_entry_ts = entry_ts.replace(tzinfo=None) if entry_ts else datetime.utcnow()
            trade = Trade(
                symbol=symbol.upper().strip(),
                side=side.lower().strip(),
                entry_ts=naive_entry_ts,
                entry_price=float(entry_price),
                shares=float(shares),
                strategy=strategy,
                notes=notes
            )
            session.add(trade)
            session.commit()
            trade_id = int(trade.trade_id)
            return trade_id
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def close_trade(self, trade_id: int, exit_ts: datetime, exit_price: float) -> None:
        """Closes an open trade by trade_id."""
        session = self.Session()
        try:
            trade = session.query(Trade).filter(Trade.trade_id == trade_id).first()
            if not trade:
                raise ValueError(f"Trade ID {trade_id} not found.")
            # Ensure naive datetime for SQL consistency
            naive_exit_ts = exit_ts.replace(tzinfo=None) if exit_ts else datetime.utcnow()
            trade.exit_ts = naive_exit_ts
            trade.exit_price = float(exit_price)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def open_trades_df(self) -> pd.DataFrame:
        """Returns all open trades as a pandas DataFrame."""
        session = self.Session()
        try:
            query = session.query(Trade).filter(Trade.exit_ts == None)
            df = pd.read_sql(query.statement, self.engine)
            return df
        finally:
            session.close()

    def closed_trades_df(self) -> pd.DataFrame:
        """Returns all closed trades as a pandas DataFrame."""
        session = self.Session()
        try:
            query = session.query(Trade).filter(Trade.exit_ts != None)
            df = pd.read_sql(query.statement, self.engine)
            return df
        finally:
            session.close()

    def get_trade_history(self, symbol: str) -> pd.DataFrame:
        """Returns trade history (both open and closed) for a symbol as a pandas DataFrame."""
        session = self.Session()
        try:
            query = session.query(Trade).filter(Trade.symbol == symbol.upper().strip())
            df = pd.read_sql(query.statement, self.engine)
            return df
        finally:
            session.close()
