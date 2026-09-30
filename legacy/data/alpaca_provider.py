"""Archived 2026-09-30: AlpacaProvider, cut from data/market_data.py when Alpaca was
removed (operator decision). Not importable as-is: it relied on market_data.py
module globals (MarketDataProvider, Quote, MarketDataError, settings,
_WS_STREAMER). Restore by pasting back into data/market_data.py."""

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
        """Alpaca does not provide fundamentals; return empty (Yahoo/FMP handle this)."""
        return {}
