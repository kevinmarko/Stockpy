import logging
import os
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Union
from sqlalchemy import Column, Integer, String, Float, DateTime, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import resolve_database_url, create_db_engine, session_scope

logger = logging.getLogger(__name__)

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DB_DIR, "quant_platform.db")
DATABASE_URL = f"sqlite:///{DB_FILE}"

Base = declarative_base()

class Trade(Base):
    __tablename__ = 'trades'
    
    trade_id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)  # 'long' or 'short'
    entry_ts = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    entry_price = Column(Float, nullable=False)
    exit_ts = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    shares = Column(Float, nullable=False)
    strategy = Column(String(50), nullable=True)
    notes = Column(String(255), nullable=True)
    conviction = Column(Float, nullable=True)  # advisory signal conviction [0,1] at entry

class TransactionsStore:
    def __init__(self, db_url: Optional[str] = None):
        db_url = db_url or resolve_database_url()
        self.engine = create_db_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._ensure_conviction_column()

    def _ensure_conviction_column(self) -> None:
        """Add conviction column to existing DBs that predate this feature."""
        try:
            insp = inspect(self.engine)
            existing = {c["name"] for c in insp.get_columns("trades")}
            if "conviction" not in existing:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE trades ADD COLUMN conviction REAL"))
        except Exception as exc:
            logger.debug("_ensure_conviction_column: %s", exc)

    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_ts: datetime,
        entry_price: float,
        shares: float,
        strategy: Optional[str] = None,
        notes: Optional[str] = None,
        conviction: Optional[float] = None,
    ) -> int:
        """Records a new open trade. Returns the trade_id."""
        with session_scope(self.Session) as session:
            # Ensure naive datetime for SQL consistency
            naive_entry_ts = entry_ts.replace(tzinfo=None) if entry_ts else datetime.now(timezone.utc).replace(tzinfo=None)
            trade = Trade(
                symbol=symbol.upper().strip(),
                side=side.lower().strip(),
                entry_ts=naive_entry_ts,
                entry_price=float(entry_price),
                shares=float(shares),
                strategy=strategy,
                notes=notes,
                conviction=float(conviction) if conviction is not None else None,
            )
            session.add(trade)
            session.flush()  # populate the autoincrement PK before the session closes
            trade_id = int(trade.trade_id)
        return trade_id

    def close_trade(self, trade_id: int, exit_ts: datetime, exit_price: float) -> None:
        """Closes an open trade by trade_id."""
        with session_scope(self.Session) as session:
            trade = session.query(Trade).filter(Trade.trade_id == trade_id).first()
            if not trade:
                raise ValueError(f"Trade ID {trade_id} not found.")
            # Ensure naive datetime for SQL consistency
            naive_exit_ts = exit_ts.replace(tzinfo=None) if exit_ts else datetime.now(timezone.utc).replace(tzinfo=None)
            trade.exit_ts = naive_exit_ts
            trade.exit_price = float(exit_price)

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
