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
from contextlib import contextmanager
import socket
import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred
from datetime import datetime, timedelta, timezone
import logging
from typing import Dict, FrozenSet, List, Any, Optional, Tuple

from settings import settings

# Configure module-level logger
logger = logging.getLogger("Data_Engine")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


@contextmanager
def _bounded_fred_timeout(seconds: float):
    """Bounds every socket opened inside the ``with`` block to ``seconds``.

    2026-08 fix: ``fredapi.Fred.get_series()`` calls a bare ``urlopen(url)``
    with no timeout parameter and no session-injection hook anywhere in the
    class (confirmed against the installed library source) -- a stalled FRED
    connection used to block forever, wedging the entire pipeline cycle (see
    docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md).
    ``socket.setdefaulttimeout()`` scoped as narrowly as possible around the
    call is the only lever available short of vendoring fredapi.

    Process-global, not thread-local: the previous default is always restored
    in ``finally``, even on exception, so this can only ever ADD a bound to a
    call that had none -- every other network call in this codebase that
    matters already sets its own explicit ``timeout=`` (FMP, GDELT) rather
    than depending on the socket default, so the narrow window where another
    thread opens an unrelated socket during this block (and would inherit
    this same bound) has no observed downside.
    """
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)


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

        # Which keys of the most recent fetch_macro_raw() return value are
        # fabricated placeholders rather than real FRED/FMP readings -- see
        # fetch_macro_raw_detailed()'s docstring. Read by callers building a
        # MacroEconomicDTO (main.py, pipeline/production_steps.py,
        # investyo_mcp_server.py) via
        # getattr(de, "last_macro_raw_fabricated_keys", frozenset()) right
        # after calling fetch_macro_raw(), so they can feed
        # macro_engine.macro_killswitch_data_unavailable(...,
        # fabricated_keys=...) instead of trusting key-presence alone
        # (CONSTRAINT #4/#6: a populated-but-fabricated key must never read
        # as real data for a safety-critical gate).
        self.last_macro_raw_fabricated_keys: FrozenSet[str] = frozenset()

    # The hardcoded emergency snapshot -- a known CONSTRAINT #4 violation kept
    # only as the LAST resort when neither FRED nor (if enabled) FMP can serve
    # real data, so a total macro-data outage never crashes the pipeline. Do
    # not "improve" these numbers here -- see settings.FMP_MACRO_ENABLED's
    # docstring and data/fmp_macro.py for the honest replacement path.
    _MACRO_HARDCODED_FALLBACK: Dict[str, float] = {
        'T10Y2Y': 0.5, 'BAMLH0A0HYM2': 3.5, 'UNRATE': 3.8, 'VIXCLS': 15.0,
    }

    def fetch_macro_raw(self) -> Dict[str, Any]:
        """
        Pulls macroeconomic indices from FRED (unchanged, still the primary,
        higher-quality unrevised-vintage source where it works).

        Thin, byte-identical delegate over fetch_macro_raw_detailed() -- kept
        for every existing caller/test that only wants the plain dict. See
        fetch_macro_raw_detailed()'s docstring for the fabrication-tracking
        this method's callers may want (self.last_macro_raw_fabricated_keys
        is set as a side effect of this call too).
        """
        return self.fetch_macro_raw_detailed()[0]

    def fetch_macro_raw_detailed(self) -> Tuple[Dict[str, Any], FrozenSet[str]]:
        """Same computation as fetch_macro_raw, but also reports which of the
        returned dict's keys hold a fabricated placeholder value rather than
        a real FRED/FMP reading.

        Callers that construct a MacroEconomicDTO (main.py,
        pipeline/production_steps.py, investyo_mcp_server.py) need this
        second value because macro_engine.macro_killswitch_data_unavailable's
        plain key-presence check can't distinguish "a real reading" from "a
        substituted benign default" once that default is written into the
        same dict key -- CONSTRAINT #4 requires that distinction survive to
        the kill switch, not be silently discarded. Also sets
        self.last_macro_raw_fabricated_keys as a side effect, so a caller
        that only ever calls the plain fetch_macro_raw() (e.g. via
        asyncio.to_thread in a different function than the one that reads
        the result) can still recover this signal afterward via
        getattr(de, "last_macro_raw_fabricated_keys", frozenset()).

        When settings.FMP_MACRO_ENABLED is True (default False -- a complete
        no-op) AND the FRED snapshot above could not be produced at all, this
        falls back to data/fmp_macro.py's fetch_treasury_curve /
        fetch_unemployment_rate for T10Y2Y and UNRATE ONLY -- never for
        VIXCLS or BAMLH0A0HYM2, which have no FMP Starter-plan equivalent and
        stay FRED-only-or-fabricated-constant exactly as before. Only when
        BOTH FRED and (if enabled) FMP fail to improve on the hardcoded
        snapshot does that fabricated dict apply, and that case is logged at
        WARNING (CONSTRAINT #4 known exception, last resort).
        """
        fred_result: Optional[Dict[str, Any]] = None
        vix_fabricated = False
        if not self.fred:
            logger.warning("FRED API not initialized. Returning baseline defaults.")
        else:
            try:
                # Yield Curve, OAS Corporate Spread, Unemployment, VIX.
                # Bounded (2026-08): fredapi has no per-call timeout of its
                # own -- see _bounded_fred_timeout's docstring.
                with _bounded_fred_timeout(settings.FRED_REQUEST_TIMEOUT_SECONDS):
                    t10y2y = self.fred.get_series('T10Y2Y', limit=1).iloc[-1]
                    oas = self.fred.get_series('BAMLH0A0HYM2', limit=1).iloc[-1]
                    unrate = self.fred.get_series('UNRATE', limit=1).iloc[-1]
                    try:
                        vix = self.fred.get_series('VIXCLS', limit=5).dropna().iloc[-1]
                    except Exception:
                        # A narrower, silent VIX-only sub-fallback INSIDE an
                        # otherwise-successful FRED read: T10Y2Y/OAS/UNRATE are
                        # real, but VIX -- the single most load-bearing field for
                        # MacroEconomicDTO.killSwitch (vix > 30.0 fires it
                        # directly, no HMM agreement needed) -- is fabricated.
                        # Tracked below via vix_fabricated so this doesn't read
                        # as a fully-healthy fetch.
                        vix = 15.0
                        vix_fabricated = True
                fred_result = {
                    'T10Y2Y': float(t10y2y),
                    'BAMLH0A0HYM2': float(oas),
                    'UNRATE': float(unrate),
                    'VIXCLS': float(vix),
                }
            except Exception as e:
                logger.error(f"Error fetching economic data from FRED: {e}")

        if fred_result is not None:
            fabricated: FrozenSet[str] = frozenset({'VIXCLS'}) if vix_fabricated else frozenset()
            self.last_macro_raw_fabricated_keys = fabricated
            return fred_result, fabricated

        # FRED could not serve a full snapshot. Flag-off (the default) is a
        # byte-identical no-op: return the EXACT hardcoded dict FRED-failure
        # has always produced here, with ZERO data/fmp_macro.py import and
        # ZERO FMP network activity.
        if not getattr(settings, "FMP_MACRO_ENABLED", False):
            fallback = dict(self._MACRO_HARDCODED_FALLBACK)
            fabricated = frozenset(self._MACRO_HARDCODED_FALLBACK.keys())
            self.last_macro_raw_fabricated_keys = fabricated
            return fallback, fabricated

        result = dict(self._MACRO_HARDCODED_FALLBACK)
        fabricated_keys = set(self._MACRO_HARDCODED_FALLBACK.keys())
        fmp_error: Optional[Exception] = None
        try:
            from data.fmp_macro import fetch_treasury_curve, fetch_unemployment_rate

            to_date = datetime.now(timezone.utc).date()
            # A generous window: treasury rates are business-day-only and
            # UNRATE is a monthly release published with a real lag, so a
            # short window can legitimately return zero rows even when FMP
            # itself is healthy.
            from_date = to_date - timedelta(days=45)
            from_str, to_str = from_date.isoformat(), to_date.isoformat()

            curve_rows = fetch_treasury_curve(from_str, to_str)
            if curve_rows:
                result['T10Y2Y'] = float(curve_rows[-1]['value'])
                fabricated_keys.discard('T10Y2Y')

            unrate_rows = fetch_unemployment_rate(from_str, to_str)
            if unrate_rows:
                result['UNRATE'] = float(unrate_rows[-1]['value'])
                fabricated_keys.discard('UNRATE')
        except Exception as e:
            # fetch_treasury_curve / fetch_unemployment_rate never raise
            # (CONSTRAINT #6) -- this guards the import itself and any other
            # genuinely unexpected failure, so it never propagates.
            fmp_error = e

        if result == self._MACRO_HARDCODED_FALLBACK:
            # Neither FRED nor FMP could serve T10Y2Y/UNRATE -- the returned
            # snapshot is entirely fabricated placeholder data.
            suffix = f" FMP fallback error: {fmp_error}." if fmp_error else ""
            logger.warning(
                "Both FRED and the FMP macro fallback failed to serve "
                "T10Y2Y/UNRATE -- fetch_macro_raw is returning fabricated "
                "placeholder macro values (known CONSTRAINT #4 exception, "
                "last resort)." + suffix
            )
        fabricated = frozenset(fabricated_keys)
        self.last_macro_raw_fabricated_keys = fabricated
        return result, fabricated

    def fetch_macro_history(self) -> pd.DataFrame:
        """
        Fetches full historical daily/monthly series for VIXCLS, T10Y2Y,
        BAMLH0A0HYM2 (HY OAS credit spread), BAA10Y (Moody's Seasoned Baa
        Corporate Bond Spread -- a continuous-since-1986 fallback for
        BAMLH0A0HYM2, which only starts 2023-08-08), UNRATE (unemployment
        rate, the Sahm Rule input), T10YIE (10-Year Breakeven Inflation
        Rate, market-implied inflation expectations), BAMLC0A0CM (ICE BofA
        US Corporate Index Option-Adjusted Spread -- investment-grade credit
        OAS, DAILY cadence, same family as BAMLH0A0HYM2 above but for
        investment-grade rather than high-yield issuers), and FEDFUNDS
        (Federal Funds Effective Rate -- MONTHLY average; FRED dates each
        observation the 1st of the month it summarizes but does not publish
        it until early the following month, so downstream consumers must
        lag it accordingly rather than treating it as same-day-available)
        from FRED. Used by regime/hmm_regime.py to fit/refit on an
        expanding window (VIXCLS/T10Y2Y only, plus T10YIE when
        settings.HMM_INFLATION_FEATURE_ENABLED is set) AND by
        scripts/refresh_validations.py's macro_regime_pit adapter, which
        needs the first six series to reconstruct
        dto_models.MacroEconomicDTO's market_regime/killSwitch classification
        at any historical date -- a single current-snapshot value
        (fetch_macro_raw) cannot do this. This method is also the source
        HistoricalStore.get_macro() tops up from, which is in turn what
        api/pilots_api.py's get_transformer_forecast endpoint reads
        BAMLC0A0CM/FEDFUNDS from for the transformer volatility
        forecaster's macro conditioning -- previously only 2 of that
        endpoint's 4 requested series (VIXCLS, T10Y2Y) were genuinely
        available here, so BAMLC0A0CM/FEDFUNDS always came back as empty
        Series; this closes that gap. Returns an empty DataFrame (never
        fabricated placeholder rows) if FRED is unavailable or the fetch
        fails.

        Deliberately FRED-only, unlike fetch_macro_raw() above (2026-07 FMP
        integration): this method pulls the ENTIRE available history with no
        date-range parameter (self.fred.get_series('T10Y2Y') with no `limit`),
        which HistoricalStore.get_macro()'s top-up and
        regime/hmm_regime.py's expanding-window fit both depend on. FMP's
        /treasury-rates and /economic-indicators endpoints require an explicit
        from/to date range, so a clean FMP supplement here would need to pick
        an arbitrary backfill window rather than "everything FRED has" -- a
        real behavioral question this PR does not answer. Left as a documented
        follow-up rather than forced in; see data/fmp_macro.py for the
        two-series (T10Y2Y/UNRATE) snapshot-only supplement that IS wired, in
        fetch_macro_raw() above.
        """
        _EMPTY_COLUMNS = ['VIXCLS', 'T10Y2Y', 'BAMLH0A0HYM2', 'BAA10Y', 'UNRATE', 'T10YIE', 'BAMLC0A0CM', 'FEDFUNDS']

        if not self.fred:
            logger.warning("FRED API not initialized. Cannot fetch macro history.")
            return pd.DataFrame(columns=_EMPTY_COLUMNS)

        try:
            # Bounded (2026-08), per-call: fredapi has no timeout of its own --
            # see _bounded_fred_timeout's docstring. 8 series calls here, so
            # worst case is 8x settings.FRED_REQUEST_TIMEOUT_SECONDS, not a
            # whole-function budget.
            with _bounded_fred_timeout(settings.FRED_REQUEST_TIMEOUT_SECONDS):
                vix_series = self.fred.get_series('VIXCLS').rename('VIXCLS')
                yield_curve_series = self.fred.get_series('T10Y2Y').rename('T10Y2Y')
                credit_spread_series = self.fred.get_series('BAMLH0A0HYM2').rename('BAMLH0A0HYM2')
                baa_spread_series = self.fred.get_series('BAA10Y').rename('BAA10Y')
                unrate_series = self.fred.get_series('UNRATE').rename('UNRATE')
                t10yie_series = self.fred.get_series('T10YIE').rename('T10YIE')
                bamlc0a0cm_series = self.fred.get_series('BAMLC0A0CM').rename('BAMLC0A0CM')
                fedfunds_series = self.fred.get_series('FEDFUNDS').rename('FEDFUNDS')
            history_df = pd.concat(
                [
                    vix_series,
                    yield_curve_series,
                    credit_spread_series,
                    baa_spread_series,
                    unrate_series,
                    t10yie_series,
                    bamlc0a0cm_series,
                    fedfunds_series,
                ],
                axis=1,
            )
            history_df.index = pd.to_datetime(history_df.index)
            return history_df.sort_index()
        except Exception as e:
            logger.error(f"Error fetching macro history from FRED: {e}")
            return pd.DataFrame(columns=_EMPTY_COLUMNS)

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
        ``{symbol: DataFrame}`` shape with Open/High/Low/Close/Volume columns
        and a tz-naive ``DatetimeIndex``) on any ``HistoricalStore``/provider
        construction or import failure, or entirely when
        ``settings.HISTORICAL_STORE_ENABLED`` is False -- byte-identical
        output either way. Never modifies ``fetch_technical_raw()`` itself.

        Delegates the actual per-symbol concurrency to
        ``HistoricalStore.get_bars_bulk()``, which already isolates each
        symbol's failure (dead-letter resilience) internally -- so this
        outer ``try/except`` now only ever triggers on a genuinely
        catastrophic failure (e.g. the whole DB unavailable), never on one
        bad symbol.
        """
        if not getattr(settings, "HISTORICAL_STORE_ENABLED", True):
            return self.fetch_technical_raw(tickers)

        try:
            from data.historical_store import HistoricalStore
            from data.market_data import get_provider

            _store = HistoricalStore()
            _provider = get_provider()

            lookback_days = int(getattr(settings, "BARS_BACKFILL_DAYS", 504))
            bulk_map = _store.get_bars_bulk(tickers, lookback_days=lookback_days, provider=_provider)
            return {sym: df for sym, df in bulk_map.items() if df is not None and not df.empty}
        except Exception as e:
            logger.error(f"Failed to fetch bulk cached technical series: {e}")
            return self.fetch_technical_raw(tickers)

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
        # Mirrors DataEngine's interface (getattr(de, "last_macro_raw_fabricated_keys",
        # frozenset()) is used defensively by callers) -- always empty since
        # preset_macro is an explicit test fixture, never a live-data outage
        # fallback, so it's never "fabricated" in the CONSTRAINT #4 sense.
        self.last_macro_raw_fabricated_keys: FrozenSet[str] = frozenset()

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

