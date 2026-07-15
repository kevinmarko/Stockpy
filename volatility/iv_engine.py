"""Implied-volatility engine: extracts ATM option IVs, performs calendar-30-day linear interpolation, and computes lookahead-free true IV rank and the Volatility Risk Premium (VRP) used to gate premium-selling strategies."""

import os
import logging
import math
from datetime import datetime, date, timedelta
from typing import Optional, Any, Tuple, List, Dict
import pandas as pd
import numpy as np
from sqlalchemy import Column, Integer, String, Float, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import resolve_database_url, create_db_engine, session_scope

logger = logging.getLogger("IV_Engine")

# Database configuration consistent with transactions_store.py
DB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(DB_DIR, "quant_platform.db")
DATABASE_URL = f"sqlite:///{DB_FILE}"

Base = declarative_base()

class IVHistory(Base):
    """
    ORM Model for storing historical 30-day ATM implied volatilities.
    """
    __tablename__ = 'iv_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False)
    date = Column(String(10), nullable=False)  # Format: YYYY-MM-DD
    iv_30d_atm = Column(Float, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('ticker', 'date', name='_ticker_date_uc'),
    )

class IVHistoryStore:
    def __init__(self, db_url: Optional[str] = None):
        db_url = db_url or resolve_database_url()
        self.engine = create_db_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_iv(self, ticker: str, date_val: Any, iv_val: float) -> None:
        """
        Inserts or updates an IV record for a specific ticker and date.
        """
        try:
            with session_scope(self.Session) as session:
                # Parse date to standard string format
                date_str = _parse_date_to_str(date_val)
                ticker_clean = ticker.upper().strip()

                # Check if record already exists
                record = session.query(IVHistory).filter(
                    IVHistory.ticker == ticker_clean,
                    IVHistory.date == date_str
                ).first()

                if record:
                    record.iv_30d_atm = float(iv_val)
                else:
                    record = IVHistory(
                        ticker=ticker_clean,
                        date=date_str,
                        iv_30d_atm=float(iv_val)
                    )
                    session.add(record)
        except Exception as e:
            logger.error(f"Failed to record IV for {ticker} on {date_val}: {e}")
            raise e

    def get_historical_ivs(self, ticker: str, as_of_date: Any, lookback_days: int = 252) -> List[float]:
        """
        Fetches historical IV values prior to the given as_of_date (strict no-lookahead).
        """
        session = self.Session()
        try:
            date_str = _parse_date_to_str(as_of_date)
            ticker_clean = ticker.upper().strip()
            
            # Strict date < as_of_date constraint
            records = session.query(IVHistory).filter(
                IVHistory.ticker == ticker_clean,
                IVHistory.date < date_str
            ).order_by(IVHistory.date.desc()).limit(lookback_days).all()
            
            return [r.iv_30d_atm for r in records]
        except Exception as e:
            logger.error(f"Failed to retrieve IV history for {ticker} prior to {as_of_date}: {e}")
            return []
        finally:
            session.close()


def _parse_date_to_str(d: Any) -> str:
    """Helper to parse datetime, date, or string into standard YYYY-MM-DD string."""
    if isinstance(d, str):
        return d.strip()[:10]
    elif isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    elif isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    else:
        raise ValueError(f"Unsupported date format: {d}")


def get_30d_atm_iv(data_engine: Any, ticker: str, as_of_date: Any, spot_price: Optional[float] = None) -> float:
    """
    Fetches the front-month and second-month option chains and linear interpolates to 30 calendar days.
    Averages call and put IV for the closest strike to spot (ATM).
    Returns float(NaN) if any step fails or data is insufficient.
    """
    try:
        as_of_dt = datetime.strptime(_parse_date_to_str(as_of_date), "%Y-%m-%d")
        
        # Get spot price if not provided
        if spot_price is None:
            tech = data_engine.fetch_technical_raw([ticker])
            if ticker in tech and not tech[ticker].empty:
                df_filtered = tech[ticker].loc[tech[ticker].index <= pd.to_datetime(as_of_dt)]
                if not df_filtered.empty:
                    spot_price = float(df_filtered['Close'].iloc[-1])
                    
        if spot_price is None or spot_price <= 0:
            logger.warning(f"No valid spot price for {ticker} as of {as_of_date}. Cannot compute IV.")
            return float('nan')

        # Get all expirations
        expirations = data_engine.fetch_options_chain(ticker)
        if not expirations or len(expirations) == 0:
            logger.warning(f"No expirations returned for {ticker} as of {as_of_date}.")
            return float('nan')

        # Filter and sort expirations strictly in the future relative to as_of_date
        future_exps = []
        for exp in expirations:
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                days_diff = (exp_dt - as_of_dt).days
                if days_diff >= 0:
                    future_exps.append((exp, days_diff))
            except Exception:
                continue

        future_exps.sort(key=lambda x: x[1])

        if len(future_exps) < 2:
            logger.warning(f"Fewer than 2 future expirations found for {ticker} as of {as_of_date}. Exps: {future_exps}")
            # If we only have 1, we can return it as fallback or NaN. Let's return NaN to enforce linear interpolation.
            return float('nan')

        # near term (front-month) and next term (second-month)
        t1, d1 = future_exps[0]
        t2, d2 = future_exps[1]

        # Fetch chains
        chain_1 = data_engine.fetch_options_chain(ticker, t1)
        chain_2 = data_engine.fetch_options_chain(ticker, t2)

        if not chain_1 or not chain_2:
            logger.warning(f"Could not fetch options chains for {ticker} expirations {t1} or {t2}.")
            return float('nan')

        # Compute ATM IV for each chain
        iv1 = _calculate_atm_iv_from_chain(chain_1, spot_price)
        iv2 = _calculate_atm_iv_from_chain(chain_2, spot_price)

        if math.isnan(iv1) or math.isnan(iv2):
            logger.warning(f"Failed to calculate ATM IV for {ticker} at {t1} or {t2}.")
            return float('nan')

        # Linear interpolation to 30 days
        if d2 == d1:
            return float(max(0.0001, iv1))
        
        iv_30 = iv1 + (iv2 - iv1) * (30.0 - d1) / (d2 - d1)
        return float(max(0.0001, iv_30))

    except Exception as e:
        logger.error(f"Error computing 30d ATM IV for {ticker} as of {as_of_date}: {e}")
        return float('nan')


def _calculate_atm_iv_from_chain(chain: Any, spot_price: float) -> float:
    """Helper to extract ATM call/put averaged IV from an OptionChain-like object."""
    try:
        calls = chain.calls
        puts = chain.puts
        
        if calls.empty and puts.empty:
            return float('nan')

        # Find closest strike
        all_strikes = pd.concat([calls['strike'], puts['strike']]).unique()
        if len(all_strikes) == 0:
            return float('nan')
            
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))

        call_iv = float('nan')
        put_iv = float('nan')

        if not calls.empty:
            call_row = calls[calls['strike'] == atm_strike]
            if not call_row.empty and 'impliedVolatility' in call_row.columns:
                call_iv = float(call_row['impliedVolatility'].iloc[0])

        if not puts.empty:
            put_row = puts[puts['strike'] == atm_strike]
            if not put_row.empty and 'impliedVolatility' in put_row.columns:
                put_iv = float(put_row['impliedVolatility'].iloc[0])

        # Average ATM call and put IV
        ivs = [iv for iv in [call_iv, put_iv] if not math.isnan(iv) and iv > 0]
        if len(ivs) > 0:
            return sum(ivs) / len(ivs)
        return float('nan')

    except Exception as e:
        logger.warning(f"Error calculating ATM IV from chain: {e}")
        return float('nan')


def calculate_true_ivr(ticker: str, current_iv: float, as_of_date: Any, store: IVHistoryStore, lookback_days: int = 252) -> float:
    """
    Calculates Implied Volatility Rank (IVR) strictly using historical IV prior to as_of_date.
    Formula: IVR = (current_iv - min_252d) / (max_252d - min_252d) * 100.
    Returns float(nan) if history is empty.
    """
    if math.isnan(current_iv) or current_iv <= 0:
        return float('nan')
        
    history = store.get_historical_ivs(ticker, as_of_date, lookback_days)
    if not history:
        return float('nan')
        
    # We include current_iv in the min/max calculation to establish current boundaries
    all_ivs = history + [current_iv]
    min_iv = min(all_ivs)
    max_iv = max(all_ivs)
    
    if max_iv == min_iv:
        return 50.0  # neutral rank when range is zero
        
    ivr = (current_iv - min_iv) / (max_iv - min_iv) * 100.0
    return float(max(0.0, min(100.0, ivr)))


def get_vrp(ticker: str, current_iv: float, garch_vol: float) -> float:
    """
    Calculates Volatility Risk Premium: implied volatility minus realized forecast volatility.
    """
    if math.isnan(current_iv) or math.isnan(garch_vol):
        return float('nan')
    return float(current_iv - garch_vol)
