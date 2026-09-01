"""
data/market_data.py — Swappable Market-Data Layer
==================================================
Provides live quotes, intraday/daily bars, and fundamentals via a provider
abstraction that hides the concrete source (Alpaca vs yfinance) from all
signal, indicator, and forecasting code.

Provider selection (evaluated at ``CompositeProvider`` construction time):
  1. ``MARKET_DATA_PROVIDER`` env-var set to "fmp" → ``FMPProvider`` (never
     auto-elected by ``FMP_API_KEY`` alone — see ``FMPProvider``'s docstring)
  2. ``MARKET_DATA_PROVIDER`` env-var set to "alpaca" → ``AlpacaProvider``
  3. ``MARKET_DATA_PROVIDER`` env-var set to "yfinance" → ``YFinanceProvider``
  4. Env-var absent, ``ALPACA_API_KEY`` + ``ALPACA_SECRET_KEY`` present → Alpaca
  5. Otherwise → ``YFinanceProvider`` (zero config, ~15-min delayed, free)

Fundamentals are Yahoo statement-derived (``YahooFundamentalsProvider``,
primary) with a raw yfinance ``.info`` fallback, or FMP-sourced when
``FUNDAMENTALS_SOURCE=fmp`` — see ``CompositeProvider.get_fundamentals``.

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
import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from settings import settings

# WebSocketStreamer integration — imported with a guard so the module
# degrades gracefully when websockets is not installed.
try:
    from data.websocket_streamer import _STREAMER as _WS_STREAMER
    _WS_AVAILABLE = True
except Exception:
    _WS_STREAMER = None  # type: ignore[assignment]
    _WS_AVAILABLE = False

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

    Class attributes
    ~~~~~~~~~~~~~~~~
    ``SOURCE``:
        Short provenance string surfaced to the dashboard / Google Sheet and
        stamped on every ``Quote.source`` this provider emits (e.g.
        ``"alpaca"``, ``"yfinance"``). ``CompositeProvider.quote_source`` reads
        it off the *selected* provider rather than hardcoding a name, so adding
        a backend can never silently mislabel its quotes as another provider's.
    ``IS_REALTIME``:
        ``False`` for any delayed or unofficial feed (the safe default —
        nothing downstream should treat a delayed price as real-time). Only a
        genuine real-time feed sets this ``True``.

    Both are read via ``getattr(..., default)`` at the call sites so a
    duck-typed provider (e.g. ``YahooFundamentalsProvider``, which is not an
    ABC subclass) never breaks the accessor.
    """

    SOURCE: str = "unknown"
    IS_REALTIME: bool = False

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> Quote:
        """Return the most recent best bid/ask/last for ``symbol``.

        Raises
        ------
        MarketDataError
            On unrecoverable network or parse failure.
        """

    @abstractmethod
    def get_intraday_bars(
        self, symbol: str, lookback_days: int = 252, interval: str = "1d"
    ) -> pd.DataFrame:
        """Return OHLCV bars for the last ``lookback_days`` trading days.

        ``interval`` selects bar resolution: ``"1d"`` (default, unchanged
        behavior) or ``"1h"`` (opt-in hourly bars, gated behind
        ``settings.EXCURSION_INTRADAY_ENABLED`` at the call sites that use
        it — see ``evaluation_engine.calculate_edge_ratio``). Concrete
        providers may raise ``MarketDataError`` for an unsupported interval;
        callers must be prepared to degrade to ``"1d"``.

        The returned DataFrame must have columns
        ``['Open', 'High', 'Low', 'Close', 'Volume']`` and a timezone-naive
        ``DatetimeIndex`` sorted ascending — the same shape that
        ``DataEngine.fetch_technical_raw()`` delivers to the processing engine
        (daily-resolution callers are unaffected by the new parameter).

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

    def get_quotes_batch(self, symbols: List[str]) -> Dict[str, Quote]:
        """Return a ``{symbol: Quote}`` map for every symbol that resolved
        successfully. A symbol that fails (bad ticker, transient error) is
        simply ABSENT from the result -- never raises, matching the
        per-symbol dead-lettering every caller of this method already did by
        hand before this method existed (F6, docs/module_efficiency_redundancy_audit.md).

        Default implementation: loops over ``get_latest_quote`` one symbol
        at a time -- the exact N-HTTP-calls pattern this method exists to
        let a batching-capable provider override. Concrete providers with a
        real batch endpoint (see ``FMPProvider.get_quotes_batch``) SHOULD
        override this for a genuine efficiency win; every other provider
        (Alpaca, yfinance) inherits this default and is unaffected --
        deliberately NOT abstract, so no existing provider subclass breaks
        by not implementing it.
        """
        out: Dict[str, Quote] = {}
        for sym in symbols:
            try:
                out[sym.upper()] = self.get_latest_quote(sym)
            except Exception:  # noqa: BLE001 -- dead-letter per symbol, CONSTRAINT #6
                continue
        return out


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
        Alpaca API key (read from settings.settings by CompositeProvider).
    secret_key:
        Alpaca secret key.
    stale_threshold_seconds:
        Age (seconds) beyond which a quote is considered stale.  Default 60.
    """

    SOURCE = "alpaca"
    IS_REALTIME = True

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
            client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
            # 2026-08 fix: StockHistoricalDataClient subclasses the same
            # alpaca-py RESTClient as execution/alpaca_broker.py's
            # TradingClient, which exposes no timeout of its own (confirmed
            # against the installed source) -- get_latest_quote/
            # get_intraday_bars below used to be able to block forever on a
            # stalled connection. See data/alpaca_http.py's module docstring.
            from data.alpaca_http import mount_timeout_adapter
            mount_timeout_adapter(client._session, settings.ALPACA_REQUEST_TIMEOUT_SECONDS)
            return client
        except ImportError as exc:
            raise ImportError(
                "alpaca-py is required for AlpacaProvider.  "
                "Install it with: pip install alpaca-py"
            ) from exc

    def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch the best bid/ask via Alpaca's IEX real-time feed.

        WS-first: checks the in-process ``WebSocketStreamer`` cache (TTL 2 s)
        before making a REST round-trip. Falls back transparently to REST on
        cache miss, keeping latency low for actively-streamed symbols without
        any code changes in callers.
        """
        sym_upper = symbol.upper()

        # --- WS fast path ---------------------------------------------------
        if _WS_AVAILABLE and _WS_STREAMER is not None:
            # Subscribe the symbol on first access so the stream picks it up
            if sym_upper not in _WS_STREAMER._subscribed:
                _WS_STREAMER.subscribe([sym_upper])

            ws_tick = _WS_STREAMER.get_quote(sym_upper)
            if ws_tick is not None:
                bid = float(ws_tick.get("bp", float("nan")) or float("nan"))
                ask = float(ws_tick.get("ap", float("nan")) or float("nan"))
                price = (
                    (bid + ask) / 2
                    if (not _isnan(bid) and not _isnan(ask))
                    else (bid if not _isnan(bid) else ask)
                )
                ts = datetime.now(timezone.utc)
                return Quote(
                    symbol=sym_upper,
                    price=price,
                    bid=bid,
                    ask=ask,
                    timestamp=ts,
                    is_stale=False,
                    source="alpaca-ws",
                )
        # --- REST fallback --------------------------------------------------
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
                symbol=sym_upper,
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

    def get_intraday_bars(
        self, symbol: str, lookback_days: int = 252, interval: str = "1d"
    ) -> pd.DataFrame:
        """Fetch OHLCV bars via Alpaca IEX for the last ``lookback_days`` days.

        ``interval="1d"`` (default) is unchanged daily-bar behavior.
        ``interval="1h"`` fetches hourly bars instead — the index stays a
        full timestamp (not normalized to midnight) so intraday resolution
        is preserved; any other value raises ``MarketDataError``.
        """
        try:
            from alpaca.data.requests import StockBarsRequest  # type: ignore
            from alpaca.data.timeframe import TimeFrame  # type: ignore

            if interval == "1d":
                timeframe = TimeFrame.Day
            elif interval == "1h":
                timeframe = TimeFrame.Hour
            else:
                raise MarketDataError(
                    f"AlpacaProvider.get_intraday_bars: unsupported interval {interval!r} "
                    "(supported: '1d', '1h')"
                )

            start = datetime.now(timezone.utc) - timedelta(days=lookback_days + 10)
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
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

            # Strip tz → timezone-naive index to match existing pipeline. Daily
            # bars normalize to midnight (unchanged); hourly bars keep their
            # real intraday timestamp so same-day excursion is resolvable.
            if bars_df.index.tz is not None:
                bars_df.index = bars_df.index.tz_localize(None)
            bars_df.index = pd.to_datetime(bars_df.index)
            if interval == "1d":
                bars_df.index = bars_df.index.normalize()
            bars_df.sort_index(inplace=True)

            return bars_df.tail(lookback_days) if interval == "1d" else bars_df

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
    # Delayed by design (~15 min, unofficial) — every Quote it emits also
    # carries ``is_stale=True``.
    IS_REALTIME = False

    def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch last price via ``Ticker.fast_info`` (avoids the slow .info round-trip)."""
        try:
            import yfinance as yf  # type: ignore

            t = yf.Ticker(symbol)
            fi = t.fast_info

            # FastInfo's dict-style .get() only recognizes its camelCase keys
            # (e.g. "lastPrice") -- the snake_case names below are exposed
            # only as attributes. getattr(..., default) mirrors .get()'s
            # None-on-missing semantics without the camelCase trap, and
            # tolerates keys (bid/ask) FastInfo doesn't expose at all.
            price = float(
                getattr(fi, "last_price", None)
                or getattr(fi, "previous_close", None)
                or float("nan")
            )
            bid = float(getattr(fi, "bid", None) or float("nan"))
            ask = float(getattr(fi, "ask", None) or float("nan"))

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

    def get_intraday_bars(
        self, symbol: str, lookback_days: int = 252, interval: str = "1d"
    ) -> pd.DataFrame:
        """Fetch OHLCV bars from yfinance history.

        ``interval="1d"`` (default) is unchanged daily-bar behavior.
        ``interval="1h"`` fetches hourly bars instead — yfinance caps hourly
        history at 730 days, and the index keeps its real intraday
        timestamp rather than being normalized to midnight.
        """
        try:
            import yfinance as yf  # type: ignore

            if interval not in ("1d", "1h"):
                raise MarketDataError(
                    f"YFinanceProvider.get_intraday_bars: unsupported interval "
                    f"{interval!r} (supported: '1d', '1h')"
                )

            if interval == "1h":
                # yfinance rejects hourly requests older than ~730 days.
                period = f"{min(lookback_days, 729)}d"
            # Map lookback to yfinance period strings to avoid overfetching
            elif lookback_days <= 20:
                period = "1mo"
            elif lookback_days <= 60:
                period = "3mo"
            elif lookback_days <= 120:
                period = "6mo"
            elif lookback_days <= 240:
                period = "1y"
            elif lookback_days <= 500:
                period = "2y"
            else:
                period = "5y"

            df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)

            if df is None or df.empty:
                raise MarketDataError(f"yfinance returned empty bars for {symbol}")

            # yfinance history() already returns Open/High/Low/Close/Volume capitalised
            # but may include Dividends / Stock Splits — keep only OHLCV
            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[keep].copy()
            if "Volume" not in df.columns:
                df["Volume"] = 0

            # Strip timezone from index → naive. Daily bars normalize to
            # midnight (unchanged); hourly bars keep their real intraday
            # timestamp so same-day excursion is resolvable.
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.index = pd.to_datetime(df.index)
            if interval == "1d":
                df.index = df.index.normalize()
            df.sort_index(inplace=True)

            return df.tail(lookback_days) if interval == "1d" else df

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
            from dto_models import normalize_yfinance_dividend_yield

            info = yf.Ticker(symbol).info or {}
            # yfinance returns dividendYield as a PERCENT; normalise to the
            # fraction the platform (and the Finnhub path) use. See the helper.
            return normalize_yfinance_dividend_yield(dict(info))
        except Exception as exc:
            logger.warning(
                "YFinanceProvider.get_fundamentals(%s) failed: %s — returning empty dict",
                symbol, exc,
            )
            return {}


# ---------------------------------------------------------------------------
# Yahoo-derived statement-computed fundamentals provider
# ---------------------------------------------------------------------------

class YahooFundamentalsProvider:
    """Statement-derived fundamentals from free Yahoo Finance data.

    Fetches yfinance financial-statement frames + a cached SPY daily-return
    series (for beta) and delegates ALL math to
    ``data.yahoo_fundamentals.compute_fundamentals`` (pure, offline-testable).
    Replaces FinnhubProvider as the primary fundamentals source. Degrades to an
    empty dict (never raises) on any failure — CONSTRAINT #6 dead-letter.

    The math module is kept strictly pure: this class is an I/O shell only. It
    performs no financial computation itself — every yfinance attribute is
    pulled inside its own try/except so one flaky frame (``.info`` in
    particular) never aborts the rest of the fetch.
    """

    SOURCE = "yahoo_computed"

    # Refetch SPY market-return series at most every ~6 h.
    _SPY_CACHE_TTL_SECONDS = 6 * 3600

    def __init__(self) -> None:
        # SPY market-return cache (shared across symbols within a run so we
        # don't refetch the benchmark once per ticker).
        self._spy_returns_cache: Optional[pd.Series] = None
        self._spy_cached_at: float = 0.0

    @property
    def source_name(self) -> str:
        return self.SOURCE

    @staticmethod
    def _beta_period() -> str:
        """Map ``BETA_LOOKBACK_DAYS`` (default 504 = ~2y) to a yfinance period."""
        try:
            days = int(settings.BETA_LOOKBACK_DAYS)
        except Exception:
            days = 504
        if days <= 30:
            return "1mo"
        if days <= 90:
            return "3mo"
        if days <= 180:
            return "6mo"
        if days <= 252:
            return "1y"
        if days <= 504:
            return "2y"
        if days <= 1260:
            return "5y"
        return "10y"

    @staticmethod
    def _tz_strip(idx: pd.Index) -> pd.Index:
        """Return a timezone-naive, date-normalised index (matches SPY/pipeline)."""
        try:
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            return pd.to_datetime(idx).normalize()
        except Exception:
            return idx

    def _spy_returns(self) -> Optional[pd.Series]:
        """Fetch SPY daily returns once, cached with a ~6 h monotonic TTL.

        Returns ``None`` on any failure (``compute_fundamentals`` then emits
        ``beta = NaN``). Index is tz-stripped so it aligns with per-symbol
        returns.
        """
        now = time.monotonic()
        if (
            self._spy_returns_cache is not None
            and (now - self._spy_cached_at) <= self._SPY_CACHE_TTL_SECONDS
        ):
            return self._spy_returns_cache
        try:
            import yfinance as yf  # type: ignore

            hist = yf.Ticker("SPY").history(period=self._beta_period(), auto_adjust=True)
            if hist is None or hist.empty or "Close" not in hist.columns:
                return self._spy_returns_cache  # keep any prior good series
            rets = hist["Close"].pct_change().dropna()
            rets.index = self._tz_strip(rets.index)
            self._spy_returns_cache = rets
            self._spy_cached_at = now
            return rets
        except Exception as exc:  # noqa: BLE001 — dead-letter, beta degrades to NaN
            logger.warning(
                "YahooFundamentalsProvider: SPY market-return fetch failed: %s — "
                "beta will be NaN",
                exc,
            )
            return self._spy_returns_cache

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Fetch yfinance statement frames and delegate math to
        ``compute_fundamentals``. Returns an empty dict (never raises) on any
        top-level failure.
        """
        try:
            import yfinance as yf  # type: ignore
            from data.yahoo_fundamentals import compute_fundamentals

            t = yf.Ticker(symbol)

            # --- .info is the flaky one; wrap it defensively. ------------------
            try:
                info = t.info or {}
            except Exception:
                info = {}

            # --- price + shares via fast_info (cheap, avoids .info round-trip) -
            # last_price/previous_close are attribute-only on FastInfo (its
            # .get() only recognizes camelCase keys) -- see YFinanceProvider.
            # get_latest_quote's comment for the full explanation.
            try:
                fi = t.fast_info
                price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
            except Exception:
                fi = None
                price = None
            try:
                shares_current = fi.get("shares") if fi is not None else None
            except Exception:
                shares_current = None
            if not shares_current:
                shares_current = info.get("sharesOutstanding")

            sector = info.get("sector", "N/A")
            company_name = info.get("shortName") or info.get("longName") or ""
            shares_diluted = info.get("sharesOutstanding") or shares_current

            # --- Statement frames — each isolated so one bad pull isn't fatal. -
            def _pull(attr: str):
                try:
                    return getattr(t, attr)
                except Exception:
                    return None

            income_stmt = _pull("income_stmt")
            income_stmt_quarterly = _pull("quarterly_income_stmt")

            # Prefer the most recent quarter for point-in-time equity.
            balance_sheet = _pull("quarterly_balance_sheet")
            if balance_sheet is None or (
                hasattr(balance_sheet, "empty") and balance_sheet.empty
            ):
                balance_sheet = _pull("balance_sheet")

            cashflow = _pull("cashflow")
            cashflow_quarterly = _pull("quarterly_cashflow")
            dividends = _pull("dividends")
            inst_holders = _pull("institutional_holders")

            # --- Per-symbol daily returns (for beta), tz-stripped. ------------
            try:
                hist = t.history(period=self._beta_period(), auto_adjust=True)
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    stock_returns = hist["Close"].pct_change().dropna()
                    stock_returns.index = self._tz_strip(stock_returns.index)
                else:
                    stock_returns = None
            except Exception:
                stock_returns = None

            market_returns = self._spy_returns()

            return compute_fundamentals(
                symbol,
                price=price,
                shares_current=shares_current,
                shares_diluted=shares_diluted,
                income_stmt=income_stmt,
                income_stmt_quarterly=income_stmt_quarterly,
                balance_sheet=balance_sheet,
                cashflow=cashflow,
                cashflow_quarterly=cashflow_quarterly,
                dividends=dividends,
                inst_holders=inst_holders,
                stock_returns=stock_returns,
                market_returns=market_returns,
                sector=sector,
                company_name=company_name,
            )
        except Exception as exc:  # noqa: BLE001 — dead-letter, never raise
            logger.warning(
                "YahooFundamentalsProvider.get_fundamentals(%s) failed: %s — "
                "returning empty dict",
                symbol, exc,
            )
            return {}

    def clear_spy_cache(self) -> None:
        """Evict the cached SPY market-return series (forced refresh)."""
        self._spy_returns_cache = None
        self._spy_cached_at = 0.0


# ---------------------------------------------------------------------------
# FMP (Financial Modeling Prep) provider
# ---------------------------------------------------------------------------

def _fmp_eod_payload_to_daily_returns(payload: Any) -> Optional[pd.Series]:
    """Convert an FMP ``/historical-price-eod/{variant}`` payload (a list of
    daily OHLCV dicts) into a tz-naive, ascending daily pct-change Series.

    Field-name note (verified live, 2026-07-31, FMP MCP connector, symbol
    AAPL): the ``dividend-adjusted`` variant — the one
    ``settings.FMP_BARS_ADJUSTMENT`` and this beta computation both use —
    returns ``adjClose`` (not ``close``). ``adjClose`` is preferred when
    present so a dividend-adjusted payload is never accidentally read from an
    unadjusted field; ``close`` is accepted as a fallback for the other
    (split-only) variants. Row order is NOT assumed — the Series is always
    explicitly sorted ascending by parsed date before the pct-change, so an
    API that answers descending (as observed live) or ascending is handled
    identically. Returns ``None`` (never raises) on any structural problem,
    letting the caller keep a previously cached good series rather than
    clobbering it with junk.
    """
    try:
        if not isinstance(payload, list) or not payload:
            return None
        dates: List[Any] = []
        closes: List[Any] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            d = row.get("date")
            c = row.get("adjClose", row.get("close"))
            if d is None or c is None:
                continue
            dates.append(d)
            closes.append(c)
        if not dates:
            return None
        idx = pd.to_datetime(pd.Index(dates), errors="coerce")
        ser = pd.Series(closes, index=idx, dtype="float64")
        ser = ser[~ser.index.isna()]
        if ser.empty:
            return None
        ser = ser.sort_index()
        rets = ser.pct_change().dropna()
        return rets if not rets.empty else None
    except Exception:  # pragma: no cover - defensive
        return None


def _fmp_first_row(payload: Any) -> Optional[Dict[str, Any]]:
    """Normalise an FMP quote-shaped response (a list wrapping one dict, or
    occasionally a bare dict) to a single dict, or ``None`` when
    empty/malformed. Mirrors ``data/fmp_fundamentals.py::_first`` exactly
    (kept as a small local duplicate here rather than importing a private,
    underscore-prefixed helper across a module-ownership boundary — this one
    function is a few lines and not worth that coupling). Never raises.
    """
    try:
        if isinstance(payload, list):
            return payload[0] if payload and isinstance(payload[0], dict) else None
        if isinstance(payload, dict):
            return payload if payload else None
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def _fmp_quote_timestamp_to_datetime(ts_raw: Any) -> datetime:
    """Convert FMP's ``/quote`` ``timestamp`` field (Unix epoch seconds) to a
    UTC-AWARE ``datetime`` — matching the ``Quote`` dataclass's own
    documented contract ("timestamp: UTC-aware datetime of the quote") and
    both existing providers' convention (``AlpacaProvider`` converts its
    real quote timestamp to tz-aware UTC; ``YFinanceProvider`` uses
    ``datetime.now(timezone.utc)``, tz-aware since ``fast_info`` exposes no
    usable timestamp). Falls back to "now" (UTC-aware) when the field is
    missing or unparsable — an approximate-but-honestly-aware timestamp beats
    raising over a field whose presence FMP does not guarantee for every
    account tier, and beats silently emitting a naive datetime that breaks
    every downstream ``(now - timestamp).total_seconds()`` staleness check.
    """
    if ts_raw is not None:
        try:
            return datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _fmp_bars_payload_to_df(payload: Any) -> Optional[pd.DataFrame]:
    """Reshape an FMP OHLCV payload (EOD or intraday chart — both are a list
    of per-bar dicts) into the ABC's bar-shape contract: columns exactly
    ``['Open', 'High', 'Low', 'Close', 'Volume']``. The index is left
    tz-naive but UNSORTED and UN-normalised here — callers apply their own
    interval-specific sort/normalize/truncate semantics (daily bars get
    ``.normalize()`` + ``.tail(lookback_days)``; hourly bars keep their real
    intraday timestamp). Returns ``None`` (never raises) on any structural
    problem, so the caller can raise a single, clearly-worded
    ``MarketDataError`` instead of a bespoke parse error.

    Field-name handling (EOD; per F5's live probe — see
    ``scripts/verify_fmp_bars.py``'s module docstring): the
    ``dividend-adjusted`` / ``non-split-adjusted`` variants return
    ``adjOpen``/``adjHigh``/``adjLow``/``adjClose``; the (NOT the
    recommended default) ``full`` variant returns plain
    ``open``/``high``/``low``/``close``. ``adjX`` is preferred when present,
    falling back to plain ``X``, so a payload from either variant reshapes
    correctly — this mirrors ``scripts/verify_fmp_bars.py::_extract_close``'s
    own defensive field-name handling. A row missing any of open/high/low/
    close (e.g. every row of a ``light``-variant payload, which is
    close/price-only with no OHLC breakdown at all) is skipped rather than
    filled with a fabricated value; an all-skipped payload returns ``None``,
    which the caller turns into ``MarketDataError`` rather than a silently
    empty frame. The intraday chart endpoint's field names were NOT
    live-probed (only ``/historical-price-eod``'s close field was); the same
    ``adjX``-or-``X`` fallback is applied defensively in case FMP ever serves
    an adjusted intraday variant, but the documented/expected shape there is
    plain ``open``/``high``/``low``/``close``/``volume``.

    ``Volume`` is used as-is from the ``volume`` field; a row with no volume
    field falls back to ``0`` (matching ``YFinanceProvider.get_intraday_bars``'s
    own precedent of filling an entirely-missing Volume column with ``0``
    rather than NaN, since a bar's volume is a count, not a valuation input).
    """
    try:
        if not isinstance(payload, list) or not payload:
            return None
        dates: List[Any] = []
        opens: List[Any] = []
        highs: List[Any] = []
        lows: List[Any] = []
        closes: List[Any] = []
        vols: List[Any] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            d = row.get("date")
            o = row.get("adjOpen", row.get("open"))
            h = row.get("adjHigh", row.get("high"))
            l = row.get("adjLow", row.get("low"))
            c = row.get("adjClose", row.get("close"))
            if d is None or o is None or h is None or l is None or c is None:
                continue
            v = row.get("volume")
            dates.append(d)
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            vols.append(v if v is not None else 0)
        if not dates:
            return None
        idx = pd.to_datetime(pd.Index(dates), errors="coerce")
        df = pd.DataFrame(
            {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
            index=idx,
        )
        df = df[~df.index.isna()]
        if df.empty:
            return None
        df = df.astype("float64")
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except Exception:  # pragma: no cover - defensive
        return None


# Once-per-process latch for the FMP_BARS_ADJUSTMENT mismatch warning — mirrors
# data/fmp_client.py's own _fmp_auth_error_logged pattern (a module-level bool,
# not a class attribute, since it is about the PROCESS's configuration, not
# any one FMPProvider instance). An operator who set this once should not have
# their log flooded once per symbol per cycle.
_fmp_bars_adjustment_warned: bool = False


def _warn_once_if_fmp_bars_adjustment_mismatched(variant: str) -> None:
    """Log a WARNING exactly once per process when ``settings.FMP_BARS_ADJUSTMENT``
    is anything other than the recommended ``"dividend-adjusted"`` default.

    This is the single highest silent-corruption risk in the whole FMP
    integration (see ``scripts/verify_fmp_bars.py``'s module docstring):
    ``"light"`` and ``"full"`` are SPLIT-ONLY and do NOT match yfinance's
    ``auto_adjust=True`` (split AND dividend adjusted) convention that every
    downstream return series, indicator, GARCH fit and backtest is built
    against — and the mismatch corrupts silently, never failing loudly. This
    warning exists to make an operator's misconfiguration hard to miss rather
    than merely documented in a settings description nobody re-reads.
    """
    global _fmp_bars_adjustment_warned
    if variant != "dividend-adjusted" and not _fmp_bars_adjustment_warned:
        logger.warning(
            "FMPProvider: FMP_BARS_ADJUSTMENT=%r is NOT the recommended "
            "'dividend-adjusted' default. 'light' and 'full' are SPLIT-ONLY "
            "and do NOT match yfinance's auto_adjust=True (split+dividend) "
            "convention -- this WILL silently corrupt every return series, "
            "indicator, GARCH fit, and backtest built on these bars. Run "
            "scripts/verify_fmp_bars.py (max abs relative close diff < 1e-4 "
            "across KO/JNJ/AAPL) before trusting this setting.",
            variant,
        )
        _fmp_bars_adjustment_warned = True


def reset_fmp_bars_adjustment_warning() -> None:
    """Test-only: reset the once-per-process ``FMP_BARS_ADJUSTMENT`` mismatch
    warning latch so a test can assert the WARNING fires again."""
    global _fmp_bars_adjustment_warned
    _fmp_bars_adjustment_warned = False


class FMPProvider(MarketDataProvider):
    """Financial Modeling Prep (FMP) market-data provider.

    Implements the full ``MarketDataProvider`` ABC: fundamentals (wave 1),
    quotes, and daily/hourly bars (wave 2). FMP becomes selectable as
    ``MARKET_DATA_PROVIDER=fmp`` only via an EXPLICIT setting — ``FMP_API_KEY``
    alone never elects it (the same two-gate discipline as
    ``FUNDAMENTALS_SOURCE=fmp``), so an operator who adds the key to light up
    one diagnostic feed never has their quote/bars source silently change.

    Fetches every fundamentals payload via the ``data.fmp_client`` wrappers
    (each in its own try/except so one missing endpoint — e.g. an
    Ultimate-only one on the Starter plan — never blanks the rest) and
    delegates ALL math/scale conversion to
    ``data.fmp_fundamentals.map_fundamentals`` (pure, offline-testable). This
    class is an I/O shell only, matching ``AlpacaProvider`` /
    ``YahooFundamentalsProvider``'s division of labor.

    Parameters
    ----------
    api_key:
        Non-empty FMP API key. Validated at construction (``RuntimeError`` on
        empty) as a fail-fast guard; the actual HTTP calls always read
        ``settings.FMP_API_KEY`` internally via ``data/fmp_client.py`` (the
        one-seam design — see that module's docstring), so this argument is
        effectively a construction-time sanity check rather than a value
        threaded through to each request.
    """

    SOURCE = "fmp"
    # IS_REALTIME is NOT a fixed class constant like Alpaca/yfinance's — FMP's
    # real-time-ness on the Starter plan could not be verified live (see the
    # plan's Risks section), so it is operator-controlled via
    # settings.FMP_QUOTES_REALTIME and read PER INSTANCE at construction
    # time, never frozen at import.
    IS_REALTIME: bool = False

    # Refetch SPY market-return series at most every ~6h — mirrors
    # YahooFundamentalsProvider._SPY_CACHE_TTL_SECONDS exactly, so a
    # multi-symbol cycle fetches the benchmark leg once, not once per ticker.
    _SPY_CACHE_TTL_SECONDS = 6 * 3600

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError("FMPProvider requires a non-empty api_key.")
        self._api_key = api_key
        from settings import settings as _settings
        self.IS_REALTIME = bool(getattr(_settings, "FMP_QUOTES_REALTIME", False))
        # (fetched_at, pd.Series) semantics via two attributes, matching
        # YahooFundamentalsProvider's cache shape exactly.
        self._spy_returns_cache: Optional[pd.Series] = None
        self._spy_cached_at: float = 0.0

    def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch the latest quote via FMP's ``/quote`` endpoint.

        ``bid``/``ask`` are always ``float('nan')`` — never fabricated
        ``0.0`` — because the Starter plan's ``/quote`` response carries no
        NBBO data (confirmed during planning). ``is_stale`` is
        ``not self.IS_REALTIME`` (operator-controlled via
        ``settings.FMP_QUOTES_REALTIME``, defaulting to ``False`` i.e.
        stale=True, since real-time-ness on Starter could not be verified
        live). Never raises anything but ``MarketDataError`` (ABC contract,
        CONSTRAINT #6): the entire body is wrapped in a broad
        ``try/except Exception``, converting any ``FMPUnavailable`` or other
        unexpected failure into ``MarketDataError`` with the original
        exception chained.
        """
        try:
            from data import fmp_client

            payload = fmp_client.quote(symbol)
            row = _fmp_first_row(payload)
            if row is None:
                raise MarketDataError(
                    f"FMP returned an empty/malformed quote payload for {symbol}"
                )

            price = row.get("price")
            if price is None:
                raise MarketDataError(
                    f"FMP quote payload for {symbol} has no 'price' field"
                )

            timestamp = _fmp_quote_timestamp_to_datetime(row.get("timestamp"))

            return Quote(
                symbol=symbol.upper(),
                price=float(price),
                bid=float("nan"),
                ask=float("nan"),
                timestamp=timestamp,
                is_stale=not self.IS_REALTIME,
                source=self.SOURCE,
            )
        except MarketDataError:
            raise
        except Exception as exc:  # noqa: BLE001 — converted to MarketDataError below
            logger.error("FMPProvider.get_latest_quote(%s) failed: %s", symbol, exc)
            raise MarketDataError(f"FMP quote fetch failed for {symbol}: {exc}") from exc

    def get_quotes_batch(self, symbols: List[str]) -> Dict[str, Quote]:
        """Override of the ABC's per-symbol-loop default (F6, docs/
        module_efficiency_redundancy_audit.md): resolves ALL symbols via ONE
        call to ``fmp_client.batch_quote()`` (the ``/batch-quote`` endpoint —
        already the correct-usage precedent in
        ``data/paper_account_store.py::_resolve_position_prices``) instead of
        N individual ``/quote`` requests.

        Same never-raises, dead-letter-per-symbol contract as the ABC
        default: a symbol absent from FMP's response, or with no usable
        ``price``, is simply absent from the returned dict. A total request
        failure (network error, FMP cooldown open) degrades to an EMPTY
        dict for the whole batch rather than falling back to N individual
        calls -- the batch endpoint failing is itself informative (FMP is
        down for everyone), and retrying via N single-symbol calls against
        the same unavailable host would just reproduce the N+1 cost this
        method exists to avoid.

        ``bid``/``ask`` are always ``float('nan')`` and ``is_stale`` is
        ``not self.IS_REALTIME`` -- identical to ``get_latest_quote``'s own
        documented Starter-plan limitations, since ``/batch-quote`` carries
        the same fields as ``/quote``.
        """
        if not symbols:
            return {}
        out: Dict[str, Quote] = {}
        try:
            from data import fmp_client

            payload = fmp_client.batch_quote(list(symbols))
            rows = payload if isinstance(payload, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sym = str(row.get("symbol") or "").upper().strip()
                price = row.get("price")
                if not sym or price is None:
                    continue
                try:
                    price_f = float(price)
                except (TypeError, ValueError):
                    continue
                timestamp = _fmp_quote_timestamp_to_datetime(row.get("timestamp"))
                out[sym] = Quote(
                    symbol=sym,
                    price=price_f,
                    bid=float("nan"),
                    ask=float("nan"),
                    timestamp=timestamp,
                    is_stale=not self.IS_REALTIME,
                    source=self.SOURCE,
                )
        except Exception as exc:  # noqa: BLE001 -- dead-letter the whole batch, CONSTRAINT #6
            logger.error("FMPProvider.get_quotes_batch(%s) failed: %s", symbols, exc)
            return {}
        return out

    def get_intraday_bars(
        self, symbol: str, lookback_days: int = 252, interval: str = "1d"
    ) -> pd.DataFrame:
        """Fetch OHLCV bars via FMP.

        ``interval="1d"`` (default) uses FMP's
        ``/historical-price-eod/{settings.FMP_BARS_ADJUSTMENT}`` (default
        ``"dividend-adjusted"``, the ONLY variant verified to match
        yfinance's ``auto_adjust=True`` convention — see
        ``scripts/verify_fmp_bars.py``). The lookback window is
        ``date.today() - timedelta(days=ceil(lookback_days * 1.45))``, a
        calendar-to-trading-day buffer matching the plan's convention.
        ``interval="1h"`` uses FMP's ``/historical-chart/1hour`` over a
        bounded trailing ~30-calendar-day window (FMP intraday history is
        not unlimited, unlike EOD which goes back to 2008) — mirroring
        ``YFinanceProvider.get_intraday_bars``'s own bounded-hourly-window
        precedent. Any other ``interval`` raises ``MarketDataError``,
        mirroring ``YFinanceProvider``'s exact handling of an unsupported
        interval.

        Reshapes the payload to the ABC's exact bar-shape contract: columns
        exactly ``['Open', 'High', 'Low', 'Close', 'Volume']``, tz-naive
        ascending ``DatetimeIndex``. An empty/malformed payload always raises
        ``MarketDataError`` — this method never returns an empty DataFrame
        silently (ABC contract, same as every other provider).

        Never raises anything but ``MarketDataError`` (CONSTRAINT #6): the
        entire body is wrapped in a broad ``try/except Exception``,
        converting any ``FMPUnavailable`` or other unexpected failure into
        ``MarketDataError`` with the original exception chained.
        """
        try:
            from data import fmp_client

            if interval == "1d":
                variant = str(
                    getattr(settings, "FMP_BARS_ADJUSTMENT", "dividend-adjusted")
                    or "dividend-adjusted"
                )
                _warn_once_if_fmp_bars_adjustment_mismatched(variant)

                to_date = date.today()
                from_date = to_date - timedelta(days=math.ceil(lookback_days * 1.45))
                payload = fmp_client.historical_eod(
                    symbol,
                    variant=variant,
                    from_date=from_date.isoformat(),
                    to_date=to_date.isoformat(),
                )
                df = _fmp_bars_payload_to_df(payload)
                if df is None or df.empty:
                    raise MarketDataError(
                        f"FMP returned an empty/malformed EOD bars payload for "
                        f"{symbol} (variant={variant!r})"
                    )
                df.index = df.index.normalize()
                df.sort_index(inplace=True)
                return df.tail(lookback_days)

            elif interval == "1h":
                to_date = date.today()
                from_date = to_date - timedelta(days=30)
                payload = fmp_client.intraday(
                    symbol,
                    interval="1hour",
                    from_date=from_date.isoformat(),
                    to_date=to_date.isoformat(),
                )
                df = _fmp_bars_payload_to_df(payload)
                if df is None or df.empty:
                    raise MarketDataError(
                        f"FMP returned an empty/malformed intraday bars payload "
                        f"for {symbol}"
                    )
                df.sort_index(inplace=True)
                return df

            else:
                raise MarketDataError(
                    f"FMPProvider.get_intraday_bars: unsupported interval "
                    f"{interval!r} (supported: '1d', '1h')"
                )

        except MarketDataError:
            raise
        except Exception as exc:  # noqa: BLE001 — converted to MarketDataError below
            logger.error("FMPProvider.get_intraday_bars(%s) failed: %s", symbol, exc)
            raise MarketDataError(f"FMP bars fetch failed for {symbol}: {exc}") from exc

    @staticmethod
    def _beta_lookback_calendar_days() -> int:
        """Calendar days to request so ``BETA_LOOKBACK_DAYS`` TRADING days are
        covered, e.g. ``int(504 * 1.6)`` ~= 806 calendar days for the 504
        (~2y) default. The 1.6x buffer absorbs weekends + holidays."""
        try:
            trading_days = int(settings.BETA_LOOKBACK_DAYS)
        except Exception:
            trading_days = 504
        return int(trading_days * 1.6)

    def _fetch_daily_returns(self, symbol: str) -> Optional[pd.Series]:
        """One symbol's daily-return series via FMP's dividend-adjusted EOD
        bars, covering ``settings.BETA_LOOKBACK_DAYS`` trading days. Returns
        ``None`` (never raises) on any failure — the caller degrades beta to
        NaN rather than fabricating a neutral value."""
        try:
            from data import fmp_client
            from data.fmp_client import FMPUnavailable

            to_date = date.today()
            from_date = to_date - timedelta(days=self._beta_lookback_calendar_days())
            payload = fmp_client.historical_eod(
                symbol,
                variant="dividend-adjusted",
                from_date=from_date.isoformat(),
                to_date=to_date.isoformat(),
            )
            return _fmp_eod_payload_to_daily_returns(payload)
        except FMPUnavailable:
            return None
        except Exception:  # pragma: no cover - defensive
            return None

    def _spy_returns(self) -> Optional[pd.Series]:
        """Fetch SPY daily returns once, cached with a ~6h monotonic TTL —
        shared across every symbol within a run so a multi-symbol cycle does
        not refetch the benchmark leg once per ticker. Returns ``None`` on
        any failure (:func:`_fetch_daily_returns` already dead-letters), in
        which case any prior good cached series is kept rather than cleared."""
        now = time.monotonic()
        if (
            self._spy_returns_cache is not None
            and (now - self._spy_cached_at) <= self._SPY_CACHE_TTL_SECONDS
        ):
            return self._spy_returns_cache
        fresh = self._fetch_daily_returns("SPY")
        if fresh is None or fresh.empty:
            return self._spy_returns_cache  # keep any prior good series
        self._spy_returns_cache = fresh
        self._spy_cached_at = now
        return fresh

    def _compute_beta(self, symbol: str) -> float:
        """``Cov(stock, SPY) / Var(SPY)`` via ``data.fmp_fundamentals.compute_beta``.
        Never fabricates a neutral 1.0 on failure — degrades to NaN."""
        try:
            from data.fmp_fundamentals import compute_beta as _compute_beta_fn

            stock_returns = self._fetch_daily_returns(symbol)
            market_returns = self._spy_returns()
            return _compute_beta_fn(stock_returns, market_returns)
        except Exception:  # pragma: no cover - defensive
            return float("nan")

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Fetch every FMP fundamentals payload for ``symbol`` and delegate
        all math/scale conversion to ``data.fmp_fundamentals.map_fundamentals``.

        Never raises (ABC contract, CONSTRAINT #6): the entire body is
        wrapped in ``try/except Exception``, returning ``{}`` + a logged
        WARNING on any top-level failure. Each individual FMP call is ALSO
        isolated in its own ``try/except FMPUnavailable`` so one missing
        endpoint (an Ultimate-only one on the Starter plan, a transient
        outage, ...) degrades only its own fields to NaN rather than blanking
        the whole response.
        """
        try:
            from data import fmp_client
            from data.fmp_client import FMPUnavailable
            from data.fmp_fundamentals import FMP_FUNDAMENTAL_KEYS, map_fundamentals

            def _safe(fetch_fn, *args, **kwargs):
                try:
                    return fetch_fn(*args, **kwargs)
                except FMPUnavailable:
                    return None

            quote = _safe(fmp_client.quote, symbol)
            profile = _safe(fmp_client.profile, symbol)
            key_metrics_ttm = _safe(fmp_client.key_metrics_ttm, symbol)
            ratios_ttm = _safe(fmp_client.ratios_ttm, symbol)
            income_statement_ttm = _safe(fmp_client.income_statement_ttm, symbol)
            dividends = _safe(fmp_client.dividends, symbol)
            shares_float = _safe(fmp_client.shares_float, symbol)

            beta = self._compute_beta(symbol)

            result = map_fundamentals(
                symbol,
                quote=quote,
                profile=profile,
                key_metrics_ttm=key_metrics_ttm,
                ratios_ttm=ratios_ttm,
                income_statement_ttm=income_statement_ttm,
                shares_float=shares_float,
                dividends=dividends,
                beta=beta,
            )

            # A degraded-but-nonempty response (majority of numeric fields
            # NaN) is still "worked" from CompositeProvider's perspective — a
            # non-empty dict never triggers the fallback chain — so it is
            # worth a WARNING (not debug/info) to keep it visible in logs.
            numeric_keys = [
                k for k in FMP_FUNDAMENTAL_KEYS if k not in ("shortName", "sector")
            ]
            nan_count = sum(
                1 for k in numeric_keys
                if isinstance(result.get(k), float) and result.get(k) != result.get(k)
            )
            if numeric_keys and nan_count > len(numeric_keys) // 2:
                logger.warning(
                    "FMPProvider.get_fundamentals(%s): %d/%d numeric fields "
                    "are NaN -- a degraded-but-nonempty response (this will "
                    "NOT trigger the fallback chain, since the dict is not "
                    "empty).",
                    symbol, nan_count, len(numeric_keys),
                )
            return result
        except Exception as exc:  # noqa: BLE001 — dead-letter, never raise
            logger.warning(
                "FMPProvider.get_fundamentals(%s) failed: %s — returning empty dict",
                symbol, exc,
            )
            return {}


# ---------------------------------------------------------------------------
# Finnhub provider (fundamentals only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sliding-window rate limiter (used by FinnhubProvider)
# ---------------------------------------------------------------------------

class _SlidingWindowRateLimiter:
    """Crude sliding-window rate limiter: at most ``max_calls`` per ``window_seconds``.

    ``acquire()`` is a synchronous, blocking call: if the budget is exhausted it
    sleeps until the oldest call in the window expires, then records the new
    call.  Thread-unsafe by design (the orchestrator's per-symbol loop is
    serial); tests can monkeypatch ``time.sleep`` to avoid real waits.

    Why this exists: the Finnhub free tier is 60 calls/minute and we make up
    to 3 calls per symbol (`company_basic_financials`, `quote`, `company_profile2`).
    On a 100-symbol watchlist sync we'd otherwise issue ~300 calls in seconds
    and be rate-limited for the bulk of the run.

    Parameters
    ----------
    max_calls:
        Maximum calls permitted within ``window_seconds``.
    window_seconds:
        Sliding-window length in seconds.  Free-tier Finnhub uses 60 s.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max(1, int(max_calls))
        self._window = float(window_seconds)
        self._timestamps: list[float] = []

    def acquire(self) -> None:
        """Block until at least one call can be issued under the budget."""
        now = time.monotonic()
        cutoff = now - self._window
        # Drop expired timestamps in-place; the list is bounded by max_calls.
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max_calls:
            wait = self._window - (now - self._timestamps[0])
            if wait > 0:
                logger.info(
                    "FinnhubRateLimiter: budget exhausted (%d/%d in %.0fs window); "
                    "sleeping %.2fs",
                    len(self._timestamps), self._max_calls, self._window, wait,
                )
                time.sleep(wait)
            now = time.monotonic()
            cutoff = now - self._window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
        self._timestamps.append(now)


# ---------------------------------------------------------------------------
# In-process TTL fundamentals cache
# ---------------------------------------------------------------------------

class _FundamentalsCache:
    """In-process TTL cache for fundamentals dicts (positive AND negative entries).

    Fundamentals are quarterly/slow-moving; caching for hours is safe.  We also
    cache "empty" responses so a symbol that returned 429 / unknown does not
    cause another Finnhub round-trip on every cycle within the TTL — this is
    the key behaviour that protects the free tier across back-to-back runs.

    Positive and negative responses use DIFFERENT TTLs. A provider that was
    rate-limited or briefly unavailable would otherwise incorrectly stay
    "no data" for the full positive-cache TTL (up to 6 h by default) even
    after it recovers — so negative (empty-dict) responses get a much
    shorter TTL (default 15 min, ``settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS``)
    while positive results keep the longer, slow-moving-data TTL.

    The cache is per-process and never written to disk (same constraint as
    ``_QuoteCache``).
    """

    def __init__(self, ttl_seconds: int = 21_600, neg_ttl_seconds: Optional[int] = None) -> None:
        self._ttl = max(1, int(ttl_seconds))
        # Negative TTL defaults to settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS
        # when not explicitly provided, so callers that don't know about the
        # split (e.g. older test fixtures) still get the shorter recovery
        # window rather than silently reusing the long positive TTL.
        if neg_ttl_seconds is None:
            try:
                from settings import settings as _settings
                neg_ttl_seconds = _settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS
            except Exception:
                neg_ttl_seconds = 900
        self._neg_ttl = max(1, int(neg_ttl_seconds))
        self._store: Dict[str, tuple[Dict[str, Any], float, bool]] = {}

    def get(self, symbol: str) -> Optional[Dict[str, Any]]:
        entry = self._store.get(symbol)
        if entry is None:
            return None
        payload, cached_at, is_negative = entry
        ttl = self._neg_ttl if is_negative else self._ttl
        if time.monotonic() - cached_at > ttl:
            del self._store[symbol]
            return None
        # Defensive copy so callers cannot mutate the cached dict.
        return dict(payload)

    def put(self, symbol: str, payload: Dict[str, Any]) -> None:
        # A falsy/empty payload is treated as a negative (no-data) response
        # and gets the shorter negative TTL; any non-empty dict gets the
        # standard (longer) positive TTL.
        is_negative = not payload
        self._store[symbol] = (dict(payload), time.monotonic(), is_negative)

    def clear(self) -> None:
        self._store.clear()


class FinnhubProvider:
    """Fundamentals-only provider backed by the Finnhub free tier.

    DEPRECATED as a fundamentals source (2026-07): no longer wired into
    CompositeProvider; retained for reference/manual use. news_catalyst uses its
    own Finnhub client.

    Uses ``company_basic_financials`` for balance-sheet metrics, shaped to
    match the yfinance ``.info`` dict keys consumed by
    ``FundamentalDataDTO.from_raw_dict()``.

    Degrades gracefully (returns an empty dict + logged warning) when
    ``FINNHUB_API_KEY`` is absent.

    Rate limiting + caching (2026-06)
    ---------------------------------
    The free Finnhub tier is 60 calls/minute and each ``get_fundamentals``
    invocation issues up to 3 API calls, so a 50+ symbol watchlist sync would
    otherwise exhaust the quota in seconds and produce a flood of 429s.  This
    class now:

    * Caches every fundamentals response (positive AND empty) in a per-process
      TTL cache (default 6 hours).  Repeat lookups within the TTL never touch
      the network, so back-to-back runs don't re-rate-limit themselves.
    * Throttles outbound calls via a sliding-window rate limiter (default 50
      calls / 60 s — under the 60/min ceiling to leave headroom for the two
      auxiliary endpoints).
    * On a 429 response, sleeps with exponential backoff (1 retry) and falls
      back to an empty dict on persistent failure.

    Parameters
    ----------
    api_key:
        Finnhub API key.  None → degrade-mode (empty dict responses).
    cache_ttl_seconds:
        TTL for the fundamentals cache.  Defaults to
        ``FUNDAMENTALS_CACHE_TTL_SECONDS`` env-var (int) or 21600 (6 h).
    rate_limit_per_min:
        Sliding-window call budget per 60 s.  Defaults to
        ``FINNHUB_RATE_LIMIT_PER_MIN`` env-var (int) or 50.
    """

    # Provenance attributes for completeness — this class is deprecated and
    # unwired (nothing constructs it inside CompositeProvider), so neither is
    # read in production today. Declared so a future re-wiring inherits the
    # same attribute contract every other provider follows.
    SOURCE = "finnhub"
    IS_REALTIME = False

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
        "debtToEquityQuarterly": "debtToEquity",
        "grossMarginTTM": "grossMargins",
        "operatingMarginTTM": "operatingMargins",
        "heldPercentInstitutions": "heldPercentInstitutions",
        "currentRatioQuarterly": "currentRatio",
    }

    def __init__(
        self,
        api_key: Optional[str],
        cache_ttl_seconds: Optional[int] = None,
        rate_limit_per_min: Optional[int] = None,
        neg_cache_ttl_seconds: Optional[int] = None,
    ) -> None:
        self._api_key = api_key
        self._client: Optional[Any] = None
        if api_key:
            self._client = self._build_client(api_key)

        # Per-process fundamentals cache (positive + negative responses).
        # Defaults can be overridden via env vars to make ad-hoc tuning trivial
        # without touching code (e.g. raise to 24h on a stale-tolerant machine).
        # Negative (empty-dict) responses use a much shorter TTL so a symbol
        # that was rate-limited or briefly down recovers quickly instead of
        # staying "no data" for the full positive TTL.
        ttl = cache_ttl_seconds if cache_ttl_seconds is not None else settings.FUNDAMENTALS_CACHE_TTL_SECONDS
        neg_ttl = neg_cache_ttl_seconds if neg_cache_ttl_seconds is not None else settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS
        rpm = rate_limit_per_min if rate_limit_per_min is not None else settings.FINNHUB_RATE_LIMIT_PER_MIN
        self._cache = _FundamentalsCache(ttl_seconds=ttl, neg_ttl_seconds=neg_ttl)
        self._rate_limiter = _SlidingWindowRateLimiter(
            max_calls=rpm, window_seconds=60.0
        )

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

    def _ensure_init(self) -> None:
        """Lazily initialise cache + rate limiter if the instance was built via
        ``__new__`` (as in some test fixtures) and ``__init__`` was skipped.

        Defensive: tests that construct ``FinnhubProvider.__new__(...)`` and
        only assign ``_api_key`` + ``_client`` must continue to work without
        every test needing to know about the cache/limiter internals.
        """
        if not hasattr(self, "_cache"):
            self._cache = _FundamentalsCache(
                ttl_seconds=settings.FUNDAMENTALS_CACHE_TTL_SECONDS,
                neg_ttl_seconds=settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS,
            )
        if not hasattr(self, "_rate_limiter"):
            self._rate_limiter = _SlidingWindowRateLimiter(
                max_calls=settings.FINNHUB_RATE_LIMIT_PER_MIN,
                window_seconds=60.0,
            )

    def _is_rate_limit_exc(self, exc: BaseException) -> bool:
        """Return True if ``exc`` represents a Finnhub 429 (rate-limit) response.

        Detection is duck-typed against ``FinnhubAPIException.status_code`` so
        this module never has to import ``finnhub`` eagerly (which would break
        the optional-dependency contract).
        """
        return getattr(exc, "status_code", None) == 429

    def _call_with_rate_limit(self, fn, *args, **kwargs):
        """Invoke a Finnhub client method under the sliding-window budget.

        On a 429 response, sleep with one-shot exponential backoff and retry
        once.  Persistent failure raises so the caller can decide whether to
        return empty / log / cache the failure.
        """
        self._rate_limiter.acquire()
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised after one backoff retry
            if self._is_rate_limit_exc(exc):
                backoff = 2.0
                logger.warning(
                    "FinnhubProvider: 429 from %s — backing off %.1fs and retrying once",
                    getattr(fn, "__name__", "<call>"), backoff,
                )
                time.sleep(backoff)
                self._rate_limiter.acquire()
                return fn(*args, **kwargs)
            raise

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Return fundamentals shaped as a yfinance .info dict.

        Returns an empty dict when the key is absent or the call fails.

        Caching: every response — positive OR empty — is cached for
        ``FUNDAMENTALS_CACHE_TTL_SECONDS`` (default 6 h).  Negative caching is
        deliberate: a symbol that returned 429 or "unknown ticker" should not
        cause another network call in the same hour, because that is exactly
        what blows the free-tier budget on repeated orchestrator passes.
        """
        self._ensure_init()
        sym = symbol.upper()

        cached = self._cache.get(sym)
        if cached is not None:
            return cached

        if self._client is None:
            logger.warning(
                "FinnhubProvider: FINNHUB_API_KEY not configured — "
                "returning empty fundamentals for %s.  "
                "Set FINNHUB_API_KEY in .env for fundamental data.",
                symbol,
            )
            # Negative cache so we don't repeat the warning every loop.
            self._cache.put(sym, {})
            return {}

        try:
            resp = self._call_with_rate_limit(
                self._client.company_basic_financials, symbol, "all"
            )
            metrics: Dict[str, Any] = resp.get("metric", {}) or {}

            # Shape Finnhub metrics to match yfinance .info key names
            info: Dict[str, Any] = {}
            for fh_key, yf_key in self._METRIC_MAP.items():
                val = metrics.get(fh_key)
                if val is not None:
                    # Finnhub returns dividendYield as percent (e.g. 0.52 = 0.52%);
                    # normalise to the fraction the platform expects. (yfinance ALSO
                    # returns percent now and is normalised at its own ingestion
                    # path via dto_models.normalize_yfinance_dividend_yield.)
                    if yf_key == "dividendYield" and isinstance(val, (int, float)):
                        val = val / 100.0
                    info[yf_key] = val

            # Fetch quote for currentPrice if not already present
            if "currentPrice" not in info:
                try:
                    q_resp = self._call_with_rate_limit(self._client.quote, symbol)
                    if q_resp and q_resp.get("c"):
                        info["currentPrice"] = float(q_resp["c"])
                except Exception as exc:  # noqa: BLE001 — auxiliary call, optional
                    logger.debug(
                        "FinnhubProvider: quote(%s) failed: %s — skipping currentPrice",
                        symbol, exc,
                    )

            # Pull company profile for name/sector
            try:
                profile = self._call_with_rate_limit(
                    self._client.company_profile2, symbol=symbol
                ) or {}
                if profile.get("name"):
                    info["shortName"] = profile["name"]
                if profile.get("finnhubIndustry"):
                    info["sector"] = profile["finnhubIndustry"]
                if profile.get("shareOutstanding"):
                    shares = float(profile["shareOutstanding"]) * 1e6
                    if "marketCap" not in info and "currentPrice" in info:
                        info["marketCap"] = shares * info["currentPrice"]
            except Exception as exc:  # noqa: BLE001 — auxiliary call, optional
                logger.debug(
                    "FinnhubProvider: company_profile2(%s) failed: %s — skipping",
                    symbol, exc,
                )

            self._cache.put(sym, info)
            return info

        except Exception as exc:
            # Downgrade 429 to INFO (expected, recoverable next cycle); keep
            # other failures at WARNING so unexpected errors stay visible.
            if self._is_rate_limit_exc(exc):
                logger.info(
                    "FinnhubProvider.get_fundamentals(%s) rate-limited after retry — "
                    "caching empty dict for TTL window",
                    symbol,
                )
            else:
                logger.warning(
                    "FinnhubProvider.get_fundamentals(%s) failed: %s — returning empty dict",
                    symbol, exc,
                )
            self._cache.put(sym, {})
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


class _BarsCache:
    """Thread-safe in-process TTL cache for intraday/daily bar DataFrames.

    Mirrors ``_QuoteCache`` (monotonic-clock TTL, in-process only, never
    persisted) but keyed by ``(symbol, lookback_days)`` and guarded by a lock,
    because bars are fetched per-ticker from the concurrent data-fetch worker
    pool. Daily-resolution bars change at most once per trading day, so a short
    TTL (default 300 s) safely de-duplicates the back-to-back fetches a single
    refresh cycle makes (e.g. HistoricalStore top-up + a forecasting refetch)
    without ever serving cross-day-stale data within a cycle.

    Stored frames are always returned as ``.copy()`` so a caller mutating its
    result can never corrupt the cached frame.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = max(1, int(ttl_seconds))
        self._store: Dict[tuple[str, int, str], tuple[pd.DataFrame, float]] = {}
        self._lock = threading.Lock()

    def get(self, symbol: str, lookback_days: int, interval: str = "1d") -> Optional[pd.DataFrame]:
        """Return a COPY of the cached bars, or None if absent / expired."""
        key = (symbol, int(lookback_days), interval)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            df, cached_at = entry
            if time.monotonic() - cached_at > self._ttl:
                del self._store[key]
                return None
            return df.copy()

    def put(self, symbol: str, lookback_days: int, df: pd.DataFrame, interval: str = "1d") -> None:
        """Store a COPY of the bars with the current monotonic timestamp."""
        key = (symbol, int(lookback_days), interval)
        with self._lock:
            self._store[key] = (df.copy(), time.monotonic())

    def invalidate(self, symbol: str) -> None:
        """Remove all cached lookback windows for a single symbol."""
        with self._lock:
            for key in [k for k in self._store if k[0] == symbol]:
                del self._store[key]

    def clear(self) -> None:
        """Wipe all cached bars (e.g. on session restart)."""
        with self._lock:
            self._store.clear()


# ---------------------------------------------------------------------------
# Composite provider — the main entrypoint for the rest of the app
# ---------------------------------------------------------------------------

class CompositeProvider(MarketDataProvider):
    """Auto-selecting composite that routes quotes/bars to one backend and
    fundamentals to the Yahoo-derived statement engine (with yfinance fallback).

    Provider selection order
    ~~~~~~~~~~~~~~~~~~~~~~~~
    1. ``MARKET_DATA_PROVIDER=fmp`` → ``FMPProvider`` (never auto-elected by
       ``FMP_API_KEY`` alone; quotes/bars then run through an ordered
       fallback chain — see ``_get_quote_via_fmp_chain`` /
       ``_get_bars_via_fmp_chain`` — gated by ``FMP_FALLBACK_ENABLED``)
    2. ``MARKET_DATA_PROVIDER=alpaca`` → ``AlpacaProvider``
    3. ``MARKET_DATA_PROVIDER=yfinance`` → ``YFinanceProvider``
    4. Env-var absent, ``ALPACA_API_KEY`` + ``ALPACA_SECRET_KEY`` set → Alpaca
    5. Otherwise → ``YFinanceProvider``

    Fundamentals come from the Yahoo-derived statement-computed engine
    (``YahooFundamentalsProvider``) as the primary source, with a raw yfinance
    ``.info`` fallback (``YFinanceProvider.get_fundamentals()``) when the primary
    returns nothing. ``FUNDAMENTALS_SOURCE=yfinance_info`` forces the raw
    ``.info`` provider as primary; ``FUNDAMENTALS_SOURCE=fmp`` routes through
    its own ordered fallback chain (``_get_fundamentals_via_fmp_chain``).
    Finnhub is no longer wired in.

    Parameters
    ----------
    quote_ttl_seconds:
        TTL for the in-process quote cache.  Defaults to
        ``MARKET_DATA_QUOTE_TTL_SECONDS`` env-var (int), then 30 s.
    """

    def __init__(self, quote_ttl_seconds: Optional[int] = None) -> None:
        ttl = quote_ttl_seconds or int(settings.MARKET_DATA_QUOTE_TTL_SECONDS)
        self._cache = _QuoteCache(ttl_seconds=ttl)
        # Short-TTL cache for get_intraday_bars — bars are daily-resolution, so
        # a small default safely de-duplicates the back-to-back fetches a
        # single refresh cycle issues per symbol.
        self._bars_cache = _BarsCache(ttl_seconds=int(settings.MARKET_DATA_BARS_TTL_SECONDS))
        # Composite-level fundamentals cache wraps Finnhub-then-yfinance so
        # neither backend is re-hammered within the TTL window, regardless of
        # which source produced the final dict.  Defense in depth: the
        # FinnhubProvider has its own cache for direct callers; this one
        # protects the yfinance fallback path too.
        self._fundamentals_cache = _FundamentalsCache(
            ttl_seconds=int(settings.FUNDAMENTALS_CACHE_TTL_SECONDS),
            neg_ttl_seconds=int(settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS),
        )
        self._quote_provider: MarketDataProvider = self._select_quote_provider()
        # Two-gate capability convention: MARKET_DATA_PROVIDER=fmp selects
        # FMPProvider as the underlying object, but FMP_QUOTES_ENABLED /
        # FMP_BARS_ENABLED each independently gate whether it actually SERVES
        # quotes vs. bars (they are separate settings on purpose -- an
        # operator may want FMP fundamentals live while quotes/bars stay on
        # the incumbent path). Pre-resolve once what serves a call whose gate
        # is off -- the exact same Alpaca-if-keyed-else-yfinance default the
        # non-FMP branch below would have produced -- so
        # _effective_quote_provider / _effective_bars_provider don't
        # reconstruct a provider on every call. None when MARKET_DATA_PROVIDER
        # isn't 'fmp' (the overwhelmingly common case): no wasted work.
        self._default_quote_provider: Optional[MarketDataProvider] = (
            self._select_default_quote_provider()
            if isinstance(self._quote_provider, FMPProvider)
            else None
        )
        # Fundamentals source: Yahoo-derived statement engine (primary), raw
        # yfinance .info when FUNDAMENTALS_SOURCE=yfinance_info, or FMP when
        # FUNDAMENTALS_SOURCE=fmp. FMP_API_KEY being set is NEVER sufficient
        # on its own -- an explicit FUNDAMENTALS_SOURCE=fmp is required, so
        # adding the key for one feed (e.g. the analyst feed) can never
        # silently change what every valuation metric is computed from.
        src = (settings.FUNDAMENTALS_SOURCE or "yahoo").strip().lower()
        if src == "fmp":
            fmp_key = (getattr(settings, "FMP_API_KEY", None) or "").strip()
            if not fmp_key:
                logger.warning(
                    "MarketData: FUNDAMENTALS_SOURCE=fmp but FMP_API_KEY is not "
                    "set -- falling back to the default fundamentals provider "
                    "(Yahoo-derived statement engine) for this entire process. "
                    "Add FMP_API_KEY to .env to restore FMP as primary."
                )
                self._fundamentals_provider = self._select_default_fundamentals_provider()
            else:
                self._fundamentals_provider = FMPProvider(api_key=fmp_key)
        else:
            self._fundamentals_provider = self._select_default_fundamentals_provider(src)
        # Same pre-resolution as _default_quote_provider above, for the
        # independent FMP_FUNDAMENTALS_ENABLED gate.
        self._default_fundamentals_provider: Optional[MarketDataProvider] = (
            self._select_default_fundamentals_provider()
            if isinstance(self._fundamentals_provider, FMPProvider)
            else None
        )
        # Log startup banner once
        self._log_startup_banner()

    # ------------------------------------------------------------------
    # Provider selection
    # ------------------------------------------------------------------

    def _select_quote_provider(self) -> MarketDataProvider:
        explicit = (settings.MARKET_DATA_PROVIDER or "").strip().lower()

        # FMP is selected ONLY by an explicit MARKET_DATA_PROVIDER=fmp — never
        # by FMP_API_KEY's mere presence (unlike the Alpaca ladder just below,
        # which DOES auto-elect on key presence alone). This is deliberate:
        # an operator adding FMP_API_KEY to light up the analyst/earnings
        # diagnostic feeds must never silently have their quote/bars source
        # change underneath them.
        if explicit == "fmp":
            fmp_key = (getattr(settings, "FMP_API_KEY", None) or "").strip()
            if not fmp_key:
                logger.warning(
                    "MarketData: MARKET_DATA_PROVIDER=fmp but FMP_API_KEY is not "
                    "set -- falling back to the default quote/bars provider "
                    "(Alpaca if keyed, else yfinance) for this entire process. "
                    "Add FMP_API_KEY to .env to restore FMP as primary."
                )
                return self._select_default_quote_provider()
            provider = FMPProvider(api_key=fmp_key)
            variant = str(
                getattr(settings, "FMP_BARS_ADJUSTMENT", "dividend-adjusted")
                or "dividend-adjusted"
            )
            logger.info(
                "MarketData: MARKET_DATA_PROVIDER=fmp selected -- "
                "FMP_BARS_ADJUSTMENT=%r is the active daily-bars variant. "
                "Note this selects the FMPProvider OBJECT; whether it actually "
                "SERVES quotes/bars still depends on FMP_QUOTES_ENABLED / "
                "FMP_BARS_ENABLED respectively (see _log_startup_banner below "
                "for what's really active). Reminder: scripts/verify_fmp_bars.py "
                "should have been run and PASSED (max abs relative close diff "
                "< 1e-4 across KO/JNJ/AAPL) before FMP_BARS_ENABLED is set in a "
                "live environment -- an adjustment-convention mismatch corrupts "
                "every return series, indicator, GARCH fit and backtest "
                "PLAUSIBLY, without failing loudly.",
                variant,
            )
            return provider

        return self._select_default_quote_provider(explicit)

    def _select_default_quote_provider(self, explicit: str = "") -> MarketDataProvider:
        """The Alpaca-or-yfinance ladder, with no ``'fmp'`` branch.

        Two call sites: (1) :meth:`_select_quote_provider`, which passes the
        REAL ``MARKET_DATA_PROVIDER`` value through once it has already ruled
        out ``'fmp'`` — preserves byte-identical behavior (including the
        "unknown value" error) for every non-FMP config. (2) ``__init__``,
        with the default empty-string argument, to pre-resolve what should
        serve a call when ``MARKET_DATA_PROVIDER=fmp`` but the specific
        capability gate (``FMP_QUOTES_ENABLED`` / ``FMP_BARS_ENABLED``) is
        off — the same auto-select (Alpaca if keyed, else yfinance) an unset
        ``MARKET_DATA_PROVIDER`` would produce, since ``'fmp'`` itself is not
        a meaningful value here.
        """
        alpaca_key = (settings.ALPACA_API_KEY or "").strip()
        alpaca_secret = (settings.ALPACA_SECRET_KEY or "").strip()

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
            "Valid values: 'fmp', 'alpaca', 'yfinance'."
        )

    def _select_default_fundamentals_provider(self, src: str = "") -> MarketDataProvider:
        """The Yahoo-or-yfinance_info ladder, with no ``'fmp'`` branch.

        Same two-call-site pattern as :meth:`_select_default_quote_provider`:
        called from ``__init__``'s main fundamentals-selection branch with
        the real ``FUNDAMENTALS_SOURCE`` value (once ``'fmp'`` has already
        been ruled out), and again with the default empty-string argument to
        pre-resolve the ``FMP_FUNDAMENTALS_ENABLED``-off fallback.
        """
        if src == "yfinance_info":
            return YFinanceProvider()
        return YahooFundamentalsProvider()

    # ------------------------------------------------------------------
    # Effective-provider resolution (the FMP_*_ENABLED capability gates)
    # ------------------------------------------------------------------
    #
    # self._quote_provider / self._fundamentals_provider reflect PROVIDER
    # SELECTION (MARKET_DATA_PROVIDER / FUNDAMENTALS_SOURCE) only. When either
    # is an FMPProvider, whether it actually SERVES a given capability is a
    # second, independent decision -- FMP_QUOTES_ENABLED / FMP_BARS_ENABLED /
    # FMP_FUNDAMENTALS_ENABLED. The three properties below are the single
    # place that combines "which provider was selected" with "is this
    # specific capability's gate on", and everything else (get_latest_quote,
    # get_intraday_bars, get_fundamentals, the startup banner, and the
    # is_realtime/quote_source/source_name accessors) reads through them
    # rather than re-deriving the same logic in five places.
    #
    # getattr(self, "_quote_provider", None) (not a direct attribute read) is
    # deliberate: CompositeProvider is constructed via __new__ in some test
    # fixtures, which never run __init__ and so never set these attributes.

    @property
    def _effective_quote_provider(self) -> Optional[MarketDataProvider]:
        """Provider that actually serves ``get_latest_quote`` right now."""
        provider = getattr(self, "_quote_provider", None)
        if isinstance(provider, FMPProvider) and not bool(
            getattr(settings, "FMP_QUOTES_ENABLED", False)
        ):
            return getattr(self, "_default_quote_provider", None) or provider
        return provider

    @property
    def _effective_bars_provider(self) -> Optional[MarketDataProvider]:
        """Provider that actually serves ``get_intraday_bars`` right now.

        Independent of :attr:`_effective_quote_provider` — the same
        underlying ``self._quote_provider`` object can serve bars via FMP
        while quotes fall back to the incumbent path, or vice versa, since
        ``FMP_QUOTES_ENABLED`` and ``FMP_BARS_ENABLED`` are separate gates.
        """
        provider = getattr(self, "_quote_provider", None)
        if isinstance(provider, FMPProvider) and not bool(
            getattr(settings, "FMP_BARS_ENABLED", False)
        ):
            return getattr(self, "_default_quote_provider", None) or provider
        return provider

    @property
    def _effective_fundamentals_provider(self) -> Optional[MarketDataProvider]:
        """Provider that actually serves ``get_fundamentals`` right now."""
        provider = getattr(self, "_fundamentals_provider", None)
        if isinstance(provider, FMPProvider) and not bool(
            getattr(settings, "FMP_FUNDAMENTALS_ENABLED", False)
        ):
            return getattr(self, "_default_fundamentals_provider", None) or provider
        return provider

    def _log_startup_banner(self) -> None:
        # Read the provenance off the selected providers' class attributes
        # rather than isinstance-ing against a fixed list of classes: an
        # isinstance ladder silently mislabels any backend it doesn't know
        # about, and this banner is the operator's first (often only) look at
        # which source a run is actually using. Reads the EFFECTIVE providers
        # (not self._quote_provider/_fundamentals_provider directly) so the
        # banner never claims FMP is active for a capability whose
        # FMP_*_ENABLED gate is actually off.
        quote_provider = self._effective_quote_provider
        bars_provider = self._effective_bars_provider
        fundamentals_provider = self._effective_fundamentals_provider

        is_realtime = bool(getattr(quote_provider, "IS_REALTIME", False))
        quote_source = str(getattr(quote_provider, "SOURCE", "unknown"))
        bars_source = str(getattr(bars_provider, "SOURCE", "unknown"))
        latency_note = "real-time" if is_realtime else "delayed (unofficial)"
        fundamentals_note = str(getattr(fundamentals_provider, "SOURCE", "unknown"))

        if quote_source == bars_source:
            # The common case, including every pre-existing config: quotes
            # and bars come from the same backend. Wording unchanged from
            # before the capability gates existed.
            logger.info(
                "MarketData: quotes/bars via %s [source=%s, %s]; fundamentals via %s",
                type(quote_provider).__name__, quote_source, latency_note, fundamentals_note,
            )
        else:
            # MARKET_DATA_PROVIDER=fmp with only one of FMP_QUOTES_ENABLED /
            # FMP_BARS_ENABLED set -- quotes and bars now genuinely come from
            # different backends and must be reported separately rather than
            # claiming a single unified source.
            logger.info(
                "MarketData: quotes via %s [source=%s, %s]; bars via %s [source=%s]; "
                "fundamentals via %s",
                type(quote_provider).__name__, quote_source, latency_note,
                type(bars_provider).__name__, bars_source, fundamentals_note,
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_latest_quote(self, symbol: str) -> Quote:
        """Return a cached or freshly-fetched Quote for ``symbol``.

        When ``settings.MARKET_DATA_WS_ENABLED`` and a fresh WebSocket-
        delivered quote exists (see ``data/market_data_ws.py``), it is
        returned directly, bypassing both the REST call and its own TTL
        cache. Any lookup miss/stale-quote/import-failure falls straight
        through to the pre-existing REST+TTL-cache path below, completely
        unchanged -- this is a purely additive, best-effort supplement.

        The in-process TTL cache (default 30 s) prevents redundant network
        calls within a single refresh cycle.  Raises ``MarketDataError`` on
        provider failure.
        """
        sym = symbol.upper()

        try:
            from settings import settings as _settings
            if bool(getattr(_settings, "MARKET_DATA_WS_ENABLED", False)):
                from data.market_data_ws import get_ws_quote
                ws_quote = get_ws_quote(sym)
                if ws_quote is not None:
                    return Quote(
                        symbol=ws_quote.symbol,
                        price=ws_quote.price,
                        bid=ws_quote.bid,
                        ask=ws_quote.ask,
                        timestamp=ws_quote.timestamp,
                        is_stale=False,
                        source="alpaca_ws",
                    )
        except Exception as exc:  # noqa: BLE001 - WS lookup must never block a REST fallback
            logger.debug("CompositeProvider: WS quote lookup failed for %s (%s) -- using REST.", sym, exc)

        cached = self._cache.get(sym)
        if cached is not None:
            logger.debug("CompositeProvider: quote cache HIT for %s.", sym)
            return cached
        logger.debug("CompositeProvider: quote cache MISS for %s; fetching live.", sym)

        provider = self._effective_quote_provider
        if isinstance(provider, FMPProvider):
            quote = self._get_quote_via_fmp_chain(sym)
        else:
            # ORIGINAL PATH — byte-identical to pre-FMP behavior when
            # MARKET_DATA_PROVIDER isn't 'fmp'. Also reached when it IS 'fmp'
            # but FMP_QUOTES_ENABLED is off, in which case `provider` is the
            # pre-resolved default (self._default_quote_provider), not
            # self._quote_provider -- so this still correctly serves from
            # Alpaca/yfinance rather than ever calling into FMP.
            quote = provider.get_latest_quote(sym)
        self._cache.put(quote)

        try:
            from settings import settings as _settings
            if bool(getattr(_settings, "MARKET_DATA_LATENCY_TRACKING_ENABLED", False)):
                import market_data_latency

                market_data_latency.record_quote_latency(
                    sym, quote.source, quote.timestamp, quote.is_stale
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, never blocks a quote fetch
            logger.debug("CompositeProvider: latency sample write failed (non-critical): %s", exc)

        return quote

    def get_quotes_batch(self, symbols: List[str]) -> Dict[str, Quote]:
        """Bulk quote resolution for a whole symbol universe in as few
        network round-trips as possible (F6, docs/
        module_efficiency_redundancy_audit.md).

        Cache-first per symbol (the same TTL cache ``get_latest_quote`` uses
        — a symbol already fetched this cycle costs nothing here either),
        then ONE delegated batch call for every cache miss via
        ``self._effective_quote_provider.get_quotes_batch(...)`` — which
        resolves to ``FMPProvider``'s real ``/batch-quote`` override when FMP
        is the active provider, or the ABC's per-symbol-loop default
        otherwise (Alpaca/yfinance — no worse than today's manual loop, but
        centralized instead of re-implemented at every call site).

        Disclosed scope boundary, not a silent gap: unlike
        ``get_latest_quote``, this method does NOT consult the
        WebSocket-delivered-quote fast path (``data/market_data_ws.py``) and
        does NOT record per-symbol latency samples. Both are tied to
        single-symbol *display-freshness* semantics (a live single-quote
        view wanting the freshest possible tick); this method's callers
        (portfolio-wide risk/scenario calculations resolving spot prices for
        many tickers at once) do not need microsecond WS freshness, and
        instrumenting a bulk fetch with N synthetic per-symbol latency
        samples would misrepresent what was actually N/batch_size real
        network calls, not N real ones.
        """
        if not symbols:
            return {}
        upper_symbols = [s.upper() for s in symbols]
        out: Dict[str, Quote] = {}
        missing: List[str] = []
        for sym in upper_symbols:
            cached = self._cache.get(sym)
            if cached is not None:
                out[sym] = cached
            else:
                missing.append(sym)

        if missing:
            provider = self._effective_quote_provider
            fetched = provider.get_quotes_batch(missing)
            for sym, quote in fetched.items():
                self._cache.put(quote)
                out[sym.upper()] = quote

        return out

    def get_intraday_bars(
        self, symbol: str, lookback_days: int = 252, interval: str = "1d"
    ) -> pd.DataFrame:
        """Return OHLCV bars for the last ``lookback_days`` days.

        ``interval="1d"`` (default) is unchanged daily-resolution behavior;
        the shape is identical to ``DataEngine.fetch_technical_raw()`` so all
        downstream processing_engine / forecasting_engine code runs unchanged.
        ``interval="1h"`` is an opt-in hourly-resolution fetch (see
        ``settings.EXCURSION_INTRADAY_ENABLED``) — not consumed by the
        standard technical/forecasting pipeline, only by callers that
        explicitly request it (e.g. ``evaluation_engine.calculate_edge_ratio``).

        The result is cached in-process for ``MARKET_DATA_BARS_TTL_SECONDS``
        (default 300 s) keyed by ``(symbol, lookback_days, interval)`` so
        repeated requests within a single refresh cycle don't re-hit the
        network; the cache returns a defensive copy and never persists to disk.

        Raises ``MarketDataError`` on provider failure.
        """
        sym = symbol.upper()

        # Lazy-init for instances constructed via ``__new__`` (test fixtures).
        if not hasattr(self, "_bars_cache"):
            self._bars_cache = _BarsCache(
                ttl_seconds=int(settings.MARKET_DATA_BARS_TTL_SECONDS)
            )

        cached = self._bars_cache.get(sym, lookback_days, interval)
        if cached is not None:
            logger.debug(
                "CompositeProvider: bars cache HIT for %s (lookback=%d, interval=%s).",
                sym, lookback_days, interval,
            )
            return cached
        logger.debug(
            "CompositeProvider: bars cache MISS for %s (lookback=%d, interval=%s); fetching live.",
            sym, lookback_days, interval,
        )

        provider = self._effective_bars_provider
        if isinstance(provider, FMPProvider):
            bars = self._get_bars_via_fmp_chain(sym, lookback_days, interval)
        else:
            # ORIGINAL PATH — byte-identical to pre-FMP behavior when
            # MARKET_DATA_PROVIDER isn't 'fmp'. Also reached when it IS 'fmp'
            # but FMP_BARS_ENABLED is off (independent of FMP_QUOTES_ENABLED
            # -- see _effective_bars_provider), in which case `provider` is
            # the pre-resolved default, never FMP.
            bars = provider.get_intraday_bars(
                symbol=sym, lookback_days=lookback_days, interval=interval
            )
        self._bars_cache.put(sym, lookback_days, bars, interval)
        return bars

    def _build_fmp_fallback_tail(self) -> List[MarketDataProvider]:
        """Build the [AlpacaProvider?, YFinanceProvider] tail shared by the
        quote and bars FMP fallback chains, honoring ``FMP_FALLBACK_ENABLED``.

        Returns an empty list when ``settings.FMP_FALLBACK_ENABLED`` is
        ``False`` — the caller's chain then collapses to ``[FMPProvider]``
        only, and a primary failure propagates as ``MarketDataError`` with no
        fallback attempted. Alpaca is only appended when BOTH
        ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY`` are set; its
        construction is defensively wrapped (unlike
        ``YahooFundamentalsProvider``/``YFinanceProvider`` in the
        fundamentals chain, ``AlpacaProvider.__init__`` does real I/O-free
        but import-dependent work via ``alpaca-py`` and can raise
        ``ImportError``) so a broken/missing Alpaca install degrades to
        "skip Alpaca, keep yfinance" rather than crashing chain construction
        itself.
        """
        tail: List[MarketDataProvider] = []
        if not bool(getattr(settings, "FMP_FALLBACK_ENABLED", True)):
            return tail
        alpaca_key = (settings.ALPACA_API_KEY or "").strip()
        alpaca_secret = (settings.ALPACA_SECRET_KEY or "").strip()
        if alpaca_key and alpaca_secret:
            try:
                tail.append(AlpacaProvider(api_key=alpaca_key, secret_key=alpaca_secret))
            except Exception as exc:  # noqa: BLE001 — defensive: keep the chain alive
                logger.warning(
                    "CompositeProvider: AlpacaProvider unavailable for the FMP "
                    "quote/bars fallback chain (%s); skipping it.", exc,
                )
        tail.append(YFinanceProvider())
        return tail

    def _get_quote_via_fmp_chain(self, sym: str) -> Quote:
        """Ordered-chain quote fetch used ONLY when ``self._quote_provider``
        is an ``FMPProvider`` (i.e. ``MARKET_DATA_PROVIDER=fmp``).

        Chain: ``[FMPProvider, AlpacaProvider (only if both Alpaca keys are
        set), YFinanceProvider]``, unless ``settings.FMP_FALLBACK_ENABLED`` is
        ``False``, in which case the chain is ``[FMPProvider]`` only and a
        primary failure propagates as ``MarketDataError`` with no fallback
        attempted — the sibling of
        :meth:`_get_fundamentals_via_fmp_chain`, mirrored exactly.

        ``Quote.source`` already carries per-quote provenance (unlike the
        fundamentals dict, which needs a synthetic ``"_source"`` key bolted
        on), so the winning provider's own ``Quote`` is returned as-is. The
        module-level ``_PROVIDER_SERVE_COUNTS[("quote", source)]`` counter is
        bumped on success. Every fallback step logs a WARNING naming the
        provider, the symbol, and the exception — never DEBUG/INFO — so a
        silent fallback can never masquerade as success.
        """
        chain: List[MarketDataProvider] = [self._quote_provider]
        chain.extend(self._build_fmp_fallback_tail())

        last_exc: Optional[BaseException] = None
        for provider in chain:
            source = str(getattr(provider, "SOURCE", type(provider).__name__.lower()))
            try:
                quote = provider.get_latest_quote(sym)
            except Exception as exc:  # defense-in-depth; providers already dead-letter internally
                last_exc = exc
                logger.warning(
                    "CompositeProvider: quote provider %s raised for %s (%s); "
                    "trying next in chain.",
                    source, sym, exc,
                )
                continue
            _bump_provider_serve_count("quote", source)
            return quote

        raise MarketDataError(
            f"All quote providers in the FMP fallback chain failed for {sym}: {last_exc}"
        ) from last_exc

    def _get_bars_via_fmp_chain(
        self, sym: str, lookback_days: int, interval: str
    ) -> pd.DataFrame:
        """Ordered-chain bars fetch — the sibling of
        :meth:`_get_quote_via_fmp_chain`, same chain and same
        ``FMP_FALLBACK_ENABLED`` gate, used ONLY when ``self._quote_provider``
        is an ``FMPProvider``.

        Bars have no per-row source field (a documented limitation — see the
        plan's "Bars have no per-row source field" note), so observability
        relies entirely on ``_PROVIDER_SERVE_COUNTS[("bars", source)]`` plus
        the WARNING logged on every fallback step; there is no analogue to
        ``Quote.source`` or the fundamentals dict's ``"_source"`` key here.
        """
        chain: List[MarketDataProvider] = [self._quote_provider]
        chain.extend(self._build_fmp_fallback_tail())

        last_exc: Optional[BaseException] = None
        for provider in chain:
            source = str(getattr(provider, "SOURCE", type(provider).__name__.lower()))
            try:
                bars = provider.get_intraday_bars(
                    symbol=sym, lookback_days=lookback_days, interval=interval
                )
            except Exception as exc:  # defense-in-depth; providers already dead-letter internally
                last_exc = exc
                logger.warning(
                    "CompositeProvider: bars provider %s raised for %s (%s); "
                    "trying next in chain.",
                    source, sym, exc,
                )
                continue
            _bump_provider_serve_count("bars", source)
            return bars

        raise MarketDataError(
            f"All bars providers in the FMP fallback chain failed for {sym}: {last_exc}"
        ) from last_exc

    def _get_fundamentals_via_fmp_chain(self, sym: str) -> Dict[str, Any]:
        """Ordered-chain fundamentals fetch used ONLY when
        ``self._fundamentals_provider`` is an ``FMPProvider`` (i.e.
        ``FUNDAMENTALS_SOURCE=fmp``).

        Chain: ``[FMPProvider, YahooFundamentalsProvider, YFinanceProvider]``,
        unless ``settings.FMP_FALLBACK_ENABLED`` is ``False``, in which case
        the chain is ``[FMPProvider]`` only and a primary failure/empty
        result returns ``{}`` with no fallback attempted.

        The first provider to return a non-empty dict wins; before returning,
        the dict is tagged ``fund["_source"] = provider.SOURCE`` (per-response
        provenance — see ``data/historical_store.py::_source_name``) and the
        module-level ``_PROVIDER_SERVE_COUNTS[("fundamentals", source)]``
        counter is incremented. Every fallback step logs a WARNING naming the
        provider, the symbol, and the reason (not debug/info) so a silent
        fallback can never masquerade as success.
        """
        chain: List[MarketDataProvider] = [self._fundamentals_provider]
        if bool(getattr(settings, "FMP_FALLBACK_ENABLED", True)):
            chain.append(YahooFundamentalsProvider())
            chain.append(YFinanceProvider())

        for provider in chain:
            source = str(getattr(provider, "SOURCE", type(provider).__name__.lower()))
            try:
                fund = provider.get_fundamentals(sym) or {}
            except Exception as exc:  # defense-in-depth; providers already dead-letter internally
                logger.warning(
                    "CompositeProvider: fundamentals provider %s raised for %s "
                    "(%s); trying next in chain.",
                    source, sym, exc,
                )
                continue
            if fund:
                tagged = dict(fund)
                tagged["_source"] = source
                _bump_provider_serve_count("fundamentals", source)
                return tagged
            logger.warning(
                "CompositeProvider: fundamentals provider %s returned nothing "
                "for %s; trying next in chain.",
                source, sym,
            )
        return {}

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Return fundamental metrics shaped as a yfinance .info dict.

        Source priority depends on ``FUNDAMENTALS_SOURCE``:

        * **Default (``"yahoo"``) or ``"yfinance_info"``** — BYTE-IDENTICAL to
          pre-FMP behavior: the Yahoo-derived statement-computed engine (or
          raw yfinance ``.info`` when ``FUNDAMENTALS_SOURCE=yfinance_info``) →
          raw yfinance ``.info`` emergency fallback when the primary returns
          nothing. This branch's code is untouched by the FMP integration.
        * **``"fmp"``** — an ordered chain via
          :meth:`_get_fundamentals_via_fmp_chain`: FMP → Yahoo → yfinance
          (or FMP-only when ``FMP_FALLBACK_ENABLED=False``).

        Always returns a dict, never raises. Results — including empty dicts
        — are cached for ``FUNDAMENTALS_CACHE_TTL_SECONDS`` (default 6h) so
        the underlying payloads are not re-fetched within the window.
        """
        # Lazy-init for instances constructed via ``__new__`` (test fixtures).
        if not hasattr(self, "_fundamentals_cache"):
            self._fundamentals_cache = _FundamentalsCache(
                ttl_seconds=int(settings.FUNDAMENTALS_CACHE_TTL_SECONDS),
                neg_ttl_seconds=int(settings.FUNDAMENTALS_NEG_CACHE_TTL_SECONDS),
            )

        sym = symbol.upper()

        cached = self._fundamentals_cache.get(sym)
        if cached is not None:
            logger.debug("CompositeProvider: fundamentals cache HIT for %s.", sym)
            return cached
        logger.debug("CompositeProvider: fundamentals cache MISS for %s; fetching live.", sym)

        provider = self._effective_fundamentals_provider
        if isinstance(provider, FMPProvider):
            fund = self._get_fundamentals_via_fmp_chain(sym)
        else:
            # ORIGINAL PATH — byte-identical to pre-FMP behavior when
            # FUNDAMENTALS_SOURCE isn't 'fmp'. Untouched on purpose: existing
            # tests pin this exact log wording and the exact returned-dict
            # shape (no "_source" tagging here), and "flag-off is
            # byte-identical" is the single most important invariant of the
            # whole FMP integration. Also reached when FUNDAMENTALS_SOURCE IS
            # 'fmp' but FMP_FUNDAMENTALS_ENABLED is off, in which case
            # `provider` is the pre-resolved default (YahooFundamentalsProvider
            # or YFinanceProvider), never self._fundamentals_provider (FMP).
            fund = provider.get_fundamentals(sym) or {}
            if not fund:
                # emergency fallback to raw yfinance .info (keeps its own
                # dividendYield normalization)
                logger.warning(
                    "CompositeProvider: primary fundamentals provider (%s) returned "
                    "nothing for %s; falling back to raw yfinance .info.",
                    self.source_name, sym,
                )
                fund = YFinanceProvider().get_fundamentals(sym)
                if not fund:
                    logger.warning(
                        "CompositeProvider: yfinance .info fallback also returned "
                        "nothing for %s; caching empty result.", sym,
                    )
        self._fundamentals_cache.put(sym, fund)
        return fund

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def source_name(self) -> str:
        """Provider name string for the active fundamentals source.

        Downstream (e.g. ``data/historical_store.py``) reads this to label the
        provenance of cached fundamentals rows. Reads through
        :attr:`_effective_fundamentals_provider` so this never claims FMP
        when ``FUNDAMENTALS_SOURCE=fmp`` but ``FMP_FUNDAMENTALS_ENABLED`` is
        actually off.
        """
        provider = self._effective_fundamentals_provider
        return getattr(
            provider,
            "source_name",
            type(provider).__name__.lower(),
        )

    @property
    def is_realtime(self) -> bool:
        """True when the active quote provider delivers real-time data.

        Reads the provider's own ``IS_REALTIME`` class attribute rather than
        isinstance-ing against ``AlpacaProvider``. The old ternary defaulted
        every non-Alpaca backend to "delayed", which happened to be right, but
        the mirror-image accessor (``quote_source``) defaulted every non-Alpaca
        backend to the literal string ``"yfinance"`` — see below.

        Reads through :attr:`_effective_quote_provider` (not
        ``self._quote_provider`` directly) so this never claims FMP's
        real-time-ness when ``MARKET_DATA_PROVIDER=fmp`` but
        ``FMP_QUOTES_ENABLED`` is actually off; that property's own
        ``getattr(self, "_quote_provider", None)`` already handles
        ``CompositeProvider`` instances constructed via ``__new__`` in some
        tests (no ``_quote_provider`` assigned by ``__init__``).
        """
        return bool(getattr(self._effective_quote_provider, "IS_REALTIME", False))

    @property
    def quote_source(self) -> str:
        """Provider name string, e.g. "alpaca" or "yfinance".

        This string is dashboard / Google Sheet attribution, so it has to be
        the provider's own name and not a two-way guess. The previous
        implementation was a hardcoded ternary that reported ``"yfinance"``
        for anything that wasn't ``AlpacaProvider`` — correct only for as long
        as exactly two backends existed. Byte-identical for both of those:
        Alpaca → ``"alpaca"``, yfinance → ``"yfinance"``. Reads through
        :attr:`_effective_quote_provider` for the same reason as
        :attr:`is_realtime` above.
        """
        return str(getattr(self._effective_quote_provider, "SOURCE", "unknown"))

    def invalidate_quote(self, symbol: str) -> None:
        """Evict a symbol's quote from the TTL cache (e.g. after a fill)."""
        self._cache.invalidate(symbol.upper())

    def clear_quote_cache(self) -> None:
        """Wipe the entire in-process quote cache (e.g. on session restart)."""
        self._cache.clear()

    def invalidate_bars(self, symbol: str) -> None:
        """Evict a symbol's cached bars (all lookback windows) from the TTL cache."""
        if hasattr(self, "_bars_cache"):
            self._bars_cache.invalidate(symbol.upper())

    def clear_bars_cache(self) -> None:
        """Wipe the entire in-process bars cache (e.g. on session restart)."""
        if hasattr(self, "_bars_cache"):
            self._bars_cache.clear()

    def clear_fundamentals_cache(self) -> None:
        """Wipe the in-process fundamentals cache (e.g. on session restart)."""
        if hasattr(self, "_fundamentals_cache"):
            self._fundamentals_cache.clear()
        # Also reset the inner provider's caches so a forced refresh actually
        # re-issues the network calls. Both are guarded — harmless when the
        # active provider doesn't expose them (e.g. the legacy Finnhub
        # ``_cache`` or the Yahoo provider's SPY market-return cache).
        provider = getattr(self, "_fundamentals_provider", None)
        inner_cache = getattr(provider, "_cache", None)
        if inner_cache is not None:
            inner_cache.clear()
        clear_spy = getattr(provider, "clear_spy_cache", None)
        if callable(clear_spy):
            clear_spy()


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _isnan(v: float) -> bool:
    """Return True for float('nan') without importing math."""
    return v != v


# ---------------------------------------------------------------------------
# Provider serve-count telemetry
# ---------------------------------------------------------------------------
# Ground-truth "who actually served this response" counter, keyed
# (kind, source) e.g. ("fundamentals", "fmp") / ("fundamentals", "yahoo_computed").
# Generic across every provider kind on purpose — not FMP-specific — so it is
# equally useful (and testable) against today's Yahoo/yfinance fundamentals
# chain once quotes/bars grow their own chains in wave 2. Module-level and
# lock-guarded because data_engine.py calls get_fundamentals under an
# 8-thread pool.

_provider_serve_counts_lock = threading.Lock()
_PROVIDER_SERVE_COUNTS: Dict[Tuple[str, str], int] = {}


def _bump_provider_serve_count(kind: str, source: str) -> None:
    """Increment the ``(kind, source)`` serve counter by one (thread-safe)."""
    with _provider_serve_counts_lock:
        key = (kind, source)
        _PROVIDER_SERVE_COUNTS[key] = _PROVIDER_SERVE_COUNTS.get(key, 0) + 1


def get_provider_serve_counts() -> Dict[Tuple[str, str], int]:
    """Snapshot copy of every ``(kind, source) -> count`` serve counter.

    The ground-truth operator/test query for "did the chain actually fall
    back on me?" — complements ``data/historical_store.py::_source_name``'s
    per-response ``"_source"`` tagging with an in-process aggregate.
    """
    with _provider_serve_counts_lock:
        return dict(_PROVIDER_SERVE_COUNTS)


def reset_provider_serve_counts() -> None:
    """Clear every serve counter. Tests call this for isolation; a long-lived
    process never needs to (the counters are cheap and monotonic)."""
    with _provider_serve_counts_lock:
        _PROVIDER_SERVE_COUNTS.clear()


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


# ---------------------------------------------------------------------------
# Options Data Provider (Tiered/Injection Point)
# ---------------------------------------------------------------------------

class OptionsDataProvider(ABC):
    """Abstract base class for fetching options chain data."""
    @abstractmethod
    def fetch_options_chain(self, symbol: str, expiration: Optional[str] = None) -> Any:
        """Fetch option chain (if expiration provided) or expirations list."""
        pass


class YFinanceOptionsProvider(OptionsDataProvider):
    """Options provider backed by yfinance."""
    def fetch_options_chain(self, symbol: str, expiration: Optional[str] = None) -> Any:
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            if expiration is None:
                return list(t.options)
            else:
                return t.option_chain(expiration)
        except Exception as e:
            logger.error("YFinanceOptionsProvider fetch failed for %s (exp=%s): %s", symbol, expiration, e)
            return [] if expiration is None else None


class CompositeOptionsProvider(OptionsDataProvider):
    """Top-level options provider that routes to the configured backend.
    Currently uses YFinanceOptionsProvider, but structured to support swapping
    in other providers (e.g., Alpaca or FMP) in the future.
    """
    def __init__(self):
        # In the future, read env vars (e.g., OPTIONS_DATA_PROVIDER) to select provider
        self._provider = YFinanceOptionsProvider()

    def fetch_options_chain(self, symbol: str, expiration: Optional[str] = None) -> Any:
        return self._provider.fetch_options_chain(symbol, expiration)


_default_options_provider: Optional[CompositeOptionsProvider] = None

def get_options_provider() -> CompositeOptionsProvider:
    """Return the module-level CompositeOptionsProvider singleton."""
    global _default_options_provider
    if _default_options_provider is None:
        _default_options_provider = CompositeOptionsProvider()
    return _default_options_provider

def reset_options_provider() -> None:
    """Force-reset the options provider singleton."""
    global _default_options_provider
    _default_options_provider = None
