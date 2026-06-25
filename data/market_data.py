"""
data/market_data.py — Swappable Market-Data Layer
==================================================
Provides live quotes, intraday/daily bars, and fundamentals via a provider
abstraction that hides the concrete source (Alpaca vs yfinance) from all
signal, indicator, and forecasting code.

Provider selection (evaluated at ``CompositeProvider`` construction time):
  1. ``MARKET_DATA_PROVIDER`` env-var set to "alpaca" → ``AlpacaProvider``
  2. ``MARKET_DATA_PROVIDER`` env-var set to "yfinance" → ``YFinanceProvider``
  3. Env-var absent, ``ALPACA_API_KEY`` + ``ALPACA_SECRET_KEY`` present → Alpaca
  4. Otherwise → ``YFinanceProvider`` (zero config, ~15-min delayed, free)

Fundamentals are always sourced from ``FinnhubProvider`` when
``FINNHUB_API_KEY`` is present; otherwise they degrade to an empty dict with a
logged warning (never a crash).

In-process quote cache:
  Live quotes (get_latest_quote) are cached in a plain dict keyed by symbol for
  ``MARKET_DATA_QUOTE_TTL_SECONDS`` (default 30 s).  The cache is in-process
  only — never written to disk — because quotes are intraday and must not
  survive across runs.

Bar shape contract (matches existing pipeline):
  The DataFrame returned by get_intraday_bars / get_daily_bars MUST have
  columns ``Open, High, Low, Close, Volume`` with a timezone-naive
  DatetimeIndex, matching what DataEngine.fetch_technical_raw() already
  delivers to processing_engine, forecasting_engine, and strategy_engine.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------

class MarketDataError(Exception):
    """Raised by any provider when a network call or parse fails unrecoverably.

    The orchestrator catches this per-symbol and dead-letters the failure
    without aborting the full run (resilience constraint).
    """


# ---------------------------------------------------------------------------
# Quote dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quote:
    """Immutable snapshot of the best bid/ask and last trade for one symbol.

    Attributes
    ----------
    symbol:
        Normalised, uppercase ticker (e.g. "AAPL").
    price:
        Last trade or mid-price fallback (USD).
    bid:
        Best bid price; ``float('nan')`` when unavailable.
    ask:
        Best ask price; ``float('nan')`` when unavailable.
    timestamp:
        UTC-aware datetime of the quote.
    is_stale:
        True when the quote is delayed (yfinance always), market is closed, or
        the timestamp is older than the configured TTL threshold.
    source:
        Provider name string for dashboard/Sheet attribution ("alpaca",
        "yfinance").
    """

    symbol: str
    price: float
    bid: float
    ask: float
    timestamp: datetime
    is_stale: bool
    source: str


# ---------------------------------------------------------------------------
# Abstract provider interface
# ---------------------------------------------------------------------------

class MarketDataProvider(ABC):
    """Abstract contract for all market-data backends.

    Callers import this type for type annotations; they receive a
    ``CompositeProvider`` instance at runtime and never need to know the
    concrete backend.
    """

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> Quote:
        """Return the most recent best bid/ask/last for ``symbol``.

        Raises
        ------
        MarketDataError
            On unrecoverable network or parse failure.
        """

    @abstractmethod
    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        """Return daily OHLCV bars for the last ``lookback_days`` trading days.

        The returned DataFrame must have columns
        ``['Open', 'High', 'Low', 'Close', 'Volume']`` and a timezone-naive
        ``DatetimeIndex`` sorted ascending — the same shape that
        ``DataEngine.fetch_technical_raw()`` delivers to the processing engine.

        Raises
        ------
        MarketDataError
            On unrecoverable network or parse failure.
        """

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Return a dict of fundamental metrics for ``symbol``.

        Keys mirror the yfinance ``.info`` dict used by
        ``FundamentalDataDTO.from_raw_dict()`` so the downstream DTO layer is
        unchanged.  Returns an empty dict (never raises) when the fundamentals
        source is misconfigured or unavailable.
        """


# ---------------------------------------------------------------------------
# Alpaca provider
# ---------------------------------------------------------------------------

class AlpacaProvider(MarketDataProvider):
    """Real-time quote/bar provider backed by the free Alpaca IEX feed.

    Requires ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY`` in the environment.
    Stale detection: quotes older than ``stale_threshold_seconds`` during
    market hours are marked ``is_stale=True``.

    Parameters
    ----------
    api_key:
        Alpaca API key (read from os.environ by CompositeProvider).
    secret_key:
        Alpaca secret key.
    stale_threshold_seconds:
        Age (seconds) beyond which a quote is considered stale.  Default 60.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        stale_threshold_seconds: int = 60,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._stale_threshold = stale_threshold_seconds
        self._client = self._build_client()

    def _build_client(self):  # type: ignore[return]
        """Lazily import alpaca-py and construct the data client."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
            return StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        except ImportError as exc:
            raise ImportError(
                "alpaca-py is required for AlpacaProvider.  "
                "Install it with: pip install alpaca-py"
            ) from exc

    def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch the best bid/ask via Alpaca's IEX real-time feed."""
        try:
            from alpaca.data.requests import StockLatestQuoteRequest  # type: ignore

            req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed="iex")
            resp = self._client.get_stock_latest_quote(req)
            q = resp[symbol]

            ts_utc: datetime = (
                q.timestamp.astimezone(timezone.utc)
                if q.timestamp.tzinfo is not None
                else q.timestamp.replace(tzinfo=timezone.utc)
            )
            age_seconds = (datetime.now(timezone.utc) - ts_utc).total_seconds()
            is_stale = age_seconds > self._stale_threshold

            bid = float(q.bid_price) if q.bid_price else float("nan")
            ask = float(q.ask_price) if q.ask_price else float("nan")
            price = (bid + ask) / 2 if (not _isnan(bid) and not _isnan(ask)) else (bid if not _isnan(bid) else ask)

            return Quote(
                symbol=symbol.upper(),
                price=price,
                bid=bid,
                ask=ask,
                timestamp=ts_utc,
                is_stale=is_stale,
                source="alpaca",
            )
        except Exception as exc:
            logger.error("AlpacaProvider.get_latest_quote(%s) failed: %s", symbol, exc)
            raise MarketDataError(f"Alpaca quote fetch failed for {symbol}: {exc}") from exc

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        """Fetch daily OHLCV bars via Alpaca IEX for the last ``lookback_days`` days."""
        try:
            from alpaca.data.requests import StockBarsRequest  # type: ignore
            from alpaca.data.timeframe import TimeFrame  # type: ignore

            start = datetime.now(timezone.utc) - timedelta(days=lookback_days + 10)
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                feed="iex",
            )
            resp = self._client.get_stock_bars(req)
            bars_df = resp.df

            if bars_df.empty:
                raise MarketDataError(f"Alpaca returned empty bars for {symbol}")

            # resp.df has a MultiIndex (symbol, timestamp) when multiple symbols
            # are requested; flatten if needed.
            if isinstance(bars_df.index, pd.MultiIndex):
                bars_df = bars_df.xs(symbol, level="symbol")

            # Alpaca column names: open, high, low, close, volume → capitalise
            bars_df = bars_df.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            bars_df = bars_df[["Open", "High", "Low", "Close", "Volume"]].copy()

            # Strip tz → timezone-naive index to match existing pipeline
            if bars_df.index.tz is not None:
                bars_df.index = bars_df.index.tz_localize(None)
            bars_df.index = pd.to_datetime(bars_df.index).normalize()
            bars_df.sort_index(inplace=True)

            return bars_df.tail(lookback_days)

        except MarketDataError:
            raise
        except Exception as exc:
            logger.error("AlpacaProvider.get_intraday_bars(%s) failed: %s", symbol, exc)
            raise MarketDataError(f"Alpaca bars fetch failed for {symbol}: {exc}") from exc

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Alpaca does not provide fundamentals; return empty (Finnhub handles this)."""
        return {}


# ---------------------------------------------------------------------------
# yfinance provider
# ---------------------------------------------------------------------------

class YFinanceProvider(MarketDataProvider):
    """Delayed quote/bar provider backed by yfinance (unofficial, ~15-min lag).

    Requires NO API keys.  ``is_stale`` is always ``True`` by design because
    yfinance data is delayed — nothing downstream should treat it as real-time.

    yfinance can return empty DataFrames or raise on rate-limit; those errors
    are caught, logged with the symbol, and re-raised as ``MarketDataError``.
    """

    SOURCE = "yfinance"

    def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch last price via ``Ticker.fast_info`` (avoids the slow .info round-trip)."""
        try:
            import yfinance as yf  # type: ignore

            t = yf.Ticker(symbol)
            fi = t.fast_info

            price = float(fi.get("last_price") or fi.get("previous_close") or float("nan"))
            bid = float(fi.get("bid") or float("nan"))
            ask = float(fi.get("ask") or float("nan"))

            # fast_info doesn't always expose a precise intraday timestamp
            ts = datetime.now(timezone.utc)

            return Quote(
                symbol=symbol.upper(),
                price=price,
                bid=bid,
                ask=ask,
                timestamp=ts,
                is_stale=True,   # yfinance is always considered delayed
                source=self.SOURCE,
            )
        except Exception as exc:
            logger.error("YFinanceProvider.get_latest_quote(%s) failed: %s", symbol, exc)
            raise MarketDataError(f"yfinance quote fetch failed for {symbol}: {exc}") from exc

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        """Fetch daily OHLCV bars from yfinance history."""
        try:
            import yfinance as yf  # type: ignore

            # Map lookback to yfinance period strings to avoid overfetching
            if lookback_days <= 30:
                period = "1mo"
            elif lookback_days <= 90:
                period = "3mo"
            elif lookback_days <= 180:
                period = "6mo"
            elif lookback_days <= 365:
                period = "1y"
            elif lookback_days <= 730:
                period = "2y"
            else:
                period = "5y"

            df = yf.Ticker(symbol).history(period=period, auto_adjust=True)

            if df is None or df.empty:
                raise MarketDataError(f"yfinance returned empty bars for {symbol}")

            # yfinance history() already returns Open/High/Low/Close/Volume capitalised
            # but may include Dividends / Stock Splits — keep only OHLCV
            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[keep].copy()
            if "Volume" not in df.columns:
                df["Volume"] = 0

            # Strip timezone from index → naive, date-only → matches existing pipeline
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.index = pd.to_datetime(df.index).normalize()
            df.sort_index(inplace=True)

            return df.tail(lookback_days)

        except MarketDataError:
            raise
        except Exception as exc:
            logger.error("YFinanceProvider.get_intraday_bars(%s) failed: %s", symbol, exc)
            raise MarketDataError(f"yfinance bars fetch failed for {symbol}: {exc}") from exc

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Fall back to yfinance .info for fundamentals when Finnhub is unavailable.

        This is the secondary fundamentals path; ``FinnhubProvider`` is preferred.
        Returns an empty dict on failure rather than raising.
        """
        try:
            import yfinance as yf  # type: ignore

            info = yf.Ticker(symbol).info or {}
            return dict(info)
        except Exception as exc:
            logger.warning(
                "YFinanceProvider.get_fundamentals(%s) failed: %s — returning empty dict",
                symbol, exc,
            )
            return {}


# ---------------------------------------------------------------------------
# Finnhub provider (fundamentals only)
# ---------------------------------------------------------------------------

class FinnhubProvider:
    """Fundamentals-only provider backed by the Finnhub free tier.

    Uses ``company_basic_financials`` for balance-sheet metrics, shaped to
    match the yfinance ``.info`` dict keys consumed by
    ``FundamentalDataDTO.from_raw_dict()``.

    Degrades gracefully (returns an empty dict + logged warning) when
    ``FINNHUB_API_KEY`` is absent.

    Parameters
    ----------
    api_key:
        Finnhub API key.  None → degrade-mode (empty dict responses).
    """

    # Mapping from Finnhub metric names to yfinance .info key names so that
    # FundamentalDataDTO.from_raw_dict() doesn't need to know the source.
    _METRIC_MAP: Dict[str, str] = {
        "peBasicExclExtraTTM": "trailingPE",
        "pbQuarterly": "priceToBook",
        "bookValuePerShareQuarterly": "bookValue",
        "epsBasicExclExtraItemsTTM": "trailingEps",
        "dividendYieldIndicatedAnnual": "dividendYield",
        "payoutRatioTTM": "payoutRatio",
        "marketCapitalization": "marketCap",
        "betaWeekly": "beta",
        "roe5Y": "returnOnEquity",
        "roeTTM": "returnOnEquity",
        "revenueGrowth3Y": "revenueGrowth",
        "debtToEquityQuarterly": "debtToEquity",
        "grossMarginTTM": "grossMargins",
        "operatingMarginTTM": "operatingMargins",
        "heldPercentInstitutions": "heldPercentInstitutions",
        "currentRatioQuarterly": "currentRatio",
    }

    def __init__(self, api_key: Optional[str]) -> None:
        self._api_key = api_key
        self._client: Optional[Any] = None
        if api_key:
            self._client = self._build_client(api_key)

    def _build_client(self, api_key: str) -> Optional[Any]:
        """Lazily import finnhub-python and return a client instance."""
        try:
            import finnhub  # type: ignore
            return finnhub.Client(api_key=api_key)
        except ImportError:
            logger.warning(
                "FinnhubProvider: finnhub-python not installed — "
                "pip install finnhub-python.  Fundamentals will be empty."
            )
            return None

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Return fundamentals shaped as a yfinance .info dict.

        Returns an empty dict when the key is absent or the call fails.
        """
        if self._client is None:
            logger.warning(
                "FinnhubProvider: FINNHUB_API_KEY not configured — "
                "returning empty fundamentals for %s.  "
                "Set FINNHUB_API_KEY in .env for fundamental data.",
                symbol,
            )
            return {}

        try:
            resp = self._client.company_basic_financials(symbol, "all")
            metrics: Dict[str, Any] = resp.get("metric", {}) or {}

            # Shape Finnhub metrics to match yfinance .info key names
            info: Dict[str, Any] = {}
            for fh_key, yf_key in self._METRIC_MAP.items():
                val = metrics.get(fh_key)
                if val is not None:
                    # Finnhub returns dividendYield as percent (e.g. 0.52 = 0.52%)
                    # yfinance returns it as a fraction (0.0052) — normalise
                    if yf_key == "dividendYield" and isinstance(val, (int, float)):
                        val = val / 100.0
                    info[yf_key] = val

            # Fetch quote for currentPrice if not already present
            if "currentPrice" not in info:
                try:
                    q_resp = self._client.quote(symbol)
                    if q_resp and q_resp.get("c"):
                        info["currentPrice"] = float(q_resp["c"])
                except Exception:
                    pass

            # Pull company profile for name/sector
            try:
                profile = self._client.company_profile2(symbol=symbol) or {}
                if profile.get("name"):
                    info["shortName"] = profile["name"]
                if profile.get("finnhubIndustry"):
                    info["sector"] = profile["finnhubIndustry"]
                if profile.get("shareOutstanding"):
                    shares = float(profile["shareOutstanding"]) * 1e6
                    if "marketCap" not in info and "currentPrice" in info:
                        info["marketCap"] = shares * info["currentPrice"]
            except Exception:
                pass

            return info

        except Exception as exc:
            logger.warning(
                "FinnhubProvider.get_fundamentals(%s) failed: %s — returning empty dict",
                symbol, exc,
            )
            return {}


# ---------------------------------------------------------------------------
# In-process TTL quote cache
# ---------------------------------------------------------------------------

class _QuoteCache:
    """Thread-unsafe in-process quote cache with a per-symbol TTL.

    This is intentionally simple — no locking, no persistence.  Quotes are
    intraday artefacts; a TTL of 30 s is sufficient to deduplicate back-to-back
    calls within a single refresh cycle without staling across runs.

    Parameters
    ----------
    ttl_seconds:
        Seconds after which a cached quote is considered expired and must be
        re-fetched.
    """

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl = ttl_seconds
        self._store: Dict[str, tuple[Quote, float]] = {}

    def get(self, symbol: str) -> Optional[Quote]:
        """Return the cached Quote or None if absent / expired."""
        entry = self._store.get(symbol)
        if entry is None:
            return None
        quote, cached_at = entry
        if time.monotonic() - cached_at > self._ttl:
            del self._store[symbol]
            return None
        return quote

    def put(self, quote: Quote) -> None:
        """Store a Quote with the current monotonic timestamp."""
        self._store[quote.symbol] = (quote, time.monotonic())

    def invalidate(self, symbol: str) -> None:
        """Remove a symbol's entry (e.g. after a failed trade)."""
        self._store.pop(symbol, None)

    def clear(self) -> None:
        """Wipe all cached quotes (e.g. on session restart)."""
        self._store.clear()


# ---------------------------------------------------------------------------
# Composite provider — the main entrypoint for the rest of the app
# ---------------------------------------------------------------------------

class CompositeProvider(MarketDataProvider):
    """Auto-selecting composite that routes quotes/bars to one backend and
    fundamentals to Finnhub (with yfinance fallback).

    Provider selection order
    ~~~~~~~~~~~~~~~~~~~~~~~~
    1. ``MARKET_DATA_PROVIDER=alpaca`` → ``AlpacaProvider``
    2. ``MARKET_DATA_PROVIDER=yfinance`` → ``YFinanceProvider``
    3. Env-var absent, ``ALPACA_API_KEY`` + ``ALPACA_SECRET_KEY`` set → Alpaca
    4. Otherwise → ``YFinanceProvider``

    Fundamentals always come from ``FinnhubProvider`` when
    ``FINNHUB_API_KEY`` is set; ``YFinanceProvider.get_fundamentals()`` is the
    fallback when Finnhub is not configured.

    Parameters
    ----------
    quote_ttl_seconds:
        TTL for the in-process quote cache.  Defaults to
        ``MARKET_DATA_QUOTE_TTL_SECONDS`` env-var (int), then 30 s.
    """

    def __init__(self, quote_ttl_seconds: Optional[int] = None) -> None:
        ttl = quote_ttl_seconds or int(
            os.environ.get("MARKET_DATA_QUOTE_TTL_SECONDS", "30")
        )
        self._cache = _QuoteCache(ttl_seconds=ttl)
        self._quote_provider: MarketDataProvider = self._select_quote_provider()
        self._fundamentals_provider: FinnhubProvider = FinnhubProvider(
            api_key=os.environ.get("FINNHUB_API_KEY")
        )
        # Log startup banner once
        self._log_startup_banner()

    # ------------------------------------------------------------------
    # Provider selection
    # ------------------------------------------------------------------

    def _select_quote_provider(self) -> MarketDataProvider:
        explicit = os.environ.get("MARKET_DATA_PROVIDER", "").strip().lower()
        alpaca_key = os.environ.get("ALPACA_API_KEY", "").strip()
        alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()

        if explicit == "alpaca" or (not explicit and alpaca_key and alpaca_secret):
            if not alpaca_key or not alpaca_secret:
                raise RuntimeError(
                    "MARKET_DATA_PROVIDER=alpaca but ALPACA_API_KEY / "
                    "ALPACA_SECRET_KEY are not set. Add them to .env."
                )
            return AlpacaProvider(api_key=alpaca_key, secret_key=alpaca_secret)

        if explicit == "yfinance" or not explicit:
            return YFinanceProvider()

        raise RuntimeError(
            f"Unknown MARKET_DATA_PROVIDER value: {explicit!r}.  "
            "Valid values: 'alpaca', 'yfinance'."
        )

    def _log_startup_banner(self) -> None:
        provider_name = type(self._quote_provider).__name__
        is_realtime = isinstance(self._quote_provider, AlpacaProvider)
        latency_note = "real-time (IEX)" if is_realtime else "delayed (~15 min, unofficial)"
        finnhub_note = (
            "Finnhub (FINNHUB_API_KEY configured)"
            if os.environ.get("FINNHUB_API_KEY")
            else "yfinance fallback (FINNHUB_API_KEY not set)"
        )
        logger.info(
            "MarketData: quotes/bars via %s [%s]; fundamentals via %s",
            provider_name, latency_note, finnhub_note,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_latest_quote(self, symbol: str) -> Quote:
        """Return a cached or freshly-fetched Quote for ``symbol``.

        The in-process TTL cache (default 30 s) prevents redundant network
        calls within a single refresh cycle.  Raises ``MarketDataError`` on
        provider failure.
        """
        sym = symbol.upper()
        cached = self._cache.get(sym)
        if cached is not None:
            return cached

        quote = self._quote_provider.get_latest_quote(sym)
        self._cache.put(quote)
        return quote

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        """Return OHLCV bars (daily resolution) for the last ``lookback_days`` days.

        The shape is identical to ``DataEngine.fetch_technical_raw()`` so all
        downstream processing_engine / forecasting_engine code runs unchanged.

        Raises ``MarketDataError`` on provider failure.
        """
        return self._quote_provider.get_intraday_bars(
            symbol=symbol.upper(), lookback_days=lookback_days
        )

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Return fundamental metrics shaped as a yfinance .info dict.

        Source priority: Finnhub (when FINNHUB_API_KEY set) → yfinance .info
        fallback.  Always returns a dict, never raises.
        """
        sym = symbol.upper()

        # Try Finnhub first
        if os.environ.get("FINNHUB_API_KEY"):
            fund = self._fundamentals_provider.get_fundamentals(sym)
            if fund:
                return fund

        # Fallback to yfinance .info
        return YFinanceProvider().get_fundamentals(sym)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def is_realtime(self) -> bool:
        """True when the active quote provider delivers real-time data."""
        return isinstance(self._quote_provider, AlpacaProvider)

    @property
    def quote_source(self) -> str:
        """Provider name string, e.g. "alpaca" or "yfinance"."""
        return (
            "alpaca"
            if isinstance(self._quote_provider, AlpacaProvider)
            else "yfinance"
        )

    def invalidate_quote(self, symbol: str) -> None:
        """Evict a symbol's quote from the TTL cache (e.g. after a fill)."""
        self._cache.invalidate(symbol.upper())

    def clear_quote_cache(self) -> None:
        """Wipe the entire in-process quote cache (e.g. on session restart)."""
        self._cache.clear()


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _isnan(v: float) -> bool:
    """Return True for float('nan') without importing math."""
    return v != v


# ---------------------------------------------------------------------------
# Module-level singleton — lazily initialised on first import access
# ---------------------------------------------------------------------------

_default_provider: Optional[CompositeProvider] = None


def get_provider() -> CompositeProvider:
    """Return the module-level ``CompositeProvider`` singleton.

    Auto-selects Alpaca vs yfinance based on environment variables.
    Constructing on first call so import-time side effects are avoided
    (tests can set env vars before calling this).
    """
    global _default_provider
    if _default_provider is None:
        _default_provider = CompositeProvider()
    return _default_provider


def reset_provider() -> None:
    """Force-reset the singleton (useful in tests to re-evaluate env vars)."""
    global _default_provider
    _default_provider = None
