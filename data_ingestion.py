# ==============================================================================
# MODULE: DATA ACQUISITION & DATABASE SETUP
# Description: Fully vectorized OHLCV fetcher and normalized SQLite database 
#              initialization (pgsqlite compatible).
# ==============================================================================

import os
import sys
import sqlite3
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod

# Ensure root dir is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Configure module-level logging
logger = logging.getLogger("Data_Backend")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ------------------------------------------------------------------------------
# 1. ABSTRACT DATA PROVIDER & DATA ENGINE
# ------------------------------------------------------------------------------
class IDataProvider(ABC):
    """Abstract contract dictating vectorized data requirements."""
    @abstractmethod
    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        pass

class DataEngine(IDataProvider):
    """Production-grade data ingestion engine powered by Yahoo Finance."""
    def __init__(self):
        self.logger = logging.getLogger("DataEngine")

    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Fetches daily historical pricing (OHLCV) spanning the last 250 trading days.
        Utilizes vectorized data structures natively via Pandas.
        """
        raw_tech = {}
        for symbol in tickers:
            try:
                # Require historical lookback window to calculate rolling states & indicators
                ticker_obj = yf.Ticker(symbol)
                df = ticker_obj.history(period="1y")
                
                if not df.empty:
                    # Ensure DataFrame is sorted chronologically
                    df = df.sort_index()
                    raw_tech[symbol] = df
                    self.logger.info(f"Retrieved technical time series for {symbol}")
                else:
                    self.logger.warning(f"No technical series found for {symbol}")
            except Exception as e:
                self.logger.error(f"Failed to fetch technical series for {symbol}: {e}")
                
        return raw_tech

# ------------------------------------------------------------------------------
# 2. VECTORIZED INDICATOR PROCESSOR
# ------------------------------------------------------------------------------
class VectorizedProcessor:
    """
    Zero-loop mathematical indicator processing using Pandas vectorization.
    """
    @staticmethod
    def calculate_rsi_vectorized(series: pd.Series, period: int = 14) -> pd.Series:
        """Vectorized Relative Strength Index (RSI) calculation."""
        delta = series.diff()
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        
        avg_gain = pd.Series(gain).rolling(window=period, min_periods=period).mean()
        avg_loss = pd.Series(loss).rolling(window=period, min_periods=period).mean()
        
        rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
        return 100.0 - (100.0 / (1.0 + rs))

# ------------------------------------------------------------------------------
# 3. RELATIONAL DATABASE CONFIGURATION (pgsqlite compatible)
# ------------------------------------------------------------------------------
import database_setup

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DB_DIR, "quant_platform.db")

def initialize_database(db_file: str = DB_FILE):
    """
    Establishes the connection to the SQLite database and initializes tables
    by delegating to database_setup.py.
    """
    database_setup.initialize_database(db_file)

# ------------------------------------------------------------------------------
# 4. INGESTION WORKFLOW
# ------------------------------------------------------------------------------
def run_ingestion(tickers: List[str]):
    """Runs the ingestion workflow using DataEngine and logging results to DB."""
    start_time = datetime.now()
    initialize_database()

    de = DataEngine()
    logger.info(f"Fetching data for tickers: {tickers}")
    tech_data = de.fetch_technical_raw(tickers)

    success_count = 0
    error_msg = None

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        for ticker, df in tech_data.items():
            try:
                # Compute vectorized RSI
                df['RSI'] = VectorizedProcessor.calculate_rsi_vectorized(df['Close'])
                latest_price = float(df['Close'].iloc[-1])
                latest_rsi = float(df['RSI'].iloc[-1]) if not pd.isna(df['RSI'].iloc[-1]) else 0.0

                # Log signal state using dynamic COLUMN_SCHEMA aligned keys
                cursor.execute("""
                    INSERT INTO DailySignals ("Symbol", "Price", "RSI")
                    VALUES (?, ?, ?)
                """, (ticker, latest_price, latest_rsi))
                
                success_count += 1
            except Exception as e:
                logger.error(f"Error processing ticker {ticker}: {e}")
                error_msg = str(e)

        # Log execution details
        execution_time = (datetime.now() - start_time).total_seconds()
        status = "SUCCESS" if success_count == len(tickers) else "PARTIAL_SUCCESS" if success_count > 0 else "FAILED"
        
        cursor.execute("""
            INSERT INTO ExecutionLogs (status, ticker_count, execution_time_seconds, error_message)
            VALUES (?, ?, ?, ?)
        """, (status, len(tickers), execution_time, error_msg))

        conn.commit()

    logger.info(f"Ingestion completed in {execution_time:.2f} seconds. Status: {status}")

if __name__ == "__main__":
    # Test run ingestion for AAPL and MSFT
    run_ingestion(["AAPL", "MSFT"])
