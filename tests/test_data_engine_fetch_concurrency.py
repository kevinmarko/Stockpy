"""
tests/test_data_engine_fetch_concurrency.py
============================================
Covers the parallelization of DataEngine.fetch_technical_raw() and
fetch_fundamentals_raw() (data_engine.py). Both were originally a serial
`for symbol in tickers:` loop making one blocking yfinance HTTP call at a
time; they now run through a ThreadPoolExecutor sized by
settings.DATA_FETCH_MAX_CONCURRENCY. All yfinance network calls are
monkeypatched -- no real network I/O.

Verifies:
  - happy path returns the same shape/content as before, regardless of
    worker count (1 = sequential, >1 = parallel)
  - dead-letter resilience: one ticker raising never aborts the batch
  - empty-history / no-attribute-on-mock tickers degrade to omission, not a
    crash
  - settings.DATA_FETCH_MAX_CONCURRENCY=1 forces the sequential path (used
    by callers that want fully deterministic ordering/timing)

Also covers TestFetchTechnicalRawCached (2026-07): DataEngine's new,
additive fetch_technical_raw_cached() method, which routes each ticker
through data.historical_store.HistoricalStore.get_bars() (incremental
top-up) when settings.HISTORICAL_STORE_ENABLED is True, falling back to the
EXACT fetch_technical_raw() behavior on any HistoricalStore/provider
construction failure or when the flag is off. All HistoricalStore/provider
calls are monkeypatched -- no real on-disk DB is touched.
"""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from data_engine import DataEngine, MockDataEngine


def _make_history_df(rows=3):
    dates = pd.date_range("2025-01-01", periods=rows)
    return pd.DataFrame(
        {"Open": [100.0] * rows, "High": [101.0] * rows, "Low": [99.0] * rows,
         "Close": [100.5] * rows, "Volume": [1000] * rows},
        index=dates,
    )


class TestFetchTechnicalRawConcurrency:
    def _engine(self):
        return DataEngine(fred_api_key=None)

    def test_happy_path_all_symbols_returned(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        def _ticker_factory(symbol):
            m = MagicMock()
            m.history.return_value = _make_history_df()
            return m

        with patch("yfinance.Ticker", side_effect=_ticker_factory):
            result = self._engine().fetch_technical_raw(["AAPL", "MSFT", "GOOG"])

        assert set(result.keys()) == {"AAPL", "MSFT", "GOOG"}
        for df in result.values():
            assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]

    def test_one_bad_symbol_does_not_abort_batch(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        def _ticker_factory(symbol):
            if symbol == "BADCO":
                raise RuntimeError("network error")
            m = MagicMock()
            m.history.return_value = _make_history_df()
            return m

        with patch("yfinance.Ticker", side_effect=_ticker_factory):
            result = self._engine().fetch_technical_raw(["AAPL", "BADCO", "MSFT"])

        assert "BADCO" not in result
        assert set(result.keys()) == {"AAPL", "MSFT"}

    def test_empty_history_omitted_not_fabricated(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)
        m = MagicMock()
        m.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=m):
            result = self._engine().fetch_technical_raw(["EMPTY"])
        assert result == {}

    def test_sequential_path_worker_1_matches_parallel_result(self, monkeypatch):
        def _ticker_factory(symbol):
            m = MagicMock()
            m.history.return_value = _make_history_df()
            return m

        tickers = ["AAPL", "MSFT", "GOOG"]
        with patch("yfinance.Ticker", side_effect=_ticker_factory):
            monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 1)
            sequential = self._engine().fetch_technical_raw(tickers)
            monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)
            parallel = self._engine().fetch_technical_raw(tickers)

        assert set(sequential.keys()) == set(parallel.keys()) == set(tickers)


class TestFetchFundamentalsRawConcurrency:
    """fetch_fundamentals_raw() now routes through the shared
    data.market_data.CompositeProvider singleton (Yahoo statement-derived
    engine, primary) instead of calling yf.Ticker(symbol).info directly --
    closing the one fundamentals path that used to bypass CompositeProvider.
    A direct yf.Ticker(symbol).dividends call is preserved alongside it (the
    provider doesn't surface the raw dividends Series), still monkeypatched.
    """

    def _engine(self):
        return DataEngine(fred_api_key=None)

    def _patch_dividends(self, dividends=None):
        def _ticker_factory(symbol):
            m = MagicMock()
            m.dividends = dividends if dividends is not None else pd.Series(dtype="float64")
            return m
        return patch("yfinance.Ticker", side_effect=_ticker_factory)

    def test_happy_path_all_symbols_returned(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        fake_provider = MagicMock()
        fake_provider.get_fundamentals.side_effect = (
            lambda symbol: {"trailingPE": 20.0, "dividendYield": 0.015}
        )

        with patch("data.market_data.get_provider", return_value=fake_provider), \
                self._patch_dividends():
            result = self._engine().fetch_fundamentals_raw(["AAPL", "MSFT"])

        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert "info" in result["AAPL"]
        assert "dividends" in result["AAPL"]
        # 'financials' is dead weight nothing downstream ever consumed -- dropped.
        assert "financials" not in result["AAPL"]

    def test_info_comes_from_composite_provider_unmodified(self, monkeypatch):
        """The provider's dividendYield is already a correctly-scaled fraction
        -- fetch_fundamentals_raw must NOT re-normalize it (that would
        silently divide an already-correct fraction by 100 again)."""
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 1)

        fake_provider = MagicMock()
        fake_provider.get_fundamentals.return_value = {
            "trailingPE": 20.0, "dividendYield": 0.0267, "debtToEquity": 150.0,
        }

        with patch("data.market_data.get_provider", return_value=fake_provider), \
                self._patch_dividends():
            result = self._engine().fetch_fundamentals_raw(["AAPL"])

        assert result["AAPL"]["info"] == {
            "trailingPE": 20.0, "dividendYield": 0.0267, "debtToEquity": 150.0,
        }

    def test_one_bad_symbol_does_not_abort_batch(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        fake_provider = MagicMock()

        def _get_fundamentals(symbol):
            if symbol == "BADCO":
                raise RuntimeError("provider crashed")
            return {"trailingPE": 20.0}
        fake_provider.get_fundamentals.side_effect = _get_fundamentals

        with patch("data.market_data.get_provider", return_value=fake_provider), \
                self._patch_dividends():
            result = self._engine().fetch_fundamentals_raw(["AAPL", "BADCO"])

        assert "BADCO" not in result
        assert "AAPL" in result

    def test_dividend_fetch_failure_falls_back_to_empty_series(self, monkeypatch):
        """A dividends-history fetch failure must not dead-letter the whole
        symbol -- fundamentals (the primary payload) still come through."""
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 1)

        fake_provider = MagicMock()
        fake_provider.get_fundamentals.return_value = {"trailingPE": 20.0}

        with patch("data.market_data.get_provider", return_value=fake_provider), \
                patch("yfinance.Ticker", side_effect=RuntimeError("no dividend data")):
            result = self._engine().fetch_fundamentals_raw(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["info"] == {"trailingPE": 20.0}
        assert result["AAPL"]["dividends"].empty

    def test_sequential_path_worker_1_matches_parallel_result(self, monkeypatch):
        fake_provider = MagicMock()
        fake_provider.get_fundamentals.side_effect = lambda symbol: {"trailingPE": 20.0}

        tickers = ["AAPL", "MSFT", "GOOG"]
        with patch("data.market_data.get_provider", return_value=fake_provider), \
                self._patch_dividends():
            monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 1)
            sequential = self._engine().fetch_fundamentals_raw(tickers)
            monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)
            parallel = self._engine().fetch_fundamentals_raw(tickers)

        assert set(sequential.keys()) == set(parallel.keys()) == set(tickers)


class TestFetchTechnicalRawCached:
    """DataEngine.fetch_technical_raw_cached() -- HistoricalStore-routed bars
    fetch for main_orchestrator.py's fetch_all_data_async(). All
    HistoricalStore/provider access is monkeypatched; nothing here touches a
    real on-disk quant_platform.db.
    """

    def _engine(self):
        return DataEngine(fred_api_key=None)

    def _ticker_factory(self, symbol):
        m = MagicMock()
        m.history.return_value = _make_history_df()
        return m

    def test_flag_disabled_falls_back_to_direct_fetch(self, monkeypatch):
        """HISTORICAL_STORE_ENABLED=False must reproduce fetch_technical_raw()
        byte-for-byte -- HistoricalStore is never even imported."""
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", False)
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        with patch("yfinance.Ticker", side_effect=self._ticker_factory):
            engine = self._engine()
            direct = engine.fetch_technical_raw(["AAPL", "MSFT"])
            cached = engine.fetch_technical_raw_cached(["AAPL", "MSFT"])

        assert set(cached.keys()) == set(direct.keys()) == {"AAPL", "MSFT"}
        for sym in direct:
            pd.testing.assert_frame_equal(cached[sym], direct[sym])

    def test_historical_store_construction_failure_falls_back(self, monkeypatch):
        """HISTORICAL_STORE_ENABLED=True but HistoricalStore() raising on
        construction must degrade to the exact fetch_technical_raw() path
        (CONSTRAINT #6 -- dead-letter, never crash)."""
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", True)
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        with patch("yfinance.Ticker", side_effect=self._ticker_factory), \
                patch("data.historical_store.HistoricalStore", side_effect=RuntimeError("db down")):
            engine = self._engine()
            direct = engine.fetch_technical_raw(["AAPL"])
            cached = engine.fetch_technical_raw_cached(["AAPL"])

        assert set(cached.keys()) == set(direct.keys()) == {"AAPL"}
        pd.testing.assert_frame_equal(cached["AAPL"], direct["AAPL"])

    def test_get_provider_failure_falls_back(self, monkeypatch):
        """A working HistoricalStore but a broken get_provider() singleton
        must ALSO degrade to the direct fetch -- both are needed."""
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", True)

        with patch("yfinance.Ticker", side_effect=self._ticker_factory), \
                patch("data.historical_store.HistoricalStore", return_value=MagicMock()), \
                patch("data.market_data.get_provider", side_effect=RuntimeError("provider init failed")):
            result = self._engine().fetch_technical_raw_cached(["AAPL"])

        assert "AAPL" in result

    def test_uses_historical_store_get_bars_per_ticker(self, monkeypatch):
        """Happy path: each ticker is routed through
        HistoricalStore.get_bars(symbol, lookback_days=BARS_BACKFILL_DAYS,
        provider=<singleton>), and its return value is what flows through."""
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", True)
        monkeypatch.setattr("data_engine.settings.BARS_BACKFILL_DAYS", 504)
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        fake_store_instance = MagicMock()
        fake_store_instance.get_bars.side_effect = (
            lambda symbol, lookback_days=None, provider=None: _make_history_df()
        )
        fake_provider = MagicMock()

        with patch("data.historical_store.HistoricalStore", return_value=fake_store_instance), \
                patch("data.market_data.get_provider", return_value=fake_provider):
            result = self._engine().fetch_technical_raw_cached(["AAPL", "MSFT"])

        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert fake_store_instance.get_bars.call_count == 2
        for call in fake_store_instance.get_bars.call_args_list:
            args, kwargs = call
            assert args[0] in {"AAPL", "MSFT"}
            assert kwargs["lookback_days"] == 504
            assert kwargs["provider"] is fake_provider

    def test_one_bad_ticker_does_not_abort_batch(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", True)
        monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)

        def _get_bars(symbol, lookback_days=None, provider=None):
            if symbol == "BADCO":
                raise RuntimeError("db exploded")
            return _make_history_df()

        fake_store_instance = MagicMock()
        fake_store_instance.get_bars.side_effect = _get_bars

        with patch("data.historical_store.HistoricalStore", return_value=fake_store_instance), \
                patch("data.market_data.get_provider", return_value=MagicMock()):
            result = self._engine().fetch_technical_raw_cached(["AAPL", "BADCO", "MSFT"])

        assert "BADCO" not in result
        assert set(result.keys()) == {"AAPL", "MSFT"}

    def test_empty_bars_from_store_are_omitted_not_fabricated(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", True)
        fake_store_instance = MagicMock()
        fake_store_instance.get_bars.return_value = pd.DataFrame()

        with patch("data.historical_store.HistoricalStore", return_value=fake_store_instance), \
                patch("data.market_data.get_provider", return_value=MagicMock()):
            result = self._engine().fetch_technical_raw_cached(["EMPTY"])

        assert result == {}

    def test_sequential_path_worker_1_matches_parallel_result(self, monkeypatch):
        monkeypatch.setattr("data_engine.settings.HISTORICAL_STORE_ENABLED", True)

        fake_store_instance = MagicMock()
        fake_store_instance.get_bars.side_effect = (
            lambda symbol, lookback_days=None, provider=None: _make_history_df()
        )
        tickers = ["AAPL", "MSFT", "GOOG"]

        with patch("data.historical_store.HistoricalStore", return_value=fake_store_instance), \
                patch("data.market_data.get_provider", return_value=MagicMock()):
            monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 1)
            sequential = self._engine().fetch_technical_raw_cached(tickers)
            monkeypatch.setattr("data_engine.settings.DATA_FETCH_MAX_CONCURRENCY", 4)
            parallel = self._engine().fetch_technical_raw_cached(tickers)

        assert set(sequential.keys()) == set(parallel.keys()) == set(tickers)


class TestMockDataEngineFetchTechnicalRawCachedAlias:
    """MockDataEngine.fetch_technical_raw_cached() must be a plain,
    behavior-identical alias for its own fetch_technical_raw() -- required so
    main_orchestrator.py's MockDataEngine offline-fallback branch (and
    existing tests that construct MockDataEngine directly) keep working
    unchanged now that fetch_all_data_async() calls the "_cached" method
    unconditionally."""

    def test_returns_identical_output_to_fetch_technical_raw(self):
        # NOTE: fetch_technical_raw() builds its DatetimeIndex from
        # datetime.now() on every call, so two independent calls a few
        # microseconds apart can have sub-second-different index values --
        # normalize() strips time-of-day before comparing so this assertion
        # isn't flaky on wall-clock timing.
        engine = MockDataEngine()
        tickers = ["AAPL", "MSFT"]
        direct = engine.fetch_technical_raw(tickers)
        cached = engine.fetch_technical_raw_cached(tickers)

        assert set(cached.keys()) == set(direct.keys()) == set(tickers)
        for sym in tickers:
            d, c = direct[sym], cached[sym]
            assert list(c.columns) == list(d.columns)
            assert c.index.normalize().equals(d.index.normalize())
            assert (c.values == d.values).all()
