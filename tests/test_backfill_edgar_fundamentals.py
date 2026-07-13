"""Tests for scripts/backfill_edgar_fundamentals.py.

Covers two real bugs found in an independent audit of the concurrent-track
SEC EDGAR data-layer commits:

1. ``past_bars.iloc[-1]["close"]`` (lowercase) raised ``KeyError`` against
   ``HistoricalStore.get_bars()``'s real, capitalized OHLCV schema
   (``["Open", "High", "Low", "Close", "Volume"]``) for essentially any real
   ticker with cached bars.
2. The per-ticker loop in ``main()`` had no try/except at all, so that single
   bug (or any other per-ticker failure) aborted the entire backfill batch on
   the first affected ticker, losing every subsequent ticker.
"""

import sys
from unittest import mock

import pandas as pd
import pytest

from scripts import backfill_edgar_fundamentals as backfill


def _facts_with_one_filing(price_date="2020-01-15"):
    return {
        "facts": {
            "us-gaap": {
                "EarningsPerShareBasic": {"units": {"USD/shares": [{"val": 5.0, "filed": price_date}]}},
                "StockholdersEquity": {"units": {"USD": [{"val": 100000.0, "filed": price_date}]}},
            }
        }
    }


def _bars_df():
    return pd.DataFrame(
        {"Open": [9.0], "High": [11.0], "Low": [8.5], "Close": [10.0], "Volume": [1000]},
        index=pd.to_datetime(["2020-01-10"]),
    )


class _FakeStore:
    """Records every upsert call; get_bars returns a fixed, real-shaped DataFrame."""

    def __init__(self, bars=None, raise_on_get_bars_for=()):
        self._bars = bars if bars is not None else _bars_df()
        self._raise_for = set(raise_on_get_bars_for)
        self.upserts = []

    def get_bars(self, symbol):
        if symbol in self._raise_for:
            raise RuntimeError(f"simulated get_bars failure for {symbol}")
        return self._bars

    def upsert_fundamentals_pit(self, symbol, ratios, raw, *, report_date, source):
        self.upserts.append((symbol, report_date, ratios))


def _run_main(monkeypatch, tickers, store, get_cik=None, fetch_companyfacts=None):
    monkeypatch.setattr(backfill, "HistoricalStore", lambda: store)
    monkeypatch.setattr(
        backfill.edgar_fundamentals, "get_cik", get_cik or (lambda sym: f"CIK-{sym}")
    )
    monkeypatch.setattr(
        backfill.edgar_fundamentals,
        "fetch_companyfacts",
        fetch_companyfacts or (lambda cik: _facts_with_one_filing()),
    )
    monkeypatch.setattr(sys, "argv", ["backfill_edgar_fundamentals.py", "--tickers", ",".join(tickers)])
    backfill.main()


class TestPriceExtractionUsesRealColumnCasing:
    def test_price_extracted_without_crashing(self, monkeypatch):
        """Regression test for the lowercase 'close' KeyError. A real (capitalized)
        bars DataFrame must not raise, and the extracted price must be correct."""
        store = _FakeStore()
        _run_main(monkeypatch, ["AAPL"], store)

        assert len(store.upserts) == 1
        symbol, report_date, ratios = store.upserts[0]
        assert symbol == "AAPL"
        # price=10.0 (from the Close column), shares unavailable -> eps/pe still computed from price.
        assert ratios["eps"] == 5.0
        assert ratios["pe_ratio"] == pytest.approx(10.0 / 5.0)


class TestDeadLetterPerTicker:
    def test_one_bad_ticker_does_not_abort_the_batch(self, monkeypatch, caplog):
        """A failure processing one ticker (in the middle of the batch) must be
        caught and logged, and every other ticker must still be processed --
        mirrors this codebase's dead-letter convention (AGENTS.md SS2)."""
        store = _FakeStore(raise_on_get_bars_for={"BAD"})
        _run_main(monkeypatch, ["AAPL", "BAD", "MSFT"], store)

        processed_symbols = {u[0] for u in store.upserts}
        assert processed_symbols == {"AAPL", "MSFT"}
        assert "BAD" not in processed_symbols
        assert any("BAD" in rec.message for rec in caplog.records)

    def test_missing_cik_still_allows_subsequent_tickers(self, monkeypatch):
        store = _FakeStore()

        def get_cik(sym):
            return None if sym == "UNKNOWN" else f"CIK-{sym}"

        _run_main(monkeypatch, ["UNKNOWN", "AAPL"], store, get_cik=get_cik)

        processed_symbols = {u[0] for u in store.upserts}
        assert processed_symbols == {"AAPL"}

    def test_empty_facts_still_allows_subsequent_tickers(self, monkeypatch):
        store = _FakeStore()

        def fetch_companyfacts(cik):
            return {} if cik == "CIK-NOFACTS" else _facts_with_one_filing()

        _run_main(
            monkeypatch,
            ["NOFACTS", "AAPL"],
            store,
            fetch_companyfacts=fetch_companyfacts,
        )

        processed_symbols = {u[0] for u in store.upserts}
        assert processed_symbols == {"AAPL"}
