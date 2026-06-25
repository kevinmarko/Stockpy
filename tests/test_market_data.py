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
  - CompositeProvider.get_fundamentals falls back to yfinance when Finnhub empty
  - reset_provider() forces re-initialisation on next get_provider() call
"""

import importlib
import os
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
        cache = _QuoteCache(ttl_seconds=1)
        q = self._make_quote()
        cache.put(q)
        # Advance time past TTL
        time.sleep(1.1)
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

    def test_get_fundamentals_returns_empty(self):
        from data.market_data import AlpacaProvider
        provider = AlpacaProvider.__new__(AlpacaProvider)
        provider._api_key = "k"
        provider._secret_key = "s"
        provider._stale_threshold = 60
        provider._client = MagicMock()
        assert provider.get_fundamentals("AAPL") == {}


# ---------------------------------------------------------------------------
# 4. YFinanceProvider
# ---------------------------------------------------------------------------

class TestYFinanceProvider:
    def _mock_fast_info(self, last_price=150.0, bid=149.9, ask=150.1):
        mock_fi = MagicMock()
        mock_fi.get = lambda k, *a: {
            "last_price": last_price, "bid": bid, "ask": ask,
        }.get(k, a[0] if a else None)
        return mock_fi

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
# 6. CompositeProvider selection
# ---------------------------------------------------------------------------

class TestCompositeProviderSelection:
    """Verifies provider auto-selection based on env vars."""

    def _env(self, **kw):
        base = {
            "ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": "",
            "MARKET_DATA_PROVIDER": "", "FINNHUB_API_KEY": "",
        }
        base.update(kw)
        return base

    def test_selects_yfinance_when_no_keys(self):
        from data.market_data import CompositeProvider, YFinanceProvider
        env = self._env()
        with patch.dict(os.environ, env, clear=False):
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, YFinanceProvider)

    def test_selects_alpaca_when_keys_present(self):
        from data.market_data import AlpacaProvider

        fake_client = MagicMock()
        with patch.dict(
            os.environ,
            self._env(ALPACA_API_KEY="key123", ALPACA_SECRET_KEY="sec456"),
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
        env = self._env(
            ALPACA_API_KEY="key", ALPACA_SECRET_KEY="sec",
            MARKET_DATA_PROVIDER="yfinance",
        )
        with patch.dict(os.environ, env):
            cp = CompositeProvider()
        assert isinstance(cp._quote_provider, YFinanceProvider)

    def test_unknown_provider_raises(self):
        env = self._env(MARKET_DATA_PROVIDER="bloomberg")
        with patch.dict(os.environ, env):
            from data.market_data import CompositeProvider
            with pytest.raises(RuntimeError, match="Unknown MARKET_DATA_PROVIDER"):
                CompositeProvider()

    def test_is_realtime_false_for_yfinance(self):
        from data.market_data import CompositeProvider
        with patch.dict(os.environ, self._env()):
            cp = CompositeProvider()
        assert cp.is_realtime is False

    def test_quote_source_string(self):
        from data.market_data import CompositeProvider
        with patch.dict(os.environ, self._env()):
            cp = CompositeProvider()
        assert cp.quote_source == "yfinance"


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
        cp._fundamentals_provider = MagicMock(spec=FinnhubProvider)
        cp._fundamentals_provider.get_fundamentals.return_value = {}
        return cp, mock_provider

    def test_cache_deduplicates_calls(self):
        cp, mock_provider = self._make_cp()
        q1 = cp.get_latest_quote("AAPL")
        q2 = cp.get_latest_quote("AAPL")
        assert mock_provider.get_latest_quote.call_count == 1
        assert q1 is q2

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

    def test_fundamentals_fallback_to_yfinance_when_finnhub_empty(self):
        """When FinnhubProvider returns {}, CompositeProvider falls back to YFinanceProvider."""
        from data.market_data import CompositeProvider
        cp = CompositeProvider.__new__(CompositeProvider)
        from data.market_data import _QuoteCache, FinnhubProvider, YFinanceProvider
        cp._cache = _QuoteCache(ttl_seconds=30)
        cp._quote_provider = MagicMock()

        mock_finnhub = MagicMock(spec=FinnhubProvider)
        mock_finnhub.get_fundamentals.return_value = {}
        cp._fundamentals_provider = mock_finnhub

        yf_fund = {"trailingPE": 28.5}
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}), \
             patch.object(YFinanceProvider, "get_fundamentals", return_value=yf_fund):
            result = cp.get_fundamentals("AAPL")

        assert result == yf_fund


# ---------------------------------------------------------------------------
# 8. get_provider / reset_provider singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_singleton_returns_same_instance(self):
        from data.market_data import get_provider, reset_provider
        reset_provider()
        with patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}):
            p1 = get_provider()
            p2 = get_provider()
        assert p1 is p2

    def test_reset_forces_new_instance(self):
        from data.market_data import get_provider, reset_provider
        reset_provider()
        with patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}):
            p1 = get_provider()
        reset_provider()
        with patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}):
            p2 = get_provider()
        assert p1 is not p2
