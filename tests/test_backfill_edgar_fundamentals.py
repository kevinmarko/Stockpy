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

Plus the EDGAR-automation fixes:

3. Direct-path invocation (``python scripts/backfill_edgar_fundamentals.py``, the
   form deploy/crontab.txt and the trigger_edgar_backfill MCP tool both use) died
   with ``ModuleNotFoundError: No module named 'data'`` because the script lacked
   a repo-root sys.path shim. Every prior test imported the module as a package,
   so this survived — ``TestInvocationForms`` exercises the real subprocess form.
4. The incremental skip (``TestIncrementalSkip`` / ``TestSkipRowIdentity``) — a
   SET-based, source-scoped skip that can never change which rows land.
5. Deterministic + dead-letter-safe under the new worker pool
   (``TestConcurrency``).
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from scripts import backfill_edgar_fundamentals as backfill

_REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _facts_with_filings(filed_dates):
    """A companyfacts payload carrying one EPS point per date in *filed_dates*."""
    return {
        "facts": {
            "us-gaap": {
                "EarningsPerShareBasic": {
                    "units": {"USD/shares": [{"val": 5.0, "filed": d} for d in filed_dates]}
                },
                "StockholdersEquity": {
                    "units": {"USD": [{"val": 100000.0, "filed": d} for d in filed_dates]}
                },
            }
        }
    }


class _FakeStore:
    """Records every upsert call; get_bars returns a fixed, real-shaped DataFrame.

    ``stored_dates`` seeds ``get_pit_report_dates`` — the set of report_dates a
    prior EDGAR run already persisted (per-symbol dict or a single flat set
    applied to every symbol). Threads may call ``upsert_fundamentals_pit``
    concurrently; ``list.append`` is atomic under the GIL so ``upserts`` stays
    intact regardless of worker count.
    """

    def __init__(self, bars=None, raise_on_get_bars_for=(), stored_dates=None):
        self._bars = bars if bars is not None else _bars_df()
        self._raise_for = set(raise_on_get_bars_for)
        self._stored = stored_dates or {}
        self.pit_calls = []
        self.upserts = []

    def get_bars(self, symbol, lookback_days=504):
        if symbol in self._raise_for:
            raise RuntimeError(f"simulated get_bars failure for {symbol}")
        return self._bars

    def get_pit_report_dates(self, symbol, *, source="edgar", since=None):
        self.pit_calls.append((symbol, source, since))
        if isinstance(self._stored, set):
            return set(self._stored)
        return set(self._stored.get(symbol, set()))

    def upsert_fundamentals_pit(self, symbol, ratios, raw, *, report_date, source):
        self.upserts.append((symbol, report_date, ratios))


def _run_main(
    monkeypatch, tickers, store, get_cik=None, fetch_companyfacts=None, since=None
):
    monkeypatch.setattr(backfill, "HistoricalStore", lambda: store)
    # Explicit ticker lists never touch the network via resolve_universe, but
    # neutralise it anyway so a stray '' never triggers a Robinhood snapshot.
    monkeypatch.setattr(
        backfill.edgar_fundamentals, "get_cik", get_cik or (lambda sym: f"CIK-{sym}")
    )
    monkeypatch.setattr(
        backfill.edgar_fundamentals,
        "fetch_companyfacts",
        fetch_companyfacts or (lambda cik: _facts_with_one_filing()),
    )
    argv = ["backfill_edgar_fundamentals.py", "--tickers", ",".join(tickers)]
    if since is not None:
        argv += ["--since", since]
    monkeypatch.setattr(sys, "argv", argv)
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


class TestInvocationForms:
    """The blocker that survived every existing test: only these subprocess forms
    exercise the script the way cron and the MCP tool actually invoke it."""

    def test_direct_path_help_exits_zero(self):
        """`python scripts/backfill_edgar_fundamentals.py --help` — the form cron
        and trigger_edgar_backfill use. Exited 1 with ModuleNotFoundError before
        the repo-root sys.path shim."""
        r = subprocess.run(
            [sys.executable, "scripts/backfill_edgar_fundamentals.py", "--help"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        assert r.returncode == 0, f"stderr:\n{r.stderr}"

    def test_module_form_help_exits_zero(self):
        """`python -m scripts.backfill_edgar_fundamentals --help` — the form the
        weekly-edgar launchd plist uses."""
        r = subprocess.run(
            [sys.executable, "-m", "scripts.backfill_edgar_fundamentals", "--help"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        assert r.returncode == 0, f"stderr:\n{r.stderr}"


class TestIncrementalSkip:
    def test_already_stored_dates_are_skipped(self, monkeypatch):
        """A date already in the EDGAR store is not re-upserted; a new one is.
        Uses a NON-CONTIGUOUS stored set so a regression to a MAX(report_date)
        skip (which would drop the interior new date) is caught."""
        filed = ["2020-01-15", "2021-01-15", "2022-01-15", "2023-01-15"]
        # Stored: first and third. Set-skip => pending is exactly the other two.
        # A MAX-based skip (max stored = 2022) would wrongly drop 2020 and 2021.
        store = _FakeStore(stored_dates={"AAPL": {"2020-01-15", "2022-01-15"}})
        _run_main(
            monkeypatch, ["AAPL"], store,
            fetch_companyfacts=lambda cik: _facts_with_filings(filed),
        )
        written = {u[1] for u in store.upserts}
        assert written == {"2021-01-15", "2023-01-15"}

    def test_all_stored_writes_nothing(self, monkeypatch):
        """A ticker whose every filed date is already stored upserts zero rows
        (the fully-skipped steady-state weekly run) — and does not crash."""
        filed = ["2020-01-15", "2021-01-15"]
        store = _FakeStore(stored_dates={"AAPL": set(filed)})
        _run_main(
            monkeypatch, ["AAPL"], store,
            fetch_companyfacts=lambda cik: _facts_with_filings(filed),
        )
        assert store.upserts == []

    def test_skip_scoped_to_edgar_source(self, monkeypatch):
        """The backfill asks get_pit_report_dates for source='edgar' — so a
        daily yahoo_computed row at the same filed date never masks it."""
        store = _FakeStore()
        _run_main(monkeypatch, ["AAPL"], store)
        assert store.pit_calls, "get_pit_report_dates was never called"
        assert all(source == "edgar" for _, source, _ in store.pit_calls)

    def test_skip_degrades_to_process_everything(self, monkeypatch):
        """When get_pit_report_dates returns set() (its error-degrade), every
        filed date is processed — a broken skip costs time, never rows."""
        filed = ["2020-01-15", "2021-01-15", "2022-01-15"]
        store = _FakeStore(stored_dates=set())  # empty => nothing skipped
        _run_main(
            monkeypatch, ["AAPL"], store,
            fetch_companyfacts=lambda cik: _facts_with_filings(filed),
        )
        assert {u[1] for u in store.upserts} == set(filed)


class TestSkipRowIdentity:
    def test_skip_cannot_change_which_rows_land(self, monkeypatch):
        """Load-bearing: the union of already-stored + newly-written rows is
        IDENTICAL whether or not the skip fired. This is the 'can never change
        which rows land' contract."""
        filed = ["2019-06-01", "2020-06-01", "2021-06-01", "2022-06-01"]

        # (a) Fresh store, nothing skipped: writes every date.
        fresh = _FakeStore(stored_dates=set())
        _run_main(
            monkeypatch, ["AAPL"], fresh,
            fetch_companyfacts=lambda cik: _facts_with_filings(filed),
        )
        fresh_keys = {(u[0], u[1]) for u in fresh.upserts}

        # (b) Store pre-seeded as if a prior run stored two of them (skip fires).
        prior = {"2019-06-01", "2021-06-01"}
        seeded = _FakeStore(stored_dates={"AAPL": prior})
        _run_main(
            monkeypatch, ["AAPL"], seeded,
            fetch_companyfacts=lambda cik: _facts_with_filings(filed),
        )
        seeded_union = {("AAPL", d) for d in prior} | {
            (u[0], u[1]) for u in seeded.upserts
        }

        assert seeded_union == fresh_keys


class TestConcurrency:
    @pytest.mark.parametrize("workers", [1, 4])
    def test_upsert_keyset_is_deterministic_across_worker_counts(
        self, monkeypatch, workers
    ):
        """The set of (symbol, report_date) rows written is identical at
        EDGAR_MAX_CONCURRENCY 1 vs 4 (order may differ; the key-set may not)."""
        monkeypatch.setattr(backfill.settings, "EDGAR_MAX_CONCURRENCY", workers)
        filed = ["2020-01-15", "2021-01-15"]
        store = _FakeStore()
        _run_main(
            monkeypatch, ["AAPL", "MSFT", "GOOG"], store,
            fetch_companyfacts=lambda cik: _facts_with_filings(filed),
        )
        keys = {(u[0], u[1]) for u in store.upserts}
        assert keys == {
            (s, d) for s in ("AAPL", "MSFT", "GOOG") for d in filed
        }

    def test_dead_letter_under_concurrency(self, monkeypatch):
        """One bad ticker still doesn't abort the batch when the pool is active
        (an exception escaping into pool.map would kill every sibling)."""
        monkeypatch.setattr(backfill.settings, "EDGAR_MAX_CONCURRENCY", 4)
        store = _FakeStore(raise_on_get_bars_for={"BAD"})
        _run_main(monkeypatch, ["AAPL", "BAD", "MSFT"], store)
        assert {u[0] for u in store.upserts} == {"AAPL", "MSFT"}


class TestUniverseResolution:
    def test_empty_universe_aborts_without_processing(self, monkeypatch, caplog):
        """--tickers resolving to an empty universe logs an error and processes
        nothing (never a silent crash)."""
        store = _FakeStore()
        monkeypatch.setattr(backfill, "HistoricalStore", lambda: store)
        monkeypatch.setattr(backfill, "resolve_universe", lambda spec: [])
        monkeypatch.setattr(sys, "argv", ["backfill_edgar_fundamentals.py", "--tickers", "all"])
        backfill.main()
        assert store.upserts == []
        assert any("empty universe" in rec.message.lower() for rec in caplog.records)
