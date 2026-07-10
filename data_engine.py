"""
InvestYo Quant Platform - Data Acquisition & Provider Interface
===============================================================
Step 4 of the Modernization Roadmap: Dependency Injection & Decoupling.

This module introduces the IDataProvider Abstract Base Class (ABC) interface,
allowing data consumption layers to be isolated from real-time API integrations.
It provides both the live DataEngine and the deterministic MockDataEngine.
"""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Any, Optional

from settings import settings

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
    def fetch_macro_history(self) -> pd.DataFrame:
        """Fetches historical daily macro series (VIXCLS, T10Y2Y) for regime models
        (e.g. regime/hmm_regime.py) that need an expanding-window time series rather
        than a single current snapshot. Returns an empty DataFrame (never fabricated
        defaults) when the underlying source is unavailable."""
        pass

    @abstractmethod
    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """Fetches historical price series (OHLCV) for a group of assets."""
        pass

    @abstractmethod
    def fetch_fundamentals_raw(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetches fundamental data, income statements, and balance sheets."""
        pass

    @abstractmethod
    def fetch_options_chain(self, ticker: str, expiration: Optional[str] = None) -> Any:
        """Fetches option chain or options metadata for a ticker.
        If expiration is specified, returns an OptionChain-like object with .calls and .puts.
        If expiration is None, returns a list of expiration date strings.
        """
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

    def fetch_macro_history(self) -> pd.DataFrame:
        """
        Fetches full historical daily series for VIXCLS and T10Y2Y from FRED.
        Used by regime/hmm_regime.py to fit/refit on an expanding window -- a
        single current-snapshot value (fetch_macro_raw) cannot train a time-series
        model. Returns an empty DataFrame (never fabricated placeholder rows) if
        FRED is unavailable or the fetch fails.
        """
        if not self.fred:
            logger.warning("FRED API not initialized. Cannot fetch macro history.")
            return pd.DataFrame(columns=['VIXCLS', 'T10Y2Y'])

        try:
            vix_series = self.fred.get_series('VIXCLS').rename('VIXCLS')
            yield_curve_series = self.fred.get_series('T10Y2Y').rename('T10Y2Y')
            history_df = pd.concat([vix_series, yield_curve_series], axis=1)
            history_df.index = pd.to_datetime(history_df.index)
            return history_df.sort_index()
        except Exception as e:
            logger.error(f"Error fetching macro history from FRED: {e}")
            return pd.DataFrame(columns=['VIXCLS', 'T10Y2Y'])

    def fetch_technical_raw(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Fetches daily historical pricing (OHLCV) spanning the last 2 years, in
        parallel across tickers (network I/O bound -- yfinance's blocking HTTP
        call releases the GIL, so a thread pool collapses wall-clock time to
        roughly N/workers instead of N sequential round-trips). Each ticker's
        fetch is isolated in try/except so one bad symbol never aborts the
        batch (dead-letter resilience). Set settings.DATA_FETCH_MAX_CONCURRENCY=1
        to force the original sequential path.
        """
        def _fetch_one(symbol: str) -> tuple[str, Optional[pd.DataFrame]]:
            try:
                # Require historical lookback window to calculate 200-day rolling states & indicators
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="2y")
                if not df.empty:
                    logger.info(f"Retrieved technical time series for {symbol}")
                    return symbol, df
                logger.warning(f"No technical series found for {symbol}")
            except Exception as e:
                logger.error(f"Failed to fetch technical series for {symbol}: {e}")
            return symbol, None

        workers = max(1, int(getattr(settings, "DATA_FETCH_MAX_CONCURRENCY", 8)))
        if workers == 1 or len(tickers) <= 1:
            pairs = [_fetch_one(symbol) for symbol in tickers]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(tickers))) as pool:
                pairs = list(pool.map(_fetch_one, tickers))
        return {symbol: df for symbol, df in pairs if df is not None}

    def fetch_technical_raw_cached(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Like ``fetch_technical_raw()``, but routes each ticker through
        ``data.historical_store.HistoricalStore.get_bars()`` when
        ``settings.HISTORICAL_STORE_ENABLED`` is True, so a symbol whose bars
        are already persisted only needs its delta ``(last_date, today]``
        fetched instead of a full 2-year yfinance re-pull every cycle. This
        closes the one remaining tech-bars call site
        (``main_orchestrator.py::fetch_all_data_async``) that bypassed
        ``HistoricalStore`` entirely, unlike ``main.py``'s
        ``_fetch_bars_for_universe()`` which already routes through it.

        Falls back to the EXACT ``fetch_technical_raw()`` behavior (same
        ``ThreadPoolExecutor``/``DATA_FETCH_MAX_CONCURRENCY`` concurrency
        pattern, identical ``{symbol: DataFrame}`` shape with
        Open/High/Low/Close/Volume columns and a tz-naive ``DatetimeIndex``)
        on any ``HistoricalStore``/provider construction or import failure,
        or entirely when ``settings.HISTORICAL_STORE_ENABLED`` is False --
        byte-identical output either way. Never modifies
        ``fetch_technical_raw()`` itself.
        """
        if not getattr(settings, "HISTORICAL_STORE_ENABLED", True):
            return self.fetch_technical_raw(tickers)

        try:
            from data.historical_store import HistoricalStore
            from data.market_data import get_provider

            _store = HistoricalStore()
            _provider = get_provider()
        except Exception as e:
            logger.warning(
                f"fetch_technical_raw_cached: HistoricalStore/provider unavailable "
                f"({e}); falling back to direct fetch_technical_raw()."
            )
            return self.fetch_technical_raw(tickers)

        lookback_days = int(getattr(settings, "BARS_BACKFILL_DAYS", 504))

        def _fetch_one(symbol: str) -> tuple[str, Optional[pd.DataFrame]]:
            try:
                df = _store.get_bars(symbol, lookback_days=lookback_days, provider=_provider)
                if df is not None and not df.empty:
                    logger.info(f"Retrieved cached/incremental technical time series for {symbol}")
                    return symbol, df
                logger.warning(f"No technical series found for {symbol} via HistoricalStore")
            except Exception as e:
                logger.error(f"Failed to fetch cached technical series for {symbol}: {e}")
            return symbol, None

        workers = max(1, int(getattr(settings, "DATA_FETCH_MAX_CONCURRENCY", 8)))
        if workers == 1 or len(tickers) <= 1:
            pairs = [_fetch_one(symbol) for symbol in tickers]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(tickers))) as pool:
                pairs = list(pool.map(_fetch_one, tickers))
        return {symbol: df for symbol, df in pairs if df is not None}

    def fetch_fundamentals_raw(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetches equity fundamentals through the shared
        ``data.market_data.CompositeProvider`` singleton (Yahoo
        statement-derived engine, primary — see ``data/yahoo_fundamentals.py``)
        in parallel across tickers, network I/O bound, same rationale as
        fetch_technical_raw. The bounded worker count is also the de-facto
        rate limit, replacing the old serial sleep(0.1)-every-5-tickers
        throttle (which only made sense when fetches ran one at a time). Each
        ticker is isolated in try/except (dead-letter resilience). Set
        settings.DATA_FETCH_MAX_CONCURRENCY=1 to force the original sequential
        path.

        This used to call ``yf.Ticker(symbol).info`` directly, bypassing
        CompositeProvider entirely — the one remaining fundamentals path in
        the codebase that violated the "all fundamentals fetches go through
        CompositeProvider" convention (see CLAUDE.md). Routing through the
        singleton here means the multifactor signal's raw inputs
        (book_to_market, earnings_yield, quality_factor_score, debt_to_equity
        — computed downstream in processing_engine.calculate_fundamental_metrics)
        finally reflect the statement-derived engine instead of stale raw
        yfinance .info data. The provider's ``dividendYield`` is already
        correctly scaled (a fraction) by whichever backend is active
        internally — do NOT re-normalize it here.
        """
        from data.market_data import get_provider

        provider = get_provider()
        total = len(tickers)

        def _fetch_one(indexed_symbol: tuple[int, str]) -> tuple[str, Optional[Dict[str, Any]]]:
            idx, symbol = indexed_symbol
            try:
                info = provider.get_fundamentals(symbol) or {}
                try:
                    dividends = yf.Ticker(symbol).dividends
                except Exception as e:
                    logger.debug(f"No dividend history for {symbol}: {e}")
                    dividends = pd.Series(dtype='float64')
                ticker_data = {'info': info, 'dividends': dividends}
                logger.info(f"Fund data fetched: {idx}/{total} - {symbol}")
                return symbol, ticker_data
            except Exception as e:
                logger.warning(f"Failed fundamental parsing for {symbol}: {e}")
            return symbol, None

        workers = max(1, int(getattr(settings, "DATA_FETCH_MAX_CONCURRENCY", 8)))
        indexed = list(enumerate(tickers, 1))
        if workers == 1 or len(tickers) <= 1:
            pairs = [_fetch_one(item) for item in indexed]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(tickers))) as pool:
                pairs = list(pool.map(_fetch_one, indexed))
        return {symbol: data for symbol, data in pairs if data is not None}

    def fetch_options_chain(self, ticker: str, expiration: Optional[str] = None) -> Any:
        """
        Fetches yfinance option chain or expirations list.
        """
        try:
            t = yf.Ticker(ticker)
            if expiration is None:
                return list(t.options)
            else:
                return t.option_chain(expiration)
        except Exception as e:
            logger.error(f"Failed to fetch options chain for {ticker} (exp={expiration}): {e}")
            if expiration is None:
                return []
            return None


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

    def fetch_macro_history(self) -> pd.DataFrame:
        """Deterministic synthetic VIXCLS/T10Y2Y history for tests -- long enough
        (500 trading days) for HMM fitting without requiring network access."""
        rng = np.random.RandomState(42)
        n = 500
        dates = pd.date_range(end=datetime.now(), periods=n, freq='B')
        vix = pd.Series(15.0 + rng.normal(0, 3.0, n).cumsum() * 0.05, index=dates).clip(lower=9.0)
        yield_curve = pd.Series(0.5 + rng.normal(0, 0.05, n).cumsum() * 0.02, index=dates)
        return pd.DataFrame({'VIXCLS': vix, 'T10Y2Y': yield_curve})

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

    def fetch_technical_raw_cached(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Deterministic-test alias for ``fetch_technical_raw()``. MockDataEngine
        has no real per-ticker network fetch to cache against a DB -- its
        bars are synthesized fresh from ``datetime.now()`` on every call, so
        there is nothing to incrementally "top up" and no HistoricalStore
        involvement makes sense here. Exists purely so callers that
        unconditionally call ``fetch_technical_raw_cached()``
        (``main_orchestrator.py``'s ``fetch_all_data_async``, which falls
        back to a fresh ``MockDataEngine()`` when ``credentials.json`` is
        absent or live data comes back empty) work identically whether
        ``de`` is a real ``DataEngine`` or this test/offline-fallback fixture.
        """
        return self.fetch_technical_raw(tickers)

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

    def fetch_options_chain(self, ticker: str, expiration: Optional[str] = None) -> Any:
        """
        Deterministic mock options chain generator.
        """
        today = datetime.now()
        # Front month (15 days out) and second month (45 days out)
        exp1 = (today + timedelta(days=15)).strftime("%Y-%m-%d")
        exp2 = (today + timedelta(days=45)).strftime("%Y-%m-%d")
        
        if expiration is None:
            return [exp1, exp2]
        
        # Get spot price
        spot = 100.0
        try:
            tech = self.fetch_technical_raw([ticker])
            if ticker in tech and not tech[ticker].empty:
                spot = float(tech[ticker]['Close'].iloc[-1])
        except Exception:
            pass

        # Generate strikes around spot
        strikes = [round(spot * factor * 2) / 2 for factor in [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]]
        
        calls_data = []
        puts_data = []
        
        for k in strikes:
            # Deterministic IV smile
            iv = 0.25 + 0.15 * ((k - spot) / spot) ** 2
            # Add small difference for front vs second month to test interpolation
            if expiration == exp2:
                iv += 0.05
            
            # Simple call/put pricing
            calls_data.append({
                'strike': float(k),
                'impliedVolatility': float(iv),
                'lastPrice': max(0.1, spot - k),
                'bid': max(0.05, spot - k - 0.05),
                'ask': max(0.15, spot - k + 0.05)
            })
            puts_data.append({
                'strike': float(k),
                'impliedVolatility': float(iv),
                'lastPrice': max(0.1, k - spot),
                'bid': max(0.05, k - spot - 0.05),
                'ask': max(0.15, k - spot + 0.05)
            })
            
        class MockOptionChain:
            def __init__(self, c, p):
                self.calls = pd.DataFrame(c)
                self.puts = pd.DataFrame(p)
                
        return MockOptionChain(calls_data, puts_data)

