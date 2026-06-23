"""
InvestYo Quant Platform - Data Acquisition & Provider Interface
===============================================================
Step 4 of the Modernization Roadmap: Dependency Injection & Decoupling.

This module introduces the IDataProvider Abstract Base Class (ABC) interface,
allowing data consumption layers to be isolated from real-time API integrations.
It provides both the live DataEngine and the deterministic MockDataEngine.
"""

from abc import ABC, abstractmethod
import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred
from datetime import datetime, timedelta
import logging
import time
from typing import Dict, List, Any, Optional

# Configure module-level logger
logger = logging.getLogger("Data_Engine")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# =============================================================================
# 1. ABSTRACT DATA PROVIDER INTERFACE
# =============================================================================
class IDataProvider(ABC):
    """
    Abstract contract dictating data requirements for the quantitative engine.
    Allows easy swapping of data vendors (e.g., Yahoo, Alpaca, Bloomberg, Mock).
    """
    
    @abstractmethod
    def fetch_macro_raw(self) -> Dict[str, Any]:
        """Fetches raw macroeconomic indicators (e.g., FRED indicators)."""
        pass

    @abstractmethod
    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """Fetches historical price series (OHLCV) for a group of assets."""
        pass

    @abstractmethod
    def fetch_fundamentals_raw(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetches fundamental data, income statements, and balance sheets."""
        pass


# =============================================================================
# 2. OPERATIONAL YAHOO FINANCE & FRED ENGINE
# =============================================================================
class DataEngine(IDataProvider):
    """
    Production-grade data ingestion engine powered by Yahoo Finance and FRED.
    """
    def __init__(self, fred_api_key: str):
        # Silence yfinance internal logs to keep console output pristine
        logging.getLogger('yfinance').setLevel(logging.CRITICAL)
        
        self.fred_key = fred_api_key
        if fred_api_key:
            try:
                self.fred = Fred(api_key=fred_api_key)
            except Exception as e:
                logger.warning(f"⚠️ FRED Initialization Failed: {e}")
                self.fred = None
        else:
            self.fred = None

    def fetch_macro_raw(self) -> Dict[str, Any]:
        """
        Pulls macroeconomic indices from FRED.
        """
        if not self.fred:
            logger.warning("FRED API not initialized. Returning baseline defaults.")
            return {'T10Y2Y': 0.5, 'BAMLH0A0HYM2': 3.5, 'UNRATE': 3.8, 'VIXCLS': 15.0}
            
        try:
            # Yield Curve, OAS Corporate Spread, Unemployment, VIX
            t10y2y = self.fred.get_series('T10Y2Y', limit=1).iloc[-1]
            oas = self.fred.get_series('BAMLH0A0HYM2', limit=1).iloc[-1]
            unrate = self.fred.get_series('UNRATE', limit=1).iloc[-1]
            try:
                vix = self.fred.get_series('VIXCLS', limit=5).dropna().iloc[-1]
            except Exception:
                vix = 15.0
            return {
                'T10Y2Y': float(t10y2y),
                'BAMLH0A0HYM2': float(oas),
                'UNRATE': float(unrate),
                'VIXCLS': float(vix)
            }
        except Exception as e:
            logger.error(f"Error fetching economic data from FRED: {e}")
            return {'T10Y2Y': 0.5, 'BAMLH0A0HYM2': 3.5, 'UNRATE': 3.8, 'VIXCLS': 15.0}

    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Fetches daily historical pricing (OHLCV) spanning the last 250 trading days.
        """
        raw_tech = {}
        for symbol in tickers:
            try:
                # Require historical lookback window to calculate 200-day rolling states & indicators
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="1y")
                if not df.empty:
                    raw_tech[symbol] = df
                    logger.info(f"Retrieved technical time series for {symbol}")
                else:
                    logger.warning(f"No technical series found for {symbol}")
            except Exception as e:
                logger.error(f"Failed to fetch technical series for {symbol}: {e}")
        return raw_tech

    def fetch_fundamentals_raw(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetches yfinance corporate profiling metrics and balance sheets.
        """
        raw_fundamentals = {}
        total = len(tickers)
        for idx, symbol in enumerate(tickers, 1):
            try:
                t = yf.Ticker(symbol)
                ticker_data = {
                    'info': t.info or {},
                    'dividends': t.dividends if hasattr(t, 'dividends') else pd.Series(dtype='float64'),
                    'financials': t.financials if hasattr(t, 'financials') else pd.DataFrame()
                }
                raw_fundamentals[symbol] = ticker_data
                logger.info(f"Fund data fetched: {idx}/{total} - {symbol}")
                
                # Dynamic throttling to comply with API rate limits
                if idx % 5 == 0:
                    time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Failed fundamental parsing for {symbol}: {e}")
        return raw_fundamentals


# =============================================================================
# 3. HIGH-FIDELITY MOCK DATA ENGINE (DETERMINISTIC UNIT TESTING)
# =============================================================================
class MockDataEngine(IDataProvider):
    """
    Deterministic data engine used to isolate math calculations from external networks.
    """
    def __init__(self, preset_prices: Optional[List[float]] = None, 
                 preset_macro: Optional[Dict[str, float]] = None,
                 preset_fund: Optional[Dict[str, Any]] = None):
        self.preset_prices = preset_prices if preset_prices is not None else [10.0] * 30
        self.preset_macro = preset_macro if preset_macro is not None else {
            'T10Y2Y': 0.5,
            'BAMLH0A0HYM2': 3.5,
            'UNRATE': 4.0
        }
        self.preset_fund = preset_fund if preset_fund is not None else {
            'AAPL': {
                'info': {
                    'shortName': 'Mock Apple Corp',
                    'sector': 'Technology',
                    'trailingPE': 28.5,
                    'priceToBook': 15.2,
                    'bookValue': 12.50,
                    'trailingEps': 6.20,
                    'dividendYield': 0.005,
                    'payoutRatio': 0.15
                }
            }
        }

    def fetch_macro_raw(self) -> Dict[str, Any]:
        return self.preset_macro

    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        # Synthesize a highly standardized Pandas DataFrame tracking pricing days
        results = {}
        for ticker in tickers:
            dates = pd.date_range(end=datetime.now(), periods=len(self.preset_prices))
            df = pd.DataFrame({
                'Open': self.preset_prices,
                'High': [p * 1.02 for p in self.preset_prices],
                'Low': [p * 0.98 for p in self.preset_prices],
                'Close': self.preset_prices,
                'Volume': [1000000] * len(self.preset_prices)
            }, index=dates)
            results[ticker] = df
        return results

    def fetch_fundamentals_raw(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        results = {}
        for ticker in tickers:
            results[ticker] = self.preset_fund.get(ticker, {
                'info': {
                    'shortName': f'Mock {ticker} Corp',
                    'sector': 'Technology',
                    'trailingPE': 15.0,
                    'priceToBook': 1.5,
                    'bookValue': 10.0,
                    'trailingEps': 2.0,
                    'dividendYield': 0.02,
                    'payoutRatio': 0.30
                }
            })
        return results
