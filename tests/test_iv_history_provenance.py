"""Tests for the ``iv_history.source`` provenance-tracking fix.

Covers three things:
1. ``record_iv()`` tags a real-pipeline-style call (no ``source`` kwarg,
   exactly matching ``pipeline/production_steps.py``'s and
   ``technical_options_engine.py``'s call signatures) as ``IV_SOURCE_CHAIN``
   by default, and ``volatility/bootstrap_iv_history.py`` explicitly tags its
   writes as ``IV_SOURCE_SYNTHETIC_BOOTSTRAP``.
2. ``get_historical_ivs()``/``calculate_true_ivr()`` demonstrably exclude
   synthetic-bootstrap rows from ranking by default (a synthetic-ground-truth
   before/after comparison, not just "new code runs without crashing"), and
   fail closed to NaN when a ticker's ENTIRE history is synthetic.
3. The additive ``source`` column migration is idempotent and safe against a
   pre-existing database that predates provenance tracking, backfilling
   ``IV_SOURCE_LEGACY_UNKNOWN`` rather than fabricating ``IV_SOURCE_CHAIN``
   for rows whose real provenance cannot be verified (CONSTRAINT #4).

See docs/known_issues/bootstrap_iv_history_provenance_fabrication_risk.md
for the full incident/fix writeup.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from volatility.iv_engine import (
    IV_SOURCE_CHAIN,
    IV_SOURCE_LEGACY_UNKNOWN,
    IV_SOURCE_SYNTHETIC_BOOTSTRAP,
    IVHistory,
    IVHistoryStore,
    calculate_true_ivr,
)


def _raw_source(store: IVHistoryStore, ticker: str, date_str: str) -> str:
    """Read back the raw ``source`` column for one (ticker, date) row."""
    session = store.Session()
    try:
        row = (
            session.query(IVHistory)
            .filter(IVHistory.ticker == ticker, IVHistory.date == date_str)
            .first()
        )
        assert row is not None, f"no row for {ticker}/{date_str}"
        return row.source
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# record_iv() provenance tagging                                              #
# --------------------------------------------------------------------------- #
class TestRecordIvProvenanceTagging:
    def test_default_call_site_signature_tags_chain(self):
        """The real production call sites
        (``pipeline/production_steps.py:397``'s
        ``iv_store.record_iv(iv_rec[0], iv_rec[1], iv_rec[2])`` and
        ``technical_options_engine.py:1069``'s
        ``store.record_iv(symbol, resolved_as_of, current_iv)``) never pass a
        ``source`` kwarg -- both rely entirely on the default."""
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        store.record_iv("AAPL", "2026-06-01", 0.25)  # 3 positional args only
        assert _raw_source(store, "AAPL", "2026-06-01") == IV_SOURCE_CHAIN

    def test_explicit_synthetic_bootstrap_tag(self):
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        store.record_iv(
            "AAPL", "2026-06-01", 0.25, source=IV_SOURCE_SYNTHETIC_BOOTSTRAP
        )
        assert _raw_source(store, "AAPL", "2026-06-01") == IV_SOURCE_SYNTHETIC_BOOTSTRAP

    def test_upsert_updates_source_on_rewrite(self):
        """Re-recording the same (ticker, date) key overwrites both the
        value AND the source tag -- an upsert must not leave a stale
        provenance tag alongside a fresh value."""
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        store.record_iv("AAPL", "2026-06-01", 0.10, source=IV_SOURCE_SYNTHETIC_BOOTSTRAP)
        assert _raw_source(store, "AAPL", "2026-06-01") == IV_SOURCE_SYNTHETIC_BOOTSTRAP

        store.record_iv("AAPL", "2026-06-01", 0.30, source=IV_SOURCE_CHAIN)
        assert _raw_source(store, "AAPL", "2026-06-01") == IV_SOURCE_CHAIN

        session = store.Session()
        try:
            row = (
                session.query(IVHistory)
                .filter(IVHistory.ticker == "AAPL", IVHistory.date == "2026-06-01")
                .first()
            )
            assert row.iv_30d_atm == pytest.approx(0.30)
        finally:
            session.close()


# --------------------------------------------------------------------------- #
# get_historical_ivs() / calculate_true_ivr() exclusion behavior              #
# --------------------------------------------------------------------------- #
class TestSyntheticExclusionFromRanking:
    def test_synthetic_ground_truth_before_after_comparison(self):
        """The core regression proof: a synthetic-bootstrap outlier row
        planted in the history must NOT move calculate_true_ivr()'s result
        -- demonstrated by computing what the OLD (pre-fix, unfiltered)
        behavior WOULD have produced and showing it materially differs from
        the NEW (fixed) result.
        """
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        ticker = "AAPL"

        # Genuine chain-derived history: 0.10, 0.20, 0.30 on ascending dates.
        store.record_iv(ticker, "2026-06-01", 0.10, source=IV_SOURCE_CHAIN)
        store.record_iv(ticker, "2026-06-05", 0.20, source=IV_SOURCE_CHAIN)
        store.record_iv(ticker, "2026-06-10", 0.30, source=IV_SOURCE_CHAIN)
        # A synthetic-bootstrap outlier, planted BEFORE the as-of date.
        store.record_iv(ticker, "2026-06-15", 0.99, source=IV_SOURCE_SYNTHETIC_BOOTSTRAP)

        current_iv = 0.30
        as_of = "2026-06-20"

        # NEW behavior: default get_historical_ivs() call excludes the
        # synthetic row -> all_ivs = [0.10, 0.20, 0.30, 0.30] -> ivr = 100.0
        ivr_new = calculate_true_ivr(ticker, current_iv, as_of, store)
        assert ivr_new == pytest.approx(100.0)

        # Prove the synthetic row is genuinely still IN the store (this is a
        # filtering fix, not a data-loss fix) by fetching the raw, unfiltered
        # history directly.
        raw_history = store.get_historical_ivs(ticker, as_of, exclude_sources=None)
        assert len(raw_history) == 4
        assert 0.99 in raw_history

        # OLD (pre-fix) behavior, reconstructed by hand from that raw,
        # unfiltered history using calculate_true_ivr's own published
        # formula: all_ivs = raw_history + [current_iv]
        # = [0.10, 0.20, 0.30, 0.99, 0.30] -> min=0.10, max=0.99
        # -> ivr = (0.30 - 0.10) / (0.99 - 0.10) * 100 ~= 22.47
        all_ivs_old = raw_history + [current_iv]
        min_iv, max_iv = min(all_ivs_old), max(all_ivs_old)
        ivr_old = (current_iv - min_iv) / (max_iv - min_iv) * 100.0

        assert ivr_old == pytest.approx(22.4719101, abs=1e-4)
        # The fix changes the ranked value materially -- not a no-op.
        assert abs(ivr_new - ivr_old) > 50.0

    def test_wholly_synthetic_history_degrades_to_nan(self):
        """CONSTRAINT #6: if EVERY prior row for a ticker is
        synthetic-bootstrap, a real current_iv must never be ranked against
        it -- calculate_true_ivr() must fail closed to NaN, exactly like the
        pre-existing empty/warm-start-history case."""
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        ticker = "TSLA"
        store.record_iv(ticker, "2026-06-01", 0.40, source=IV_SOURCE_SYNTHETIC_BOOTSTRAP)
        store.record_iv(ticker, "2026-06-05", 0.50, source=IV_SOURCE_SYNTHETIC_BOOTSTRAP)

        # Confirm the rows are genuinely present (not simply missing).
        raw = store.get_historical_ivs(ticker, "2026-06-10", exclude_sources=None)
        assert len(raw) == 2

        ivr = calculate_true_ivr(ticker, 0.45, "2026-06-10", store)
        assert np.isnan(ivr)

    def test_legacy_unknown_rows_are_not_excluded(self):
        """A row tagged IV_SOURCE_LEGACY_UNKNOWN (backfilled by the schema
        migration for a pre-existing DB) is NOT treated as synthetic --
        excluding it would silently blank out real historical ranking on
        every pre-existing installation with no compensating safety
        benefit (see IV_SOURCE_LEGACY_UNKNOWN's own docstring)."""
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        ticker = "MSFT"
        store.record_iv(ticker, "2026-06-01", 0.20, source=IV_SOURCE_LEGACY_UNKNOWN)
        store.record_iv(ticker, "2026-06-05", 0.30, source=IV_SOURCE_LEGACY_UNKNOWN)

        filtered = store.get_historical_ivs(ticker, "2026-06-10")
        assert len(filtered) == 2

        ivr = calculate_true_ivr(ticker, 0.30, "2026-06-10", store)
        assert ivr == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# Additive migration: idempotent, safe on a pre-existing (legacy-schema) DB   #
# --------------------------------------------------------------------------- #
class TestSourceColumnMigration:
    def _create_legacy_schema_db(self, path) -> None:
        """Build a real on-disk sqlite file using the ORIGINAL (pre-fix)
        iv_history schema -- no `source` column -- with a couple of
        pre-existing rows, exactly what an operator's real database would
        look like before this fix ships."""
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                """
                CREATE TABLE iv_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker VARCHAR(10) NOT NULL,
                    date VARCHAR(10) NOT NULL,
                    iv_30d_atm FLOAT NOT NULL,
                    CONSTRAINT _ticker_date_uc UNIQUE (ticker, date)
                )
                """
            )
            conn.execute(
                "INSERT INTO iv_history (ticker, date, iv_30d_atm) VALUES (?, ?, ?)",
                ("AAPL", "2026-05-01", 0.22),
            )
            conn.execute(
                "INSERT INTO iv_history (ticker, date, iv_30d_atm) VALUES (?, ?, ?)",
                ("AAPL", "2026-05-10", 0.28),
            )
            conn.commit()
        finally:
            conn.close()

    def test_migration_adds_column_and_backfills_legacy_unknown(self, tmp_path):
        db_path = tmp_path / "legacy_iv_history.db"
        self._create_legacy_schema_db(db_path)

        store = IVHistoryStore(db_url=f"sqlite:///{db_path}")

        # The migration must not have raised, and the column must now exist
        # with the honest "we don't know" tag for the pre-existing rows.
        assert _raw_source(store, "AAPL", "2026-05-01") == IV_SOURCE_LEGACY_UNKNOWN
        assert _raw_source(store, "AAPL", "2026-05-10") == IV_SOURCE_LEGACY_UNKNOWN

        # Legacy rows still rank normally (not excluded) -- no regression
        # for an existing installation's already-accrued real history.
        history = store.get_historical_ivs("AAPL", "2026-05-15")
        assert len(history) == 2

    def test_migration_is_idempotent_across_repeated_construction(self, tmp_path):
        db_path = tmp_path / "legacy_iv_history_repeat.db"
        self._create_legacy_schema_db(db_path)

        # First construction performs the migration.
        store1 = IVHistoryStore(db_url=f"sqlite:///{db_path}")
        assert _raw_source(store1, "AAPL", "2026-05-01") == IV_SOURCE_LEGACY_UNKNOWN

        # Second construction against the SAME already-migrated file must
        # not raise (e.g. "duplicate column name: source") and must leave
        # the data untouched.
        store2 = IVHistoryStore(db_url=f"sqlite:///{db_path}")
        assert _raw_source(store2, "AAPL", "2026-05-01") == IV_SOURCE_LEGACY_UNKNOWN
        assert _raw_source(store2, "AAPL", "2026-05-10") == IV_SOURCE_LEGACY_UNKNOWN

        # New writes against the migrated file still tag correctly.
        store2.record_iv("AAPL", "2026-05-20", 0.35)
        assert _raw_source(store2, "AAPL", "2026-05-20") == IV_SOURCE_CHAIN

    def test_fresh_database_needs_no_migration(self):
        """A brand-new database (the common case: :memory: or a fresh file)
        gets `source` straight from the CREATE TABLE -- the migration probe
        must recognize this and not attempt (or need) an ALTER TABLE."""
        store = IVHistoryStore(db_url="sqlite:///:memory:")
        store.record_iv("AAPL", "2026-06-01", 0.25)
        assert _raw_source(store, "AAPL", "2026-06-01") == IV_SOURCE_CHAIN


# --------------------------------------------------------------------------- #
# bootstrap_iv_history.py: real writes are tagged synthetic_bootstrap         #
# --------------------------------------------------------------------------- #
class _FakeYfTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, start=None):
        # ~170 calendar days of business-day bars -- enough for the 20-day
        # rolling std plus a `--days 30` slice to leave several rows.
        idx = pd.bdate_range(
            end=pd.Timestamp.now(tz="America/New_York").normalize(), periods=120
        )
        rng = np.random.default_rng(42)
        # Deterministic small random walk -- non-degenerate rolling std.
        rets = rng.normal(loc=0.0002, scale=0.01, size=len(idx))
        close = 100.0 * np.cumprod(1.0 + rets)
        return pd.DataFrame({"Close": close}, index=idx)


class TestBootstrapScriptTagsSyntheticBootstrap:
    def test_main_writes_are_tagged_synthetic_bootstrap(self, monkeypatch):
        import volatility.bootstrap_iv_history as bih

        monkeypatch.setattr(bih.yf, "Ticker", lambda symbol: _FakeYfTicker(symbol))
        monkeypatch.setattr(sys, "argv", ["bootstrap_iv_history.py", "--tickers", "AAPL", "--days", "30"])

        # bootstrap_iv_history.main() constructs its own IVHistoryStore()
        # with no db_url override, which would otherwise hit the real,
        # git-committed, on-disk quant_platform.db. Force that one
        # construction onto an isolated in-memory DB and capture the
        # resulting instance so we can inspect what it wrote.
        captured_store = {}
        original_init = IVHistoryStore.__init__

        def _mem_capture_init(self, db_url=None, *a, **kw):
            original_init(self, db_url="sqlite:///:memory:", *a, **kw)
            captured_store["store"] = self

        monkeypatch.setattr(IVHistoryStore, "__init__", _mem_capture_init)
        bih.main()

        store = captured_store["store"]
        session = store.Session()
        try:
            rows = session.query(IVHistory).filter(IVHistory.ticker == "AAPL").all()
        finally:
            session.close()

        assert len(rows) > 0, "bootstrap script recorded no rows for the fake price series"
        assert all(r.source == IV_SOURCE_SYNTHETIC_BOOTSTRAP for r in rows)
        assert not any(r.source == IV_SOURCE_CHAIN for r in rows)
