"""
tests/test_market_data.py
=========================
Offline unit tests for data/market_data.py.

All network I/O is monkeypatched.  The suite verifies:
  - Quote dataclass is frozen and fields are typed correctly
  - _QuoteCache honours TTL and eviction
  - AlpacaProvider shapes the bar DataFrame to the expected OHLCV contract
  - YFinanceProvider marks quotes is_stale=True unconditionally
  - YFinanceProvider raises MarketDataError on empty bar response
  - FinnhubProvider maps metric names to yfinance .info keys
  - FinnhubProvider degrades gracefully (empty dict) when key is absent
  - CompositeProvider selects Alpaca when keys are set
  - CompositeProvider selects yfinance when Alpaca keys are absent
  - CompositeProvider raises RuntimeError on unknown MARKET_DATA_PROVIDER value
  - CompositeProvider caches quotes and avoids redundant provider calls
  - CompositeProvider routes fundamentals to YahooFundamentalsProvider (primary)
    and falls back to raw yfinance .info when the primary returns {}
  - YahooFundamentalsProvider delegates math to compute_fundamentals and degrades
    to {} on any yfinance failure
  - reset_provider() forces re-initialisation on next get_provider() call
"""

import importlib
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers to reset the module-level singleton between tests
# ---------------------------------------------------------------------------

def _reload_module():
    """Re-import data.market_data to clear the _default_provider singleton."""
    import data.market_data as md
    md.reset_provider()
    return md


def _make_fake_quote(symbol: str, source: str):
    """A minimal, valid ``Quote`` for stubbing a chain member's return value
    in the FMP quote/bars fallback-chain tests below."""
    from data.market_data import Quote
    return Quote(
        symbol=symbol, price=100.0, bid=float("nan"), ask=float("nan"),
        timestamp=datetime.now(timezone.utc), is_stale=True, source=source,
    )


def _make_fake_bars_df() -> pd.DataFrame:
    """A minimal, valid OHLCV bars DataFrame (already in the ABC's bar-shape
    contract) for stubbing a chain member's return value."""
    idx = pd.date_range("2026-01-01", periods=5, freq="B")
    return pd.DataFrame(
        {
            "Open": [100.0] * 5, "High": [101.0] * 5, "Low": [99.0] * 5,
            "Close": [100.5] * 5, "Volume": [1000] * 5,
        },
        index=idx,
    )


class _FakeFastInfo:
    """Stand-in for yfinance's real ``FastInfo`` scraper object.

    Real ``FastInfo`` exposes ``last_price``/``previous_close`` ONLY as
    attributes -- its dict-style ``.get()`` recognizes just its camelCase
    keys (``lastPrice``/``previousClose``), so ``fi.get("last_price")``
    silently returns ``None`` on the real object even though it looks like
    it should work. A mock that only implements ``.get()`` with snake_case
    keys (the bug this class replaces) would mask that exact regression.
    ``shares`` has no underscore so the real object's ``.get()`` does
    recognize it verbatim -- reproduced here for the fundamentals path.
    """

    def __init__(self, last_price=None, previous_close=None, bid=None, ask=None, shares=None):
        self.last_price = last_price
        self.previous_close = previous_close
        self.bid = bid
        self.ask = ask
        self.shares = shares

    def get(self, key, default=None):
        return self.shares if key == "shares" else default


# ---------------------------------------------------------------------------
# 1. Quote dataclass
# ---------------------------------------------------------------------------

class TestQuote:
    def _make_quote(self, **overrides):
        from data.market_data import Quote
        defaults = dict(
            symbol="AAPL",
            price=175.0,
            bid=174.9,
            ask=175.1,
            timestamp=datetime.now(timezone.utc),
            is_stale=False,
            source="alpaca",
        )
        defaults.update(overrides)
        return Quote(**defaults)

    def test_frozen(self):
        from data.market_data import Quote
        q = self._make_quote()
        with pytest.raises(Exception):   # frozen dataclass raises FrozenInstanceError
            q.price = 0.0  # type: ignore[misc]

    def test_symbol_preserved(self):
        q = self._make_quote(symbol="MSFT")
        assert q.symbol == "MSFT"

    def test_source_field(self):
        q = self._make_quote(source="yfinance")
        assert q.source == "yfinance"

    def test_is_stale_bool(self):
        q = self._make_quote(is_stale=True)
        assert q.is_stale is True


# ---------------------------------------------------------------------------
# 2. _QuoteCache
# ---------------------------------------------------------------------------

class TestQuoteCache:
    def _make_quote(self, symbol="AAPL", price=100.0, is_stale=False, source="alpaca"):
        from data.market_data import Quote
        return Quote(
            symbol=symbol, price=price, bid=99.9, ask=100.1,
            timestamp=datetime.now(timezone.utc), is_stale=is_stale, source=source,
        )

    def test_miss_on_empty(self):
        from data.market_data import _QuoteCache
        cache = _QuoteCache(ttl_seconds=30)
        assert cache.get("AAPL") is None

    def test_put_then_get(self):
        from data.market_data import _QuoteCache
        cache = _QuoteCache(ttl_seconds=30)
        q = self._make_quote()
        cache.put(q)
        assert cache.get("AAPL") is q

    def test_ttl_expiry(self):
        from data.market_data import _QuoteCache
        cache = _QuoteCache(ttl_seconds=0.1)
        q = self._make_quote()
        cache.put(q)
        # Advance time past TTL
        time.sleep(0.15)
        assert cache.get("AAPL") is None

    def test_invalidate(self):
        from data.market_data import _QuoteCache
        cache = _QuoteCache(ttl_seconds=30)
        q = self._make_quote()
        cache.put(q)
        cache.invalidate("AAPL")
        assert cache.get("AAPL") is None

    def test_clear(self):
        from data.market_data import _QuoteCache
        cache = _QuoteCache(ttl_seconds=30)
        cache.put(self._make_quote("AAPL"))
        cache.put(self._make_quote("MSFT"))
        cache.clear()
        assert cache.get("AAPL") is None
        assert cache.get("MSFT") is None

    def test_multiple_symbols_independent(self):
        from data.market_data import _QuoteCache
        cache = _QuoteCache(ttl_seconds=30)
        qa = self._make_quote("AAPL", price=100.0)
        qb = self._make_quote("MSFT", price=200.0)
        cache.put(qa)
        cache.put(qb)
        assert cache.get("AAPL").price == 100.0
        assert cache.get("MSFT").price == 200.0


# ---------------------------------------------------------------------------
# 3. AlpacaProvider
# ---------------------------------------------------------------------------

class TestAlpacaProvider:
    """Tests AlpacaProvider with alpaca-py SDK fully mocked."""

    def _make_mock_client(self, bid=174.9, ask=175.1, ts_utc=None):
        """Build a mock StockHistoricalDataClient."""
        if ts_utc is None:
            ts_utc = datetime.now(timezone.utc)
        mock_quote = MagicMock()
        mock_quote.bid_price = bid
        mock_quote.ask_price = ask
        mock_quote.timestamp = ts_utc

        mock_client = MagicMock()
        mock_client.get_stock_latest_quote.return_value = {"AAPL": mock_quote}
        return mock_client

    def _make_bar_df(self, symbol="AAPL"):
        dates = pd.date_range("2025-01-01", periods=5, freq="B", tz="UTC")
        idx = pd.MultiIndex.from_tuples(
            [(symbol, d) for d in dates], names=["symbol", "timestamp"]
        )
        return pd.DataFrame(
            {"open": [100.0]*5, "high": [101.0]*5, "low": [99.0]*5,
             "close": [100.5]*5, "volume": [1000]*5},
            index=idx,
        )

    def test_get_latest_quote_source_alpaca(self):
        from data.market_data import AlpacaProvider
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60
        provider._client = self._make_mock_client()

        with patch("alpaca.data.requests.StockLatestQuoteRequest"):
            quote = provider.get_latest_quote("AAPL")

        assert quote.source == "alpaca"
        assert quote.symbol == "AAPL"
        assert quote.price == pytest.approx((174.9 + 175.1) / 2, abs=1e-6)

    def test_get_latest_quote_stale_when_old(self):
        from data.market_data import AlpacaProvider
        old_ts = datetime(2000, 1, 1, tzinfo=timezone.utc)
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60
        provider._client = self._make_mock_client(ts_utc=old_ts)

        with patch("alpaca.data.requests.StockLatestQuoteRequest"):
            quote = provider.get_latest_quote("AAPL")
        assert quote.is_stale is True

    def test_get_latest_quote_raises_market_data_error(self):
        from data.market_data import AlpacaProvider, MarketDataError
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60
        provider._client = MagicMock(
            get_stock_latest_quote=MagicMock(side_effect=RuntimeError("network error"))
        )
        with patch("alpaca.data.requests.StockLatestQuoteRequest"):
            with pytest.raises(MarketDataError):
                provider.get_latest_quote("AAPL")

    def test_get_intraday_bars_shape(self):
        from data.market_data import AlpacaProvider
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60

        mock_resp = MagicMock()
        mock_resp.df = self._make_bar_df("AAPL")
        provider._client = MagicMock(get_stock_bars=MagicMock(return_value=mock_resp))

        with patch("alpaca.data.requests.StockBarsRequest"), \
             patch("alpaca.data.timeframe.TimeFrame"):
            df = provider.get_intraday_bars("AAPL", lookback_days=5)

        assert set(["Open", "High", "Low", "Close", "Volume"]).issubset(df.columns)
        assert df.index.tz is None, "Index must be timezone-naive to match existing pipeline"
        assert df.index.is_monotonic_increasing

    def test_get_intraday_bars_hourly_interval_keeps_intraday_timestamp(self):
        """Phase-1 audit item B2: interval='1h' must not normalize the index
        to midnight (that would collapse same-day excursion resolution)."""
        from data.market_data import AlpacaProvider
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60

        mock_resp = MagicMock()
        mock_resp.df = self._make_bar_df("AAPL")
        provider._client = MagicMock(get_stock_bars=MagicMock(return_value=mock_resp))

        with patch("alpaca.data.requests.StockBarsRequest"), \
             patch("alpaca.data.timeframe.TimeFrame"):
            df = provider.get_intraday_bars("AAPL", lookback_days=5, interval="1h")

        assert set(["Open", "High", "Low", "Close", "Volume"]).issubset(df.columns)
        assert df.index.tz is None
        assert df.index.is_monotonic_increasing

    def test_get_intraday_bars_unsupported_interval_raises(self):
        from data.market_data import AlpacaProvider, MarketDataError
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60
        provider._client = MagicMock()
        with pytest.raises(MarketDataError):
            provider.get_intraday_bars("AAPL", lookback_days=5, interval="5m")

    def test_get_fundamentals_returns_empty(self):
        from data.market_data import AlpacaProvider
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60
        provider._client = MagicMock()
        assert provider.get_fundamentals("AAPL") == {}


# ---------------------------------------------------------------------------
# 3b. AlpacaProvider._build_client() -- 2026-08 HTTP-timeout-hardening fix.
# ---------------------------------------------------------------------------
# StockHistoricalDataClient subclasses the same alpaca-py RESTClient as
# execution/alpaca_broker.py's TradingClient, which exposes no timeout of
# its own (confirmed against the installed source) -- get_latest_quote /
# get_intraday_bars used to be able to block forever on a stalled
# connection. See data/alpaca_http.py's module docstring and
# tests/test_alpaca_http.py for full coverage of the adapter itself; these
# tests are spy assertions on the actual _build_client() call, not a
# re-statement of the docstring's claim.
#
# mount_timeout_adapter is imported LOCALLY inside _build_client()
# (``from data.alpaca_http import mount_timeout_adapter``), so it must be
# patched at its DEFINITION site (data.alpaca_http.mount_timeout_adapter) --
# verified empirically before writing these tests: patching
# data.alpaca_http.mount_timeout_adapter before constructing AlpacaProvider()
# does intercept the call, since the local ``from X import Y`` re-resolves
# X.Y at the moment _build_client() actually runs.
# ---------------------------------------------------------------------------

class TestAlpacaProviderBuildClientTimeoutWiring:
    def test_build_client_mounts_timeout_adapter_on_real_client_session(self):
        from settings import settings

        fake_client = MagicMock()
        with patch(
            "alpaca.data.historical.StockHistoricalDataClient",
            return_value=fake_client,
        ), patch("data.alpaca_http.mount_timeout_adapter") as _mount:
            from data.market_data import AlpacaProvider
            provider = AlpacaProvider(api_key="k", secret_key="s")

        assert provider._client is fake_client
        _mount.assert_called_once_with(
            fake_client._session, settings.ALPACA_REQUEST_TIMEOUT_SECONDS
        )

    def test_build_client_honors_a_monkeypatched_timeout_setting(self, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ALPACA_REQUEST_TIMEOUT_SECONDS", 42.0)

        fake_client = MagicMock()
        with patch(
            "alpaca.data.historical.StockHistoricalDataClient",
            return_value=fake_client,
        ), patch("data.alpaca_http.mount_timeout_adapter") as _mount:
            from data.market_data import AlpacaProvider
            AlpacaProvider(api_key="k", secret_key="s")

        _mount.assert_called_once_with(fake_client._session, 42.0)

    def test_build_client_mount_runs_for_real_against_the_client_session(self):
        """Unpatched mount_timeout_adapter: the real function must run
        synchronously inside _build_client() and mount a genuine
        _TimeoutHTTPAdapter on both schemes of the client's own session --
        not merely be scheduled or a no-op."""
        from settings import settings
        from data.alpaca_http import _TimeoutHTTPAdapter

        fake_client = MagicMock()
        with patch(
            "alpaca.data.historical.StockHistoricalDataClient",
            return_value=fake_client,
        ):
            from data.market_data import AlpacaProvider
            AlpacaProvider(api_key="k", secret_key="s")

        session = fake_client._session  # a MagicMock (fake_client is a MagicMock)
        assert session.mount.call_count == 2
        schemes = {call.args[0] for call in session.mount.call_args_list}
        assert schemes == {"https://", "http://"}
        for call in session.mount.call_args_list:
            adapter = call.args[1]
            assert isinstance(adapter, _TimeoutHTTPAdapter)
            assert adapter._timeout == settings.ALPACA_REQUEST_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# 4. YFinanceProvider
# ---------------------------------------------------------------------------

class TestYFinanceProvider:
    def _mock_fast_info(self, last_price=150.0, bid=149.9, ask=150.1):
        return _FakeFastInfo(last_price=last_price, bid=bid, ask=ask)

    def test_is_stale_always_true(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.fast_info = self._mock_fast_info()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            q = provider.get_latest_quote("AAPL")
        assert q.is_stale is True

    def test_source_is_yfinance(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.fast_info = self._mock_fast_info()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            q = provider.get_latest_quote("AAPL")
        assert q.source == "yfinance"

    def test_get_latest_quote_reads_price_from_fast_info_attributes(self):
        """Regression: FastInfo.get("last_price") always returns None on the
        real object (only its camelCase keys are dict-gettable); the price
        must come from attribute access instead, or every quote degrades to
        NaN and everything downstream (options matrix, etc.) goes blank."""
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.fast_info = self._mock_fast_info(last_price=150.0, bid=149.9, ask=150.1)
        with patch("yfinance.Ticker", return_value=mock_ticker):
            q = provider.get_latest_quote("AAPL")
        assert q.price == 150.0
        assert q.bid == 149.9
        assert q.ask == 150.1

    def test_get_latest_quote_falls_back_to_previous_close(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.fast_info = _FakeFastInfo(last_price=None, previous_close=142.0)
        with patch("yfinance.Ticker", return_value=mock_ticker):
            q = provider.get_latest_quote("AAPL")
        assert q.price == 142.0

    def test_get_latest_quote_price_is_nan_when_unavailable(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.fast_info = _FakeFastInfo(last_price=None, previous_close=None)
        with patch("yfinance.Ticker", return_value=mock_ticker):
            q = provider.get_latest_quote("AAPL")
        assert q.price != q.price  # NaN

    def test_quote_raises_market_data_error_on_exception(self):
        from data.market_data import YFinanceProvider, MarketDataError
        provider = YFinanceProvider()
        with patch("yfinance.Ticker", side_effect=RuntimeError("rate limit")):
            with pytest.raises(MarketDataError):
                provider.get_latest_quote("AAPL")

    def test_get_intraday_bars_correct_columns(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        dates = pd.date_range("2025-01-01", periods=5)
        df = pd.DataFrame(
            {"Open": [100.0]*5, "High": [101.0]*5, "Low": [99.0]*5,
             "Close": [100.5]*5, "Volume": [1000]*5},
            index=dates,
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_intraday_bars("AAPL", lookback_days=5)

        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert result.index.tz is None

    def test_get_intraday_bars_raises_on_empty(self):
        from data.market_data import YFinanceProvider, MarketDataError
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            with pytest.raises(MarketDataError):
                provider.get_intraday_bars("AAPL", lookback_days=5)

    def test_get_intraday_bars_hourly_interval_keeps_intraday_timestamp(self):
        """Phase-1 audit item B2: interval='1h' must not normalize the index
        to midnight, and must pass interval='1h' through to yfinance."""
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        timestamps = pd.date_range("2025-01-02 09:30", periods=5, freq="h")
        df = pd.DataFrame(
            {"Open": [100.0] * 5, "High": [101.0] * 5, "Low": [99.0] * 5,
             "Close": [100.5] * 5, "Volume": [1000] * 5},
            index=timestamps,
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_intraday_bars("AAPL", lookback_days=5, interval="1h")

        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert result.index.tz is None
        # Real intraday timestamps preserved (not collapsed to one row/day).
        assert result.index[0].hour == 9 and result.index[0].minute == 30
        mock_ticker.history.assert_called_once()
        _, call_kwargs = mock_ticker.history.call_args
        assert call_kwargs.get("interval") == "1h"

    def test_get_intraday_bars_unsupported_interval_raises(self):
        from data.market_data import YFinanceProvider, MarketDataError
        provider = YFinanceProvider()
        with pytest.raises(MarketDataError):
            provider.get_intraday_bars("AAPL", lookback_days=5, interval="5m")

    def test_get_fundamentals_returns_info_dict(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        mock_ticker = MagicMock()
        mock_ticker.info = {"trailingPE": 28.5, "shortName": "Apple Inc."}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            fund = provider.get_fundamentals("AAPL")
        assert fund.get("trailingPE") == 28.5

    def test_get_fundamentals_returns_empty_on_error(self):
        from data.market_data import YFinanceProvider
        provider = YFinanceProvider()
        with patch("yfinance.Ticker", side_effect=RuntimeError("rate limit")):
            fund = provider.get_fundamentals("AAPL")
        assert fund == {}


# ---------------------------------------------------------------------------
# 5. FinnhubProvider
# ---------------------------------------------------------------------------

class TestFinnhubProvider:
    def _mock_client(self, metrics: Dict[str, Any] = None, profile: Dict[str, Any] = None):
        client = MagicMock()
        client.company_basic_financials.return_value = {
            "metric": metrics or {"peBasicExclExtraTTM": 25.0, "pbQuarterly": 3.5}
        }
        client.company_profile2.return_value = profile or {
            "name": "Apple Inc.", "finnhubIndustry": "Technology"
        }
        client.quote.return_value = {"c": 175.0}
        return client

    def test_degrades_when_key_absent(self):
        from data.market_data import FinnhubProvider
        provider = FinnhubProvider(api_key=None)
        result = provider.get_fundamentals("AAPL")
        assert result == {}

    def test_maps_finnhub_to_yfinance_keys(self):
        from data.market_data import FinnhubProvider
        provider = FinnhubProvider.__new__(FinnhubProvider)
        provider._api_key = "test_key"
        provider._client = self._mock_client(
            metrics={"peBasicExclExtraTTM": 28.5, "pbQuarterly": 3.5,
                     "dividendYieldIndicatedAnnual": 0.52}
        )
        fund = provider.get_fundamentals("AAPL")
        assert "trailingPE" in fund
        assert fund["trailingPE"] == pytest.approx(28.5, abs=1e-6)
        # Dividend yield should be converted from percent to fraction
        assert fund["dividendYield"] == pytest.approx(0.0052, abs=1e-6)

    def test_returns_empty_on_network_error(self):
        from data.market_data import FinnhubProvider
        provider = FinnhubProvider.__new__(FinnhubProvider)
        provider._api_key = "key"
        provider._client = MagicMock(
            company_basic_financials=MagicMock(side_effect=RuntimeError("API error"))
        )
        result = provider.get_fundamentals("AAPL")
        assert result == {}

    def test_includes_company_name_and_sector(self):
        from data.market_data import FinnhubProvider
        provider = FinnhubProvider.__new__(FinnhubProvider)
        provider._api_key = "key"
        provider._client = self._mock_client(
            profile={"name": "Apple Inc.", "finnhubIndustry": "Technology"}
        )
        fund = provider.get_fundamentals("AAPL")
        assert fund.get("shortName") == "Apple Inc."
        assert fund.get("sector") == "Technology"


# ---------------------------------------------------------------------------
# 5b. YahooFundamentalsProvider (primary fundamentals source)
# ---------------------------------------------------------------------------

class TestYahooFundamentalsProvider:
    """YahooFundamentalsProvider is an I/O shell over compute_fundamentals.

    yfinance is not installed in this environment, so we register a stub
    ``yfinance`` module in sys.modules (the provider imports it lazily inside
    ``get_fundamentals`` / ``_spy_returns``).
    """

    def _annual(self):
        dates = pd.to_datetime(["2025-12-31", "2024-12-31"])
        return pd.DataFrame(
            {dates[0]: [200.0, 1000.0], dates[1]: [180.0, 900.0]},
            index=["Net Income", "Total Revenue"],
        )

    def _quarterly(self):
        dates = pd.to_datetime(
            ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
        )
        return pd.DataFrame(
            {d: [50.0, 250.0, 0.5] for d in dates},
            index=["Net Income", "Total Revenue", "Diluted EPS"],
        )

    def _balance_sheet(self):
        dates = pd.to_datetime(["2025-12-31", "2024-12-31"])
        return pd.DataFrame(
            {
                dates[0]: [1000.0, 1500.0, 800.0, 400.0],
                dates[1]: [900.0, 1400.0, 700.0, 350.0],
            },
            index=[
                "Stockholders Equity", "Total Debt",
                "Current Assets", "Current Liabilities",
            ],
        )

    def _dividends(self):
        return pd.Series(
            [1.0, 1.0, 1.0, 1.0],
            index=pd.to_datetime(
                ["2025-01-15", "2025-04-15", "2025-07-15", "2025-10-15"]
            ),
        )

    def _history(self):
        idx = pd.date_range("2024-01-01", periods=80, freq="B", tz="UTC")
        return pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(80)]}, index=idx)

    def _make_ticker(self):
        m = MagicMock()
        m.info = {
            "sector": "Technology",
            "shortName": "Apple Inc.",
            "longName": "Apple Inc.",
            "sharesOutstanding": 100.0,
        }
        m.fast_info = _FakeFastInfo(last_price=150.0, previous_close=149.0, shares=100.0)
        m.income_stmt = self._annual()
        m.quarterly_income_stmt = self._quarterly()
        m.balance_sheet = self._balance_sheet()
        m.quarterly_balance_sheet = self._balance_sheet()
        m.cashflow = pd.DataFrame()
        m.quarterly_cashflow = pd.DataFrame()
        m.dividends = self._dividends()
        m.institutional_holders = None
        m.history.return_value = self._history()
        return m

    def _install_yf(self, monkeypatch, ticker_factory):
        import types
        fake = types.ModuleType("yfinance")
        fake.Ticker = ticker_factory
        monkeypatch.setitem(sys.modules, "yfinance", fake)

    def test_source_constant(self):
        from data.market_data import YahooFundamentalsProvider
        assert YahooFundamentalsProvider.SOURCE == "yahoo_computed"
        assert YahooFundamentalsProvider().source_name == "yahoo_computed"

    def test_get_fundamentals_returns_dividend_yield_fraction(self, monkeypatch):
        from data.market_data import YahooFundamentalsProvider
        ticker = self._make_ticker()
        self._install_yf(monkeypatch, lambda symbol: ticker)

        provider = YahooFundamentalsProvider()
        fund = provider.get_fundamentals("AAPL")

        assert isinstance(fund, dict)
        # dividendYield emitted as a FRACTION (4.00 / 150 ~= 0.0267), not 2.67.
        assert fund["dividendYield"] == pytest.approx(4.0 / 150.0, abs=1e-4)
        assert fund["dividendYield"] < 1.0
        # Sanity: a couple of straight-through / computed values.
        assert fund["currentPrice"] == pytest.approx(150.0, abs=1e-9)
        assert fund["debtToEquity"] == pytest.approx(150.0, abs=1e-6)
        assert fund["shortName"] == "Apple Inc."

    def test_get_fundamentals_returns_empty_on_total_failure(self, monkeypatch):
        from data.market_data import YahooFundamentalsProvider
        # Ticker construction itself blows up -> dead-letter to {}.
        self._install_yf(
            monkeypatch,
            MagicMock(side_effect=RuntimeError("network down")),
        )
        provider = YahooFundamentalsProvider()
        assert provider.get_fundamentals("AAPL") == {}


# ---------------------------------------------------------------------------
# 6. CompositeProvider selection
# ---------------------------------------------------------------------------

class TestCompositeProviderSelection:
    """Verifies provider auto-selection based on settings.settings fields.

    Patches ``settings.settings`` directly (NOT ``os.environ``) -- pydantic-
    settings' ``env_file=".env"`` loading populates ``settings.settings``
    but does not copy values into the real ``os.environ``, so a test that
    only mutates ``os.environ`` would pass even if ``CompositeProvider``
    regressed back to reading ``os.environ.get(...)`` directly. See the
    2026-07 ``os.environ`` -> ``settings.settings`` fix (mirrors the
    ``signals/news_catalyst.py::build_finnhub_client`` precedent).
    """

    def _patched(self, **overrides):
        base = dict(
            ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None,
            MARKET_DATA_PROVIDER=None, FINNHUB_API_KEY=None,
            FUNDAMENTALS_SOURCE="yahoo",
        )
        base.update(overrides)
        return patch.multiple("settings.settings", **base)

    def test_selects_yfinance_when_no_keys(self):
        from data.market_data import CompositeProvider, YFinanceProvider
        with self._patched():
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, YFinanceProvider)

    def test_selects_alpaca_when_keys_present(self):
        from data.market_data import AlpacaProvider

        fake_client = MagicMock()
        with self._patched(
            ALPACA_API_KEY="key123", ALPACA_SECRET_KEY="sec456",
        ), patch(
            "alpaca.data.historical.StockHistoricalDataClient",
            return_value=fake_client,
        ):
            from data.market_data import CompositeProvider
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, AlpacaProvider)
        assert cp.is_realtime is True

    def test_explicit_yfinance_overrides_alpaca_keys(self):
        from data.market_data import CompositeProvider, YFinanceProvider
        with self._patched(
            ALPACA_API_KEY="key", ALPACA_SECRET_KEY="sec",
            MARKET_DATA_PROVIDER="yfinance",
        ):
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, YFinanceProvider)

    def test_unknown_provider_raises(self):
        with self._patched(MARKET_DATA_PROVIDER="bloomberg"):
            from data.market_data import CompositeProvider
            with pytest.raises(RuntimeError, match="Unknown MARKET_DATA_PROVIDER"):
                CompositeProvider()

    def test_is_realtime_false_for_yfinance(self):
        from data.market_data import CompositeProvider
        with self._patched():
            cp = CompositeProvider()
        assert cp.is_realtime is False

    def test_quote_source_string(self):
        from data.market_data import CompositeProvider
        with self._patched():
            cp = CompositeProvider()
        assert cp.quote_source == "yfinance"

    def test_settings_object_alone_selects_alpaca_without_os_environ(self):
        """Regression: provider selection must come from settings.settings,
        never os.environ. os.environ is deliberately blanked/wrong here so a
        regression back to os.environ.get() would silently fall through to
        yfinance (or crash) instead of honouring the settings.settings values
        an operator set only in .env."""
        from data.market_data import AlpacaProvider, CompositeProvider

        fake_client = MagicMock()
        with patch.dict(
            os.environ,
            {"MARKET_DATA_PROVIDER": "", "ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""},
            clear=False,
        ), self._patched(
            MARKET_DATA_PROVIDER="alpaca",
            ALPACA_API_KEY="key123", ALPACA_SECRET_KEY="sec456",
        ), patch(
            "alpaca.data.historical.StockHistoricalDataClient",
            return_value=fake_client,
        ):
            cp = CompositeProvider()

        assert isinstance(cp._quote_provider, AlpacaProvider)
        assert cp.is_realtime is True


# ---------------------------------------------------------------------------
# 6b. Provider provenance class attributes (SOURCE / IS_REALTIME)
# ---------------------------------------------------------------------------

class TestProviderProvenanceAttributes:
    """``CompositeProvider.is_realtime`` / ``.quote_source`` used to be two
    hardcoded ``isinstance(self._quote_provider, AlpacaProvider)`` ternaries.
    That is fine for exactly two backends and silently wrong for a third:
    ``quote_source`` would report the literal string ``"yfinance"`` for a quote
    served by ANY other provider, and that string is dashboard / Google Sheet
    attribution -- a mislabeling bug, not a cosmetic one.

    Both now read the provider's own ``SOURCE`` / ``IS_REALTIME`` class
    attributes. These two tests pin the EQUIVALENCE for the two backends that
    exist today, which is the whole safety claim of that refactor.
    """

    def _patched(self, **overrides):
        base = dict(
            ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None,
            MARKET_DATA_PROVIDER=None, FINNHUB_API_KEY=None,
            FUNDAMENTALS_SOURCE="yahoo",
        )
        base.update(overrides)
        return patch.multiple("settings.settings", **base)

    def test_alpaca_reports_realtime_true_and_source_alpaca(self):
        from data.market_data import AlpacaProvider, CompositeProvider

        fake_client = MagicMock()
        with self._patched(
            ALPACA_API_KEY="key123", ALPACA_SECRET_KEY="sec456",
        ), patch(
            "alpaca.data.historical.StockHistoricalDataClient",
            return_value=fake_client,
        ):
            cp = CompositeProvider()

        assert isinstance(cp._quote_provider, AlpacaProvider)
        assert (cp.is_realtime, cp.quote_source) == (True, "alpaca")

    def test_yfinance_reports_realtime_false_and_source_yfinance(self):
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched():
            cp = CompositeProvider()

        assert isinstance(cp._quote_provider, YFinanceProvider)
        assert (cp.is_realtime, cp.quote_source) == (False, "yfinance")

    def test_abc_declares_safe_defaults(self):
        """A provider that forgets to declare them must degrade to "unknown"
        and NOT-realtime -- never to a confident wrong attribution, and never
        to an optimistic real-time claim for a delayed feed."""
        from data.market_data import MarketDataProvider

        assert MarketDataProvider.SOURCE == "unknown"
        assert MarketDataProvider.IS_REALTIME is False

    def test_accessors_tolerate_a_ducktyped_provider_without_the_attributes(self):
        """``CompositeProvider`` is built via ``__new__`` in several tests and
        some providers (``YahooFundamentalsProvider``) are duck-typed rather
        than ABC subclasses, so both accessors must use ``getattr`` with a
        default rather than assuming the attribute exists."""
        from data.market_data import CompositeProvider

        cp = CompositeProvider.__new__(CompositeProvider)
        cp._quote_provider = SimpleNamespace()  # no SOURCE, no IS_REALTIME

        assert cp.is_realtime is False
        assert cp.quote_source == "unknown"


# ---------------------------------------------------------------------------
# 7. CompositeProvider caching behaviour
# ---------------------------------------------------------------------------

class TestCompositeProviderCache:
    def _make_cp(self, quote_ttl=30):
        """Return a CompositeProvider with a mocked YFinanceProvider."""
        from data.market_data import CompositeProvider, Quote, YFinanceProvider
        cp = CompositeProvider.__new__(CompositeProvider)
        from data.market_data import _QuoteCache, FinnhubProvider
        cp._cache = _QuoteCache(ttl_seconds=quote_ttl)

        mock_provider = MagicMock(spec=YFinanceProvider)
        mock_provider.get_latest_quote = MagicMock(
            return_value=Quote(
                symbol="AAPL", price=150.0, bid=149.9, ask=150.1,
                timestamp=datetime.now(timezone.utc), is_stale=True, source="yfinance",
            )
        )
        mock_provider.get_intraday_bars = MagicMock(
            return_value=pd.DataFrame(
                {"Open": [100.0], "High": [101.0], "Low": [99.0],
                 "Close": [100.5], "Volume": [1000]},
                index=pd.DatetimeIndex(["2025-01-01"]),
            )
        )
        mock_provider.get_fundamentals = MagicMock(return_value={})
        cp._quote_provider = mock_provider
        # Fundamentals now route to YahooFundamentalsProvider (primary), not Finnhub.
        from data.market_data import YahooFundamentalsProvider
        cp._fundamentals_provider = MagicMock(spec=YahooFundamentalsProvider)
        cp._fundamentals_provider.get_fundamentals.return_value = {}
        return cp, mock_provider

    def test_cache_deduplicates_calls(self):
        cp, mock_provider = self._make_cp()
        q1 = cp.get_latest_quote("AAPL")
        q2 = cp.get_latest_quote("AAPL")
        assert mock_provider.get_latest_quote.call_count == 1
        assert q1 is q2

    def test_logs_quote_cache_hit_and_miss(self, caplog):
        import logging
        cp, mock_provider = self._make_cp()
        with caplog.at_level(logging.DEBUG, logger="data.market_data"):
            cp.get_latest_quote("AAPL")  # miss
            cp.get_latest_quote("AAPL")  # hit
        messages = [r.message for r in caplog.records]
        assert any("quote cache MISS for AAPL" in m for m in messages)
        assert any("quote cache HIT for AAPL" in m for m in messages)

    def test_logs_bars_cache_hit_and_miss(self, caplog):
        import logging
        cp, mock_provider = self._make_cp()
        with caplog.at_level(logging.DEBUG, logger="data.market_data"):
            cp.get_intraday_bars("AAPL")  # miss
            cp.get_intraday_bars("AAPL")  # hit
        messages = [r.message for r in caplog.records]
        assert any("bars cache MISS for AAPL" in m for m in messages)
        assert any("bars cache HIT for AAPL" in m for m in messages)

    def test_invalidate_forces_refetch(self):
        cp, mock_provider = self._make_cp()
        cp.get_latest_quote("AAPL")
        cp.invalidate_quote("AAPL")
        cp.get_latest_quote("AAPL")
        assert mock_provider.get_latest_quote.call_count == 2

    def test_clear_forces_refetch(self):
        cp, mock_provider = self._make_cp()
        cp.get_latest_quote("AAPL")
        cp.clear_quote_cache()
        cp.get_latest_quote("AAPL")
        assert mock_provider.get_latest_quote.call_count == 2

    def test_fundamentals_fallback_to_yfinance_when_primary_empty(self):
        """When the primary (Yahoo) provider returns {}, the composite falls back
        to raw yfinance .info via YFinanceProvider.get_fundamentals."""
        from data.market_data import CompositeProvider
        cp = CompositeProvider.__new__(CompositeProvider)
        from data.market_data import (
            _QuoteCache,
            YahooFundamentalsProvider,
            YFinanceProvider,
        )
        cp._cache = _QuoteCache(ttl_seconds=30)
        cp._quote_provider = MagicMock()

        mock_primary = MagicMock(spec=YahooFundamentalsProvider)
        mock_primary.get_fundamentals.return_value = {}
        cp._fundamentals_provider = mock_primary

        yf_fund = {"trailingPE": 28.5}
        with patch.object(YFinanceProvider, "get_fundamentals", return_value=yf_fund):
            result = cp.get_fundamentals("AAPL")

        # Primary was consulted first, then the yfinance .info fallback fired.
        mock_primary.get_fundamentals.assert_called_once()
        assert result == yf_fund

    def test_fundamentals_fallback_logs_a_warning(self, caplog):
        """The silent emergency fallback to yfinance .info must be logged --
        an operator needs to know the primary fundamentals source failed."""
        import logging
        from data.market_data import CompositeProvider
        cp = CompositeProvider.__new__(CompositeProvider)
        from data.market_data import (
            _QuoteCache,
            _FundamentalsCache,
            YahooFundamentalsProvider,
            YFinanceProvider,
        )
        cp._cache = _QuoteCache(ttl_seconds=30)
        cp._quote_provider = MagicMock()
        cp._fundamentals_cache = _FundamentalsCache(ttl_seconds=21600, neg_ttl_seconds=900)

        mock_primary = MagicMock(spec=YahooFundamentalsProvider)
        mock_primary.get_fundamentals.return_value = {}
        cp._fundamentals_provider = mock_primary

        with caplog.at_level(logging.WARNING, logger="data.market_data"), \
             patch.object(YFinanceProvider, "get_fundamentals", return_value={"trailingPE": 28.5}):
            cp.get_fundamentals("AAPL")

        assert any("falling back to raw yfinance" in r.message for r in caplog.records)

    def test_fundamentals_uses_primary_when_non_empty(self):
        """When the primary (Yahoo) provider returns data, the composite uses it
        and never touches the yfinance .info fallback."""
        from data.market_data import CompositeProvider
        cp = CompositeProvider.__new__(CompositeProvider)
        from data.market_data import (
            _QuoteCache,
            YahooFundamentalsProvider,
            YFinanceProvider,
        )
        cp._cache = _QuoteCache(ttl_seconds=30)
        cp._quote_provider = MagicMock()

        primary_fund = {"dividendYield": 0.0267, "trailingPE": 30.0}
        mock_primary = MagicMock(spec=YahooFundamentalsProvider)
        mock_primary.get_fundamentals.return_value = primary_fund
        cp._fundamentals_provider = mock_primary

        with patch.object(
            YFinanceProvider, "get_fundamentals", return_value={"trailingPE": 999.0}
        ) as yf_fallback:
            result = cp.get_fundamentals("AAPL")

        assert result == primary_fund
        yf_fallback.assert_not_called()


class TestCompositeProviderLatencyTracking:
    """CompositeProvider.get_latest_quote's best-effort hook into
    market_data_latency.py — see that module's docstring and settings.py's
    MARKET_DATA_LATENCY_TRACKING_ENABLED for the full design rationale."""

    def _make_cp(self, quote_ttl=30):
        from data.market_data import CompositeProvider, Quote, YFinanceProvider, _QuoteCache
        cp = CompositeProvider.__new__(CompositeProvider)
        cp._cache = _QuoteCache(ttl_seconds=quote_ttl)
        mock_provider = MagicMock(spec=YFinanceProvider)
        mock_provider.get_latest_quote = MagicMock(
            return_value=Quote(
                symbol="AAPL", price=150.0, bid=149.9, ask=150.1,
                timestamp=datetime.now(timezone.utc), is_stale=True, source="yfinance",
            )
        )
        cp._quote_provider = mock_provider
        return cp, mock_provider

    def test_disabled_by_default_records_nothing(self):
        import market_data_latency
        market_data_latency.get_ring().clear()
        cp, _ = self._make_cp()

        with patch("settings.settings.MARKET_DATA_LATENCY_TRACKING_ENABLED", False):
            cp.get_latest_quote("AAPL")

        assert market_data_latency.get_ring().samples() == []

    def test_enabled_records_exactly_one_sample_per_real_fetch(self):
        import market_data_latency
        market_data_latency.get_ring().clear()
        cp, mock_provider = self._make_cp()

        with patch("settings.settings.MARKET_DATA_LATENCY_TRACKING_ENABLED", True):
            cp.get_latest_quote("AAPL")  # cache miss -> real fetch -> 1 sample
            cp.get_latest_quote("AAPL")  # cache hit -> no additional sample

        samples = market_data_latency.get_ring().samples()
        assert len(samples) == 1
        assert samples[0].symbol == "AAPL"
        assert samples[0].source == "yfinance"
        assert samples[0].is_stale is True
        assert mock_provider.get_latest_quote.call_count == 1

    def test_latency_write_failure_never_breaks_the_quote_fetch(self):
        """A raising recorder must never propagate into get_latest_quote's
        return value -- CONSTRAINT #6, best-effort per this hook's own
        try/except-log-and-continue shape (mirrors pipeline/production_steps
        .py's identical pattern for CapAuditStore.record_cap_events)."""
        import market_data_latency
        cp, _ = self._make_cp()

        with patch("settings.settings.MARKET_DATA_LATENCY_TRACKING_ENABLED", True):
            with patch.object(
                market_data_latency, "record_quote_latency", side_effect=RuntimeError("boom")
            ):
                quote = cp.get_latest_quote("AAPL")

        assert quote.symbol == "AAPL"
        assert quote.price == 150.0


# ---------------------------------------------------------------------------
# 8. get_provider / reset_provider singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_singleton_returns_same_instance(self):
        from data.market_data import get_provider, reset_provider
        reset_provider()
        with patch.multiple("settings.settings", ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None, FUNDAMENTALS_SOURCE="yahoo"):
            p1 = get_provider()
            p2 = get_provider()
        assert p1 is p2

    def test_reset_forces_new_instance(self):
        from data.market_data import get_provider, reset_provider
        reset_provider()
        with patch.multiple("settings.settings", ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None, FUNDAMENTALS_SOURCE="yahoo"):
            p1 = get_provider()
        reset_provider()
        with patch.multiple("settings.settings", ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None, FUNDAMENTALS_SOURCE="yahoo"):
            p2 = get_provider()
        assert p1 is not p2


# ---------------------------------------------------------------------------
# 9. Rate limiter + fundamentals cache (2026-06 Finnhub 429 mitigation)
# ---------------------------------------------------------------------------

class TestSlidingWindowRateLimiter:
    """Verifies the rate limiter blocks once the per-window budget is exhausted."""

    def test_first_n_calls_do_not_sleep(self, monkeypatch):
        from data.market_data import _SlidingWindowRateLimiter
        slept: list[float] = []
        monkeypatch.setattr("data.market_data.time.sleep", lambda s: slept.append(s))
        rl = _SlidingWindowRateLimiter(max_calls=3, window_seconds=60.0)
        for _ in range(3):
            rl.acquire()
        assert slept == []  # No sleep within budget

    def test_exceeds_budget_triggers_sleep(self, monkeypatch):
        from data.market_data import _SlidingWindowRateLimiter
        slept: list[float] = []
        monkeypatch.setattr("data.market_data.time.sleep", lambda s: slept.append(s))
        rl = _SlidingWindowRateLimiter(max_calls=2, window_seconds=60.0)
        rl.acquire()
        rl.acquire()
        rl.acquire()  # Should trigger a sleep
        assert len(slept) == 1
        assert slept[0] > 0


class TestFundamentalsCache:
    """Verifies positive AND empty fundamentals are cached with TTL semantics."""

    def test_cache_returns_empty_dict_on_miss(self):
        from data.market_data import _FundamentalsCache
        c = _FundamentalsCache(ttl_seconds=60)
        assert c.get("AAPL") is None

    def test_cache_round_trip(self):
        from data.market_data import _FundamentalsCache
        c = _FundamentalsCache(ttl_seconds=60)
        c.put("AAPL", {"trailingPE": 28.5})
        cached = c.get("AAPL")
        assert cached == {"trailingPE": 28.5}
        # Defensive copy: mutating the returned dict should not corrupt the cache.
        cached["trailingPE"] = 999.0
        assert c.get("AAPL") == {"trailingPE": 28.5}

    def test_cache_negative_entry(self):
        """An empty-dict response is a valid cache entry (negative caching)."""
        from data.market_data import _FundamentalsCache
        c = _FundamentalsCache(ttl_seconds=60)
        c.put("BAD", {})
        assert c.get("BAD") == {}  # Distinct from None (miss)

    def test_ttl_expiry(self, monkeypatch):
        from data.market_data import _FundamentalsCache
        c = _FundamentalsCache(ttl_seconds=1)
        c.put("AAPL", {"x": 1})
        # Fast-forward by patching time.monotonic.
        import data.market_data as md
        orig = md.time.monotonic()
        monkeypatch.setattr(md.time, "monotonic", lambda: orig + 2.0)
        assert c.get("AAPL") is None


class TestFinnhubRateLimitAndCache:
    """End-to-end: FinnhubProvider must cache and rate-limit per 2026-06 fix."""

    def _make_mock_client(self, *, raise_429: bool = False):
        client = MagicMock()
        if raise_429:
            # Mimic finnhub.exceptions.FinnhubAPIException's status_code attr
            exc = Exception("Too many requests.")
            exc.status_code = 429
            client.company_basic_financials.side_effect = exc
            client.quote.side_effect = exc
            client.company_profile2.side_effect = exc
        else:
            client.company_basic_financials.return_value = {
                "metric": {"peBasicExclExtraTTM": 28.5}
            }
            client.quote.return_value = {"c": 150.0}
            client.company_profile2.return_value = {
                "name": "Apple Inc", "finnhubIndustry": "Tech"
            }
        return client

    def test_repeated_calls_hit_cache_not_network(self, monkeypatch):
        from data.market_data import FinnhubProvider
        provider = FinnhubProvider(api_key="key", cache_ttl_seconds=3600)
        provider._client = self._make_mock_client()

        provider.get_fundamentals("AAPL")
        provider.get_fundamentals("AAPL")
        provider.get_fundamentals("AAPL")

        # Only the FIRST call should reach the network.
        assert provider._client.company_basic_financials.call_count == 1

    def test_429_is_caught_and_negative_cached(self, monkeypatch):
        """A 429 should be swallowed, return {}, and prevent re-hammer next call."""
        from data.market_data import FinnhubProvider
        monkeypatch.setattr("data.market_data.time.sleep", lambda s: None)

        provider = FinnhubProvider(api_key="key", cache_ttl_seconds=3600)
        provider._client = self._make_mock_client(raise_429=True)

        result = provider.get_fundamentals("BAC")
        assert result == {}  # Empty, never raises

        # Second call hits negative cache — zero additional network calls.
        first_call_count = provider._client.company_basic_financials.call_count
        provider.get_fundamentals("BAC")
        assert provider._client.company_basic_financials.call_count == first_call_count

    def test_rate_limiter_blocks_when_budget_exhausted(self, monkeypatch):
        """Verify the limiter is wired into FinnhubProvider, not just a free function."""
        from data.market_data import FinnhubProvider
        slept: list[float] = []
        monkeypatch.setattr("data.market_data.time.sleep", lambda s: slept.append(s))

        # 2 calls/min budget; each get_fundamentals makes up to 3 internal calls.
        provider = FinnhubProvider(api_key="key", cache_ttl_seconds=3600,
                                   rate_limit_per_min=2)
        provider._client = self._make_mock_client()

        provider.get_fundamentals("AAPL")
        # The third internal call within the window should have triggered a sleep.
        assert len(slept) >= 1


class TestCompositeProviderFundamentalsCache:
    """The composite-level cache prevents the fundamentals provider re-hammering.

    Fundamentals now come from ``YahooFundamentalsProvider`` (primary), not
    Finnhub. This test injects a call-counting fake onto the composite's
    ``_fundamentals_provider`` so it stays fully offline (no yfinance network)
    and proves the composite TTL cache deduplicates repeat lookups.
    """

    def test_composite_caches_final_result(self):
        from data.market_data import CompositeProvider
        with patch.multiple(
            "settings.settings",
            FINNHUB_API_KEY=None, ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None,
            MARKET_DATA_PROVIDER=None, FUNDAMENTALS_SOURCE="yahoo",
        ):
            cp = CompositeProvider()
            call_count = {"n": 0}

            class _FakePrimary:
                source_name = "yahoo_computed"

                def get_fundamentals(self, sym):  # noqa: ARG002
                    call_count["n"] += 1
                    return {"trailingPE": 28.5}

            cp._fundamentals_provider = _FakePrimary()

            cp.get_fundamentals("AAPL")
            cp.get_fundamentals("AAPL")
            cp.get_fundamentals("AAPL")

            assert call_count["n"] == 1  # Composite cache deduplicates


# ---------------------------------------------------------------------------
# 10. Robin_stocks output suppression (2026-06 Robinhood 400 noise mitigation)
# ---------------------------------------------------------------------------

class TestRobinhoodOutputSuppression:
    """Verify _suppress_rs_output redirects robin_stocks' stdout-style prints."""

    def test_suppress_swallows_print_to_helper_output(self):
        """robin_stocks prints HTTP errors via `print(msg, file=helper.get_output())`.

        With suppression active, that text must land in our buffer, not stdout.
        """
        from data.robinhood_client import _suppress_rs_output
        try:
            from robin_stocks.robinhood import helper as _rs_helper
        except Exception:  # pragma: no cover
            pytest.skip("robin_stocks not installed")

        with _suppress_rs_output() as buf:
            print("400 Client Error: Bad Request", file=_rs_helper.get_output())
        assert "400 Client Error" in buf.getvalue()

    def test_output_restored_after_context(self):
        """Ensure the prior output handle is restored even after suppression."""
        from data.robinhood_client import _suppress_rs_output
        try:
            from robin_stocks.robinhood import helper as _rs_helper
        except Exception:  # pragma: no cover
            pytest.skip("robin_stocks not installed")

        original = _rs_helper.get_output()
        with _suppress_rs_output():
            assert _rs_helper.get_output() is not original
        assert _rs_helper.get_output() is original


# ---------------------------------------------------------------------------
# 11. CompositeProvider config sourced from settings.settings, not os.environ
#     (2026-07 fix -- mirrors signals/news_catalyst.py::build_finnhub_client
#     and prompt_registry/registry.py's precedent: pydantic-settings'
#     env_file=".env" loading populates settings.settings directly, NOT the
#     real os.environ, so every knob CompositeProvider reads must come from
#     settings.settings.X. Each test below patches settings.settings ONLY
#     (never os.environ) with a value that differs from the hard-coded
#     fallback default, so a regression back to os.environ.get(...) would
#     make the assertion observe the stale default instead and fail.
# ---------------------------------------------------------------------------

class TestCompositeProviderSettingsWiring:
    def _patched(self, **overrides):
        base = dict(
            ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None,
            FUNDAMENTALS_SOURCE="yahoo",
        )
        base.update(overrides)
        return patch.multiple("settings.settings", **base)

    def test_quote_ttl_sourced_from_settings(self):
        from data.market_data import CompositeProvider
        with self._patched(MARKET_DATA_QUOTE_TTL_SECONDS=77):
            cp = CompositeProvider()
        assert cp._cache._ttl == 77

    def test_bars_ttl_sourced_from_settings(self):
        from data.market_data import CompositeProvider
        with self._patched(MARKET_DATA_BARS_TTL_SECONDS=123):
            cp = CompositeProvider()
        assert cp._bars_cache._ttl == 123

    def test_bars_ttl_lazy_init_fallback_sourced_from_settings(self):
        """get_intraday_bars' __new__-fixture lazy-init branch must also read
        settings.settings, not just __init__'s primary path."""
        from data.market_data import CompositeProvider, YFinanceProvider
        with self._patched(MARKET_DATA_BARS_TTL_SECONDS=456):
            cp = CompositeProvider.__new__(CompositeProvider)
            cp._quote_provider = MagicMock(spec=YFinanceProvider)
            cp._quote_provider.get_intraday_bars.return_value = pd.DataFrame(
                {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                index=pd.DatetimeIndex(["2025-01-01"]),
            )
            cp.get_intraday_bars("AAPL")
        assert cp._bars_cache._ttl == 456

    def test_fundamentals_cache_ttls_sourced_from_settings(self):
        from data.market_data import CompositeProvider
        with self._patched(
            FUNDAMENTALS_CACHE_TTL_SECONDS=5000, FUNDAMENTALS_NEG_CACHE_TTL_SECONDS=50,
        ):
            cp = CompositeProvider()
        assert cp._fundamentals_cache._ttl == 5000
        assert cp._fundamentals_cache._neg_ttl == 50

    def test_fundamentals_cache_ttls_lazy_init_fallback_sourced_from_settings(self):
        """get_fundamentals' __new__-fixture lazy-init branch must also read
        settings.settings, not just __init__'s primary path."""
        from data.market_data import CompositeProvider, YahooFundamentalsProvider
        with self._patched(
            FUNDAMENTALS_CACHE_TTL_SECONDS=6000, FUNDAMENTALS_NEG_CACHE_TTL_SECONDS=60,
        ):
            cp = CompositeProvider.__new__(CompositeProvider)
            cp._quote_provider = MagicMock()
            cp._fundamentals_provider = MagicMock(spec=YahooFundamentalsProvider)
            cp._fundamentals_provider.get_fundamentals.return_value = {"trailingPE": 1.0}
            cp.get_fundamentals("AAPL")
        assert cp._fundamentals_cache._ttl == 6000
        assert cp._fundamentals_cache._neg_ttl == 60

    def test_fundamentals_source_yfinance_info_sourced_from_settings(self):
        from data.market_data import CompositeProvider, YFinanceProvider
        with self._patched(FUNDAMENTALS_SOURCE="yfinance_info"):
            cp = CompositeProvider()
        assert isinstance(cp._fundamentals_provider, YFinanceProvider)

    def test_fundamentals_source_yahoo_sourced_from_settings(self):
        from data.market_data import CompositeProvider, YahooFundamentalsProvider
        with self._patched(FUNDAMENTALS_SOURCE="yahoo"):
            cp = CompositeProvider()
        assert isinstance(cp._fundamentals_provider, YahooFundamentalsProvider)

    def test_beta_lookback_days_sourced_from_settings(self):
        from data.market_data import YahooFundamentalsProvider
        with patch("settings.settings.BETA_LOOKBACK_DAYS", 90):
            assert YahooFundamentalsProvider._beta_period() == "3mo"
        with patch("settings.settings.BETA_LOOKBACK_DAYS", 1260):
            assert YahooFundamentalsProvider._beta_period() == "5y"


# ---------------------------------------------------------------------------
# 12. FMP fundamentals wiring: FUNDAMENTALS_SOURCE=fmp selection + the
#     ordered fallback chain in CompositeProvider.get_fundamentals (wave 1).
#     The pure mapping layer (data/fmp_fundamentals.py) and FMPProvider's own
#     I/O-shell behavior have their own dedicated test files
#     (tests/test_fmp_fundamentals.py, tests/test_fmp_provider.py); this
#     class only covers CompositeProvider's SELECTION and CHAIN logic.
# ---------------------------------------------------------------------------

class TestFMPFundamentalsChain:
    """The single most important invariant here: with FUNDAMENTALS_SOURCE at
    its default, the pre-existing Yahoo -> yfinance chain is BYTE-IDENTICAL
    to before FMP existed, even when FMP_API_KEY is set and FMP is fully
    configured to fail. FMP_API_KEY alone must never elect FMP."""

    @pytest.fixture(autouse=True)
    def _reset_serve_counts(self):
        from data.market_data import reset_provider_serve_counts
        reset_provider_serve_counts()
        yield
        reset_provider_serve_counts()

    def _patched(self, **overrides):
        base = dict(
            ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None,
            FMP_API_KEY=None, FUNDAMENTALS_SOURCE="yahoo", FMP_FALLBACK_ENABLED=True,
            # Defaults ON here so every existing "FMP actually serves" test
            # below keeps exercising that path -- FMP_FUNDAMENTALS_ENABLED is
            # the independent capability gate wired in on top of
            # FUNDAMENTALS_SOURCE=fmp (see TestFMPCapabilityGates for
            # coverage of the gate being off).
            FMP_FUNDAMENTALS_ENABLED=True,
        )
        base.update(overrides)
        return patch.multiple("settings.settings", **base)

    # -- 1. Flag-off byte-identical -----------------------------------
    def test_flag_off_fmp_key_set_but_source_not_fmp_selects_yahoo(self):
        """FMP_API_KEY alone must NEVER elect FMP -- FUNDAMENTALS_SOURCE must
        ALSO be explicitly 'fmp' (the two-gate convention)."""
        from data.market_data import CompositeProvider, YahooFundamentalsProvider

        with self._patched(FMP_API_KEY="a-real-looking-key", FUNDAMENTALS_SOURCE="yahoo"):
            cp = CompositeProvider()
        assert isinstance(cp._fundamentals_provider, YahooFundamentalsProvider)

    def test_flag_off_get_fundamentals_never_touches_fmp_network(self):
        from data.market_data import CompositeProvider, YahooFundamentalsProvider

        # The settings patch stays open across BOTH construction and the
        # get_fundamentals() call: FMP_FALLBACK_ENABLED (irrelevant on this
        # branch, but the general pattern below relies on it) and the
        # provider-selection settings are read at different times.
        with self._patched(FMP_API_KEY="a-real-looking-key", FUNDAMENTALS_SOURCE="yahoo"):
            cp = CompositeProvider()
            with patch("data.fmp_client.requests.get") as mock_get, \
                 patch.object(
                     YahooFundamentalsProvider, "get_fundamentals",
                     return_value={"trailingPE": 1.0},
                 ):
                cp.get_fundamentals("AAPL")
            mock_get.assert_not_called()

    # -- 2. FUNDAMENTALS_SOURCE=fmp without a key -> graceful fallback ---
    def test_fmp_source_without_api_key_falls_back_to_yahoo(self, caplog):
        import logging
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY=None):
            with caplog.at_level(logging.WARNING, logger="data.market_data"):
                cp = CompositeProvider()
        assert not isinstance(cp._fundamentals_provider, FMPProvider)
        assert "falling back to the default fundamentals provider" in caplog.text

    def test_fmp_source_with_blank_api_key_also_falls_back(self, caplog):
        import logging
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="   "):
            with caplog.at_level(logging.WARNING, logger="data.market_data"):
                cp = CompositeProvider()
        assert not isinstance(cp._fundamentals_provider, FMPProvider)
        assert "FMP_API_KEY is not set" in caplog.text

    def test_fmp_source_with_key_selects_fmp_provider(self):
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
        assert isinstance(cp._fundamentals_provider, FMPProvider)

    # -- 3. FMP empty -> Yahoo serves, tagged, counted, WARNING logged --
    def test_fmp_empty_falls_back_to_yahoo_then_serves_and_counts(self, caplog):
        import logging
        from data.market_data import (
            CompositeProvider,
            FMPProvider,
            YahooFundamentalsProvider,
            get_provider_serve_counts,
        )
        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()

            yahoo_fund = {"trailingPE": 28.5}
            with patch.object(FMPProvider, "get_fundamentals", return_value={}), \
                 patch.object(YahooFundamentalsProvider, "get_fundamentals", return_value=yahoo_fund), \
                 caplog.at_level(logging.WARNING, logger="data.market_data"):
                out = cp.get_fundamentals("AAPL")

        assert out["_source"] == "yahoo_computed"
        assert out["trailingPE"] == 28.5
        assert any(
            r.levelno == logging.WARNING and "returned nothing for AAPL" in r.message
            for r in caplog.records
        )
        assert get_provider_serve_counts()[("fundamentals", "yahoo_computed")] == 1

    # -- 4. All three chain members empty -> {} cached at negative TTL --
    def test_all_three_chain_members_empty_returns_empty_and_caches_negative(self):
        from data.market_data import (
            CompositeProvider,
            FMPProvider,
            YahooFundamentalsProvider,
            YFinanceProvider,
        )
        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()

            with patch.object(FMPProvider, "get_fundamentals", return_value={}), \
                 patch.object(YahooFundamentalsProvider, "get_fundamentals", return_value={}), \
                 patch.object(YFinanceProvider, "get_fundamentals", return_value={}):
                out = cp.get_fundamentals("AAPL")
            assert out == {}

            # Cached at the negative TTL -- a second call hits the cache,
            # never re-consulting any chain member.
            with patch.object(FMPProvider, "get_fundamentals", return_value={}) as fmp_mock, \
                 patch.object(YahooFundamentalsProvider, "get_fundamentals", return_value={}) as yahoo_mock, \
                 patch.object(YFinanceProvider, "get_fundamentals", return_value={}) as yf_mock:
                cp.get_fundamentals("AAPL")
            fmp_mock.assert_not_called()
            yahoo_mock.assert_not_called()
            yf_mock.assert_not_called()

    # -- 5. FMP_FALLBACK_ENABLED=False -> chain length 1 ----------------
    def test_fallback_disabled_chain_is_fmp_only(self):
        from data.market_data import CompositeProvider, FMPProvider, YahooFundamentalsProvider
        with self._patched(
            FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()

            with patch.object(FMPProvider, "get_fundamentals", return_value={}), \
                 patch.object(
                     YahooFundamentalsProvider, "get_fundamentals",
                     return_value={"trailingPE": 1.0},
                 ) as yahoo_mock:
                out = cp.get_fundamentals("AAPL")

            assert out == {}
            yahoo_mock.assert_not_called()

    def test_fallback_disabled_but_fmp_succeeds_still_serves_and_tags(self):
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(
            FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()

            with patch.object(FMPProvider, "get_fundamentals", return_value={"trailingPE": 9.0}):
                out = cp.get_fundamentals("AAPL")
            assert out["trailingPE"] == 9.0
            assert out["_source"] == "fmp"

    # -- 6. Existing Yahoo -> yfinance chain completely untouched -------
    def test_default_config_chain_untouched_even_with_fmp_fully_configured(self):
        """Regression guard: even with FMP fully configured (a real-looking
        key present) but FUNDAMENTALS_SOURCE left at its default, the
        existing 2-element Yahoo -> yfinance chain must be exactly what it
        is today -- FMP must never appear in it, and no FMP network call is
        ever attempted."""
        from data.market_data import CompositeProvider, YahooFundamentalsProvider, YFinanceProvider

        with self._patched(FMP_API_KEY="a-real-looking-key", FUNDAMENTALS_SOURCE="yahoo"):
            cp = CompositeProvider()

            with patch.object(YahooFundamentalsProvider, "get_fundamentals", return_value={}), \
                 patch.object(
                     YFinanceProvider, "get_fundamentals",
                     return_value={"trailingPE": 2.0},
                 ) as yf_mock, \
                 patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_fundamentals("AAPL")

            assert out == {"trailingPE": 2.0}
            yf_mock.assert_called_once()
            fmp_get_mock.assert_not_called()
            # No "_source" tagging on the legacy path -- the returned dict is
            # exactly what YFinanceProvider.get_fundamentals returned, byte
            # for byte (existing tests pin this same no-extra-keys contract).
            assert "_source" not in out

    def test_default_config_yfinance_info_source_also_untouched(self):
        """The other pre-existing non-FMP branch (FUNDAMENTALS_SOURCE=
        yfinance_info) must be equally unaffected by FMP's existence."""
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched(FMP_API_KEY="a-real-looking-key", FUNDAMENTALS_SOURCE="yfinance_info"):
            cp = CompositeProvider()
            assert isinstance(cp._fundamentals_provider, YFinanceProvider)

            with patch.object(
                YFinanceProvider, "get_fundamentals", return_value={"trailingPE": 3.0},
            ), patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_fundamentals("AAPL")
            assert out == {"trailingPE": 3.0}
            fmp_get_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 10. FMP quote/bars fallback chain (wave 2).
# ---------------------------------------------------------------------------

class TestFMPQuoteBarsChain:
    """The single most important invariant here: with MARKET_DATA_PROVIDER at
    its default, the pre-existing Alpaca/yfinance quote/bars path is
    BYTE-IDENTICAL to before FMP existed, even when FMP_API_KEY is set and
    FMP is fully configured to fail. FMP_API_KEY alone must never elect FMP.
    Mirrors ``TestFMPFundamentalsChain``'s structure exactly, one level up
    the stack (quotes/bars instead of fundamentals)."""

    @pytest.fixture(autouse=True)
    def _reset_serve_counts(self):
        from data.market_data import reset_provider_serve_counts
        reset_provider_serve_counts()
        yield
        reset_provider_serve_counts()

    def _patched(self, **overrides):
        base = dict(
            ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None,
            FMP_API_KEY=None, FUNDAMENTALS_SOURCE="yahoo", FMP_FALLBACK_ENABLED=True,
            # Defaults ON here so every existing "FMP actually serves" test
            # below keeps exercising that path -- FMP_QUOTES_ENABLED /
            # FMP_BARS_ENABLED are the independent capability gates wired in
            # on top of MARKET_DATA_PROVIDER=fmp (see
            # TestFMPCapabilityGates for coverage of each gate being off,
            # including independently of one another).
            FMP_QUOTES_ENABLED=True, FMP_BARS_ENABLED=True,
        )
        base.update(overrides)
        return patch.multiple("settings.settings", **base)

    # -- 1. Flag-off byte-identical -------------------------------------
    def test_flag_off_fmp_key_set_but_provider_not_fmp_selects_yfinance(self):
        """FMP_API_KEY alone must NEVER elect FMP -- MARKET_DATA_PROVIDER
        must ALSO be explicitly 'fmp' (the two-gate convention)."""
        from data.market_data import CompositeProvider, YFinanceProvider
        with self._patched(FMP_API_KEY="a-real-looking-key", MARKET_DATA_PROVIDER=None):
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, YFinanceProvider)

    def test_flag_off_never_touches_fmp_network(self):
        from data.market_data import CompositeProvider, YFinanceProvider
        with self._patched(FMP_API_KEY="a-real-looking-key", MARKET_DATA_PROVIDER=None):
            cp = CompositeProvider()
            with patch("data.fmp_client.requests.get") as mock_get, \
                 patch.object(
                     YFinanceProvider, "get_latest_quote",
                     return_value=_make_fake_quote("AAPL", "yfinance"),
                 ), \
                 patch.object(
                     YFinanceProvider, "get_intraday_bars",
                     return_value=_make_fake_bars_df(),
                 ):
                cp.get_latest_quote("AAPL")
                cp.get_intraday_bars("AAPL")
            mock_get.assert_not_called()

    # -- 2. MARKET_DATA_PROVIDER=fmp without a key -> graceful fallback --
    def test_fmp_provider_without_api_key_falls_back_to_default(self, caplog):
        import logging
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY=None):
            with caplog.at_level(logging.WARNING, logger="data.market_data"):
                cp = CompositeProvider()
        assert not isinstance(cp._quote_provider, FMPProvider)
        assert "falling back to the default quote/bars provider" in caplog.text

    def test_fmp_provider_with_blank_api_key_also_falls_back(self, caplog):
        import logging
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="   "):
            with caplog.at_level(logging.WARNING, logger="data.market_data"):
                cp = CompositeProvider()
        assert not isinstance(cp._quote_provider, FMPProvider)
        assert "FMP_API_KEY is not set" in caplog.text

    def test_fmp_provider_with_key_selects_fmp_provider(self):
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, FMPProvider)

    def test_fmp_selection_logs_info_reminder_about_verify_script(self, caplog):
        """Deliverable 5: an INFO startup line naming the active
        FMP_BARS_ADJUSTMENT variant and a reminder that
        scripts/verify_fmp_bars.py should have been run and passed."""
        import logging
        from data.market_data import CompositeProvider
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"), \
             caplog.at_level(logging.INFO, logger="data.market_data"):
            CompositeProvider()
        assert any(
            "verify_fmp_bars.py" in r.message and "FMP_BARS_ADJUSTMENT" in r.message
            for r in caplog.records
        )

    # -- 3. FMP quote/bars raise -> yfinance serves, counted, WARNING ----
    def test_fmp_quote_failure_falls_back_to_yfinance_then_serves_and_counts(self, caplog):
        import logging
        from data.market_data import (
            CompositeProvider, FMPProvider, YFinanceProvider,
            MarketDataError, get_provider_serve_counts,
        )
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()

            yf_quote = _make_fake_quote("AAPL", "yfinance")
            with patch.object(
                     FMPProvider, "get_latest_quote",
                     side_effect=MarketDataError("FMP down"),
                 ), \
                 patch.object(YFinanceProvider, "get_latest_quote", return_value=yf_quote), \
                 caplog.at_level(logging.WARNING, logger="data.market_data"):
                out = cp.get_latest_quote("AAPL")

        assert out.source == "yfinance"
        assert any(
            r.levelno == logging.WARNING and "trying next in chain" in r.message
            for r in caplog.records
        )
        assert get_provider_serve_counts()[("quote", "yfinance")] == 1

    def test_fmp_bars_failure_falls_back_to_yfinance_then_serves_and_counts(self, caplog):
        import logging
        from data.market_data import (
            CompositeProvider, FMPProvider, YFinanceProvider,
            MarketDataError, get_provider_serve_counts,
        )
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()

            yf_bars = _make_fake_bars_df()
            with patch.object(
                     FMPProvider, "get_intraday_bars",
                     side_effect=MarketDataError("FMP down"),
                 ), \
                 patch.object(YFinanceProvider, "get_intraday_bars", return_value=yf_bars), \
                 caplog.at_level(logging.WARNING, logger="data.market_data"):
                out = cp.get_intraday_bars("AAPL")

        pd.testing.assert_frame_equal(out, yf_bars)
        assert any(
            r.levelno == logging.WARNING and "trying next in chain" in r.message
            for r in caplog.records
        )
        assert get_provider_serve_counts()[("bars", "yfinance")] == 1

    # -- 4. FMP_FALLBACK_ENABLED=False -> chain length 1 -----------------
    def test_fallback_disabled_quote_chain_is_fmp_only(self):
        from data.market_data import (
            CompositeProvider, FMPProvider, YFinanceProvider, MarketDataError,
        )
        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()
            with patch.object(
                     FMPProvider, "get_latest_quote",
                     side_effect=MarketDataError("FMP down"),
                 ), \
                 patch.object(YFinanceProvider, "get_latest_quote") as yf_mock:
                with pytest.raises(MarketDataError):
                    cp.get_latest_quote("AAPL")
            yf_mock.assert_not_called()

    def test_fallback_disabled_bars_chain_is_fmp_only(self):
        from data.market_data import (
            CompositeProvider, FMPProvider, YFinanceProvider, MarketDataError,
        )
        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()
            with patch.object(
                     FMPProvider, "get_intraday_bars",
                     side_effect=MarketDataError("FMP down"),
                 ), \
                 patch.object(YFinanceProvider, "get_intraday_bars") as yf_mock:
                with pytest.raises(MarketDataError):
                    cp.get_intraday_bars("AAPL")
            yf_mock.assert_not_called()

    def test_fallback_disabled_but_fmp_succeeds_still_serves(self):
        from data.market_data import CompositeProvider, FMPProvider
        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()
            fmp_quote = _make_fake_quote("AAPL", "fmp")
            with patch.object(FMPProvider, "get_latest_quote", return_value=fmp_quote):
                out = cp.get_latest_quote("AAPL")
            assert out.source == "fmp"

    # -- 5. Cache-then-chain ordering -------------------------------------
    def test_cached_quote_never_triggers_chain(self):
        from data.market_data import CompositeProvider, FMPProvider, YFinanceProvider
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
            fmp_quote = _make_fake_quote("AAPL", "fmp")
            with patch.object(FMPProvider, "get_latest_quote", return_value=fmp_quote) as fmp_mock:
                cp.get_latest_quote("AAPL")  # populates the cache
            assert fmp_mock.call_count == 1

            with patch.object(FMPProvider, "get_latest_quote") as fmp_mock2, \
                 patch.object(YFinanceProvider, "get_latest_quote") as yf_mock2:
                cp.get_latest_quote("AAPL")  # must be served from cache
            fmp_mock2.assert_not_called()
            yf_mock2.assert_not_called()

    def test_cached_bars_never_trigger_chain(self):
        from data.market_data import CompositeProvider, FMPProvider, YFinanceProvider
        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
            fmp_bars = _make_fake_bars_df()
            with patch.object(FMPProvider, "get_intraday_bars", return_value=fmp_bars) as fmp_mock:
                cp.get_intraday_bars("AAPL", lookback_days=50)  # populates the cache
            assert fmp_mock.call_count == 1

            with patch.object(FMPProvider, "get_intraday_bars") as fmp_mock2, \
                 patch.object(YFinanceProvider, "get_intraday_bars") as yf_mock2:
                cp.get_intraday_bars("AAPL", lookback_days=50)  # must be served from cache
            fmp_mock2.assert_not_called()
            yf_mock2.assert_not_called()

    # -- 6. Existing Alpaca/yfinance quote/bars path completely untouched --
    def test_default_config_quote_path_untouched_even_with_fmp_fully_configured(self):
        """Regression guard: even with FMP fully configured (a real-looking
        key present) but MARKET_DATA_PROVIDER left at its default, the
        existing single-provider quote path must be exactly what it is
        today -- no chain, no FMP network call ever attempted."""
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched(FMP_API_KEY="a-real-looking-key", MARKET_DATA_PROVIDER=None):
            cp = CompositeProvider()
            yf_quote = _make_fake_quote("AAPL", "yfinance")
            with patch.object(
                     YFinanceProvider, "get_latest_quote", return_value=yf_quote,
                 ) as yf_mock, \
                 patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_latest_quote("AAPL")

            assert out.source == "yfinance"
            yf_mock.assert_called_once()
            fmp_get_mock.assert_not_called()

    def test_default_config_bars_path_untouched_even_with_fmp_fully_configured(self):
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched(FMP_API_KEY="a-real-looking-key", MARKET_DATA_PROVIDER=None):
            cp = CompositeProvider()
            yf_bars = _make_fake_bars_df()
            with patch.object(
                     YFinanceProvider, "get_intraday_bars", return_value=yf_bars,
                 ) as yf_mock, \
                 patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_intraday_bars("AAPL")

            pd.testing.assert_frame_equal(out, yf_bars)
            yf_mock.assert_called_once()
            fmp_get_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 11. FMP_QUOTES_ENABLED / FMP_BARS_ENABLED / FMP_FUNDAMENTALS_ENABLED --
# the independent per-capability gates on top of MARKET_DATA_PROVIDER=fmp /
# FUNDAMENTALS_SOURCE=fmp. These were originally shipped as reserved,
# unread settings (documented as a "two-gate convention" that the code never
# actually enforced); this class covers the follow-up that wires them in for
# real. The defining property under test throughout: MARKET_DATA_PROVIDER=fmp
# selects the FMPProvider OBJECT, but each capability (quotes, bars,
# fundamentals) independently decides whether that object actually SERVES --
# a gate being off must behave EXACTLY like MARKET_DATA_PROVIDER/
# FUNDAMENTALS_SOURCE had never selected 'fmp' at all for that capability,
# unconditionally (i.e. NOT governed by FMP_FALLBACK_ENABLED, since FMP was
# never attempted in the first place -- there's nothing to "fall back" from).
# ---------------------------------------------------------------------------

class TestFMPCapabilityGates:
    @pytest.fixture(autouse=True)
    def _reset_serve_counts(self):
        from data.market_data import reset_provider_serve_counts
        reset_provider_serve_counts()
        yield
        reset_provider_serve_counts()

    def _patched(self, **overrides):
        base = dict(
            ALPACA_API_KEY=None, ALPACA_SECRET_KEY=None, MARKET_DATA_PROVIDER=None,
            FMP_API_KEY=None, FUNDAMENTALS_SOURCE="yahoo", FMP_FALLBACK_ENABLED=True,
            FMP_QUOTES_ENABLED=False, FMP_BARS_ENABLED=False, FMP_FUNDAMENTALS_ENABLED=False,
        )
        base.update(overrides)
        return patch.multiple("settings.settings", **base)

    # -- Quotes gate, independently -------------------------------------
    def test_quotes_gate_off_falls_through_to_default_unconditionally(self):
        """MARKET_DATA_PROVIDER=fmp + FMP_QUOTES_ENABLED=False: quotes must
        be served by the plain Alpaca-if-keyed-else-yfinance default, with
        ZERO FMP network activity -- even though FMP_FALLBACK_ENABLED is at
        its default True, this is NOT a fallback (FMP was never attempted)."""
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
            yf_quote = _make_fake_quote("AAPL", "yfinance")
            with patch.object(
                     YFinanceProvider, "get_latest_quote", return_value=yf_quote,
                 ) as yf_mock, \
                 patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_latest_quote("AAPL")

            assert out.source == "yfinance"
            yf_mock.assert_called_once()
            fmp_get_mock.assert_not_called()

    def test_quotes_gate_off_even_with_fallback_disabled_still_serves(self):
        """The capability-gate-off path must NOT be short-circuited by
        FMP_FALLBACK_ENABLED=False -- that setting only governs what happens
        after a REAL FMP attempt fails, and FMP is never attempted here."""
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()
            yf_quote = _make_fake_quote("AAPL", "yfinance")
            with patch.object(YFinanceProvider, "get_latest_quote", return_value=yf_quote):
                out = cp.get_latest_quote("AAPL")
            assert out.source == "yfinance"

    def test_quotes_gate_on_bars_gate_off_are_independent(self):
        """The whole point of two separate settings: quotes can use FMP while
        bars fall through to the default, from the SAME CompositeProvider
        instance, without touching MARKET_DATA_PROVIDER at all."""
        from data.market_data import CompositeProvider, FMPProvider, YFinanceProvider

        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_QUOTES_ENABLED=True, FMP_BARS_ENABLED=False,
        ):
            cp = CompositeProvider()
            fmp_quote = _make_fake_quote("AAPL", "fmp")
            yf_bars = _make_fake_bars_df()
            with patch.object(FMPProvider, "get_latest_quote", return_value=fmp_quote), \
                 patch.object(FMPProvider, "get_intraday_bars") as fmp_bars_mock, \
                 patch.object(YFinanceProvider, "get_intraday_bars", return_value=yf_bars):
                quote_out = cp.get_latest_quote("AAPL")
                bars_out = cp.get_intraday_bars("AAPL")

            assert quote_out.source == "fmp"
            pd.testing.assert_frame_equal(bars_out, yf_bars)
            fmp_bars_mock.assert_not_called()

    # -- Bars gate, independently ----------------------------------------
    def test_bars_gate_off_falls_through_to_default_unconditionally(self):
        from data.market_data import CompositeProvider, YFinanceProvider

        with self._patched(MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
            yf_bars = _make_fake_bars_df()
            with patch.object(
                     YFinanceProvider, "get_intraday_bars", return_value=yf_bars,
                 ) as yf_mock, \
                 patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_intraday_bars("AAPL")

            pd.testing.assert_frame_equal(out, yf_bars)
            yf_mock.assert_called_once()
            fmp_get_mock.assert_not_called()

    def test_bars_gate_on_quotes_gate_off_are_independent(self):
        """The mirror image of test_quotes_gate_on_bars_gate_off_are_independent."""
        from data.market_data import CompositeProvider, FMPProvider, YFinanceProvider

        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_QUOTES_ENABLED=False, FMP_BARS_ENABLED=True,
        ):
            cp = CompositeProvider()
            fmp_bars = _make_fake_bars_df()
            yf_quote = _make_fake_quote("AAPL", "yfinance")
            with patch.object(FMPProvider, "get_intraday_bars", return_value=fmp_bars), \
                 patch.object(FMPProvider, "get_latest_quote") as fmp_quote_mock, \
                 patch.object(YFinanceProvider, "get_latest_quote", return_value=yf_quote):
                bars_out = cp.get_intraday_bars("AAPL")
                quote_out = cp.get_latest_quote("AAPL")

            pd.testing.assert_frame_equal(bars_out, fmp_bars)
            assert quote_out.source == "yfinance"
            fmp_quote_mock.assert_not_called()

    # -- Fundamentals gate, independently ---------------------------------
    def test_fundamentals_gate_off_falls_through_to_default_unconditionally(self):
        from data.market_data import CompositeProvider, YahooFundamentalsProvider

        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
            with patch.object(
                     YahooFundamentalsProvider, "get_fundamentals",
                     return_value={"trailingPE": 12.0},
                 ) as yahoo_mock, \
                 patch("data.fmp_client.requests.get") as fmp_get_mock:
                out = cp.get_fundamentals("AAPL")

            assert out == {"trailingPE": 12.0}
            yahoo_mock.assert_called_once()
            fmp_get_mock.assert_not_called()
            # No "_source" tagging on the legacy path this falls through to
            # -- matches TestFMPFundamentalsChain's default-config contract.
            assert "_source" not in out

    def test_fundamentals_gate_off_even_with_fallback_disabled_still_serves(self):
        from data.market_data import CompositeProvider, YahooFundamentalsProvider

        with self._patched(
            FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_FALLBACK_ENABLED=False,
        ):
            cp = CompositeProvider()
            with patch.object(
                YahooFundamentalsProvider, "get_fundamentals",
                return_value={"trailingPE": 12.0},
            ):
                out = cp.get_fundamentals("AAPL")
            assert out == {"trailingPE": 12.0}

    # -- Observability: is_realtime / quote_source / source_name must be
    #    honest about the EFFECTIVE provider, not the merely-selected one --
    def test_quote_source_and_is_realtime_reflect_effective_not_selected_provider(self):
        from data.market_data import CompositeProvider

        with self._patched(
            MARKET_DATA_PROVIDER="fmp", FMP_API_KEY="a-real-looking-key",
            FMP_QUOTES_ENABLED=False, FMP_QUOTES_REALTIME=True,
        ):
            cp = CompositeProvider()
            # Provider SELECTION still picked FMP...
            from data.market_data import FMPProvider
            assert isinstance(cp._quote_provider, FMPProvider)
            # ...but with the capability gate off, the observable labels must
            # report the provider that ACTUALLY serves quotes (yfinance),
            # never "fmp" -- even though FMP_QUOTES_REALTIME=True would have
            # made is_realtime True had FMP genuinely been serving.
            assert cp.quote_source == "yfinance"
            assert cp.is_realtime is False

    def test_source_name_reflects_effective_not_selected_fundamentals_provider(self):
        from data.market_data import CompositeProvider, FMPProvider

        with self._patched(FUNDAMENTALS_SOURCE="fmp", FMP_API_KEY="a-real-looking-key"):
            cp = CompositeProvider()
            assert isinstance(cp._fundamentals_provider, FMPProvider)
            assert cp.source_name != "fmp"


# ---------------------------------------------------------------------------
# 12. CompositeProvider against the genuinely UNTOUCHED Settings() defaults --
# closes a real gap in the FMP test coverage above. Every TestFMP*Chain /
# TestCompositeProviderSelection test above explicitly monkeypatches
# MARKET_DATA_PROVIDER / FUNDAMENTALS_SOURCE (several even hardcode the
# PRE-FMP baseline, MARKET_DATA_PROVIDER=None / FUNDAMENTALS_SOURCE="yahoo",
# as their own "default" fixture -- see TestFMPFundamentalsChain._patched /
# TestFMPQuoteBarsChain._patched) -- none of them ever construct a
# CompositeProvider() against whatever settings.settings ACTUALLY holds at
# that point, which today is MARKET_DATA_PROVIDER="fmp" /
# FUNDAMENTALS_SOURCE="fmp" (settings.py, changed by explicit operator
# decision from the pre-FMP baseline every test above still hardcodes as its
# "default"). A regression that broke the INTERACTION between these
# individually-correct field defaults (e.g. a typo in the string compared
# against MARKET_DATA_PROVIDER, or a refactor of the branch that reads it)
# could pass every test above while the real, unpatched default silently
# fell back to yfinance -- and nothing in the suite would catch it.
# ---------------------------------------------------------------------------

class TestCompositeProviderGenuineDefaultRouting:
    """Constructs ``CompositeProvider()`` against the real, un-monkeypatched
    ``settings.settings`` singleton (conftest.py's autouse reset fixtures
    restore it to the true coded defaults before every test -- see
    ``conftest.py::_clean_settings_between_tests`` / the module-level reset
    at the top of that file) and proves the platform's actual shipped
    defaults route quotes, bars, AND fundamentals to FMP -- not merely that
    a hand-patched ``MARKET_DATA_PROVIDER="fmp"`` would.

    No test here ever monkeypatches ``MARKET_DATA_PROVIDER`` /
    ``FUNDAMENTALS_SOURCE`` / any ``FMP_*_ENABLED`` flag. The only setting
    ever touched is ``FMP_API_KEY`` (via the plain ``monkeypatch`` fixture,
    never ``patch.multiple``, since it is the one field genuinely being
    varied) -- everything else is whatever the untouched singleton holds.
    """

    @pytest.fixture(autouse=True)
    def _reset_serve_counts(self):
        from data.market_data import reset_provider_serve_counts
        reset_provider_serve_counts()
        yield
        reset_provider_serve_counts()

    @staticmethod
    def _fmp_response(payload: Any) -> MagicMock:
        """A minimal stand-in for ``requests.Response`` shaped like
        ``tests/test_fmp_client.py``'s own ``_resp()`` helper (status 200, no
        Retry-After header, ``.json()`` returns ``payload``)."""
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = payload
        return resp

    # -- 0. Self-check: pin the values every test below assumes against the
    #    REAL settings.py field declarations, so a future intentional
    #    default change fails loudly here instead of this class silently
    #    continuing to test stale assumptions (do not hand-edit these
    #    literals without first re-reading settings.py). ------------------
    def test_documented_defaults_match_settings_py_field_declarations(self):
        from settings import Settings

        assert Settings.model_fields["MARKET_DATA_PROVIDER"].default == "fmp"
        assert Settings.model_fields["FUNDAMENTALS_SOURCE"].default == "fmp"
        assert Settings.model_fields["FMP_QUOTES_ENABLED"].default is True
        assert Settings.model_fields["FMP_BARS_ENABLED"].default is True
        assert Settings.model_fields["FMP_FUNDAMENTALS_ENABLED"].default is True

    def test_settings_singleton_currently_holds_the_real_untouched_defaults(self):
        """Guards the premise every other test in this class relies on: the
        live ``settings.settings`` singleton, at THIS point in the test run
        (no patch applied by this test), actually holds the same values
        pinned above -- not a leftover mutation from a prior test, and not a
        real operator ``.env`` override (this checkout ships none)."""
        from settings import settings as _settings

        assert _settings.MARKET_DATA_PROVIDER == "fmp"
        assert _settings.FUNDAMENTALS_SOURCE == "fmp"
        assert _settings.FMP_QUOTES_ENABLED is True
        assert _settings.FMP_BARS_ENABLED is True
        assert _settings.FMP_FUNDAMENTALS_ENABLED is True
        assert not _settings.FMP_API_KEY  # genuinely absent, not merely falsy-by-luck

    # -- 1. Quotes ---------------------------------------------------------
    def test_quote_path_routes_to_fmp_at_genuine_defaults(self, monkeypatch):
        """With FMP_API_KEY set and every other setting left untouched, the
        quote path must select FMPProvider AND actually invoke the FMP HTTP
        layer -- asserting the mock was called is the only way to prove FMP
        was really the path taken, rather than merely that a Quote came back
        (which the yfinance/Alpaca fallback would also produce)."""
        from settings import settings as _settings
        from data.market_data import (
            CompositeProvider, FMPProvider, get_provider_serve_counts,
        )

        monkeypatch.setattr(_settings, "FMP_API_KEY", "a-real-looking-key")
        cp = CompositeProvider()
        assert isinstance(cp._quote_provider, FMPProvider)

        row = {"symbol": "AAPL", "price": 187.43, "timestamp": 1735689600}
        with patch(
            "data.fmp_client.requests.get", return_value=self._fmp_response([row]),
        ) as mock_get:
            out = cp.get_latest_quote("AAPL")

        mock_get.assert_called()
        assert out.source == "fmp"
        assert out.symbol == "AAPL"
        assert out.price == 187.43
        assert get_provider_serve_counts()[("quote", "fmp")] == 1

    # -- 2. Bars -------------------------------------------------------------
    def test_bars_path_routes_to_fmp_at_genuine_defaults(self, monkeypatch):
        from settings import settings as _settings
        from data.market_data import (
            CompositeProvider, FMPProvider, get_provider_serve_counts,
        )

        monkeypatch.setattr(_settings, "FMP_API_KEY", "a-real-looking-key")
        cp = CompositeProvider()
        assert isinstance(cp._quote_provider, FMPProvider)  # bars share quote selection

        bar_rows = [
            {
                "date": f"2026-08-0{d}", "adjOpen": 100.0 + d, "adjHigh": 101.0 + d,
                "adjLow": 99.0 + d, "adjClose": 100.5 + d, "volume": 1_000_000,
            }
            for d in range(1, 6)
        ]
        with patch(
            "data.fmp_client.requests.get", return_value=self._fmp_response(bar_rows),
        ) as mock_get:
            out = cp.get_intraday_bars("AAPL")

        mock_get.assert_called()
        assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert not out.empty
        assert get_provider_serve_counts()[("bars", "fmp")] == 1

    # -- 3. Fundamentals -----------------------------------------------------
    def test_fundamentals_path_routes_to_fmp_at_genuine_defaults(self, monkeypatch):
        from settings import settings as _settings
        from data.market_data import (
            CompositeProvider, FMPProvider, get_provider_serve_counts,
        )

        monkeypatch.setattr(_settings, "FMP_API_KEY", "a-real-looking-key")
        cp = CompositeProvider()
        assert isinstance(cp._fundamentals_provider, FMPProvider)

        # One row reused across every FMP endpoint FMPProvider.get_fundamentals
        # fans out to (quote/profile/key_metrics_ttm/ratios_ttm/
        # income_statement_ttm/dividends/shares_float, plus two historical_eod
        # calls for the beta computation) -- map_fundamentals degrades any
        # field a given endpoint doesn't actually carry to NaN rather than
        # raising, so a single shared shape is sufficient here; the exact
        # per-field mapping math is already covered by
        # tests/test_fmp_fundamentals.py.
        row = {
            "symbol": "AAPL", "price": 187.43, "companyName": "Apple Fake Co",
            "sector": "Technology", "date": "2026-08-01",
            "adjOpen": 185.0, "adjHigh": 188.0, "adjLow": 184.5, "adjClose": 187.0,
            "volume": 50_000_000, "dividend": 0.24,
        }
        with patch(
            "data.fmp_client.requests.get", return_value=self._fmp_response([row]),
        ) as mock_get:
            out = cp.get_fundamentals("AAPL")

        mock_get.assert_called()
        assert out["_source"] == "fmp"
        assert out["shortName"] == "Apple Fake Co"
        assert get_provider_serve_counts()[("fundamentals", "fmp")] == 1

    # -- 4. Negative case: operator forgot to set the key --------------------
    def test_missing_api_key_at_genuine_defaults_falls_back_gracefully(self, caplog):
        """The documented graceful-degrade path, exercised at the platform's
        REAL default (MARKET_DATA_PROVIDER=FUNDAMENTALS_SOURCE="fmp") rather
        than a hand-patched one -- proves the "operator forgot to configure
        FMP_API_KEY" case degrades to the Alpaca/yfinance/Yahoo default with
        a WARNING instead of raising, on a completely untouched singleton."""
        import logging
        from settings import settings as _settings
        from data.market_data import (
            CompositeProvider, FMPProvider, YFinanceProvider, YahooFundamentalsProvider,
        )

        assert not _settings.FMP_API_KEY  # genuinely absent -- never overridden
        assert _settings.MARKET_DATA_PROVIDER == "fmp"
        assert _settings.FUNDAMENTALS_SOURCE == "fmp"

        with caplog.at_level(logging.WARNING, logger="data.market_data"):
            cp = CompositeProvider()  # zero patches of any kind

        # Quote/bars: falls through to the plain Alpaca-if-keyed-else-yfinance
        # default (yfinance here, since ALPACA_API_KEY/SECRET are also
        # genuinely unset) rather than raising.
        assert not isinstance(cp._quote_provider, FMPProvider)
        assert isinstance(cp._quote_provider, YFinanceProvider)
        assert "falling back to the default quote/bars provider" in caplog.text

        # Fundamentals: the same graceful degrade, independently gated.
        assert not isinstance(cp._fundamentals_provider, FMPProvider)
        assert isinstance(cp._fundamentals_provider, YahooFundamentalsProvider)
        assert "falling back to the default fundamentals provider" in caplog.text

        # And it must not have raised at all -- get_latest_quote should still
        # work end-to-end via the fallback, never a MarketDataError bubbling
        # up just because the operator forgot FMP_API_KEY.
        yf_quote = _make_fake_quote("AAPL", "yfinance")
        with patch.object(YFinanceProvider, "get_latest_quote", return_value=yf_quote), \
             patch("data.fmp_client.requests.get") as fmp_get_mock:
            out = cp.get_latest_quote("AAPL")
        assert out.source == "yfinance"
        fmp_get_mock.assert_not_called()
