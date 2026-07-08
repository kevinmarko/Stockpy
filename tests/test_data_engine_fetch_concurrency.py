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
"""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from data_engine import DataEngine


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
