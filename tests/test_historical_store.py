"""
tests/test_historical_store.py — Tier 2.3 Phase 1 + Phase 2 + Phase 3

All tests are fully offline: no network calls, no real quant_platform.db.
Every test uses a fresh temporary SQLite database via pytest's tmp_path fixture.

Data convention: _make_ohlcv(...) generates rows ending at TODAY by default so
that _read_from_db's date-cutoff filter (today - lookback_days) always includes
the test data.  Tests that exercise the incremental-delta logic seed the DB with
rows ending N business days ago and provide a delta frame ending today.

Phase 3 tests verify:
  - fundamentals_history table schema + incremental TTL cache
  - NaN (not 0.0) for missing fundamentals fields (CONSTRAINT #4)
  - macro_history round-trip and incremental top-up
  - Dead-letter resilience (total failure → empty sentinels, no raise)
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.historical_store import HistoricalStore, _DF_COLUMNS


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — bars (Phase 1)
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int, *, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Return a synthetic OHLCV DataFrame with *n* business-day rows ending at *end*.

    Defaults to ending at today so all rows fall within any realistic lookback
    window (important: _read_from_db filters by today − lookback_days).
    """
    if end is None:
        end = pd.Timestamp.now().normalize()
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(
        {
            "Open":   [100.0 + i * 0.1 for i in range(n)],
            "High":   [101.0 + i * 0.1 for i in range(n)],
            "Low":    [99.0  + i * 0.1 for i in range(n)],
            "Close":  [100.5 + i * 0.1 for i in range(n)],
            "Volume": [1_000_000 + i    for i in range(n)],
        },
        index=dates,
    )


def _make_provider(df: pd.DataFrame) -> MagicMock:
    """Return a mock provider whose get_intraday_bars() returns *df*."""
    p = MagicMock()
    p.get_intraday_bars.return_value = df
    p.source_name = "yfinance"
    return p


def _make_raising_provider() -> MagicMock:
    p = MagicMock()
    p.get_intraday_bars.side_effect = RuntimeError("network down")
    p.source_name = "yfinance"
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — account snapshots (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

def _make_account_snapshot(age_hours: float = 0.0, n_positions: int = 3):
    """Build a synthetic AccountSnapshot using the real dataclasses."""
    from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition

    fetched_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    positions = {}
    for i in range(n_positions):
        sym = f"SYM{i}"
        qty = 10.0 + i
        avg_cost = 100.0 + i * 5.0
        current_price = 110.0 + i * 5.0
        market_value = qty * current_price
        cost_basis = qty * avg_cost
        unrealized_pl = market_value - cost_basis
        unrealized_pl_pct = (unrealized_pl / cost_basis * 100.0) if cost_basis > 0 else 0.0
        positions[sym] = PortfolioPosition(
            symbol=sym,
            quantity=qty,
            average_cost=avg_cost,
            current_price=current_price,
            market_value=market_value,
            unrealized_pl=unrealized_pl,
            unrealized_pl_pct=unrealized_pl_pct,
            dividends_received=5.0 * i,
            name=f"Symbol {i}",
        )
    return AccountSnapshot(
        positions=positions,
        buying_power=1000.0,
        total_equity=5000.0,
        total_dividends=15.0,
        fetched_at=fetched_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — TestTableCreation
# ─────────────────────────────────────────────────────────────────────────────

class TestTableCreation:
    def test_table_created_on_init(self, tmp_path):
        db = str(tmp_path / "test.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
        assert "price_bars" in tables
        assert "idx_price_bars_symbol_date" in indexes
        # Phase 2 tables also created at init
        assert "account_snapshots" in tables
        assert "account_positions" in tables
        assert "idx_acct_snap_ts" in indexes
        # Phase 3 tables also created at init
        assert "fundamentals_history" in tables
        assert "macro_history" in tables
        assert "idx_fund_history_symbol" in indexes
        assert "idx_macro_history_series" in indexes

    def test_init_idempotent(self, tmp_path):
        """Calling __init__ twice must not raise or corrupt the DB."""
        db = str(tmp_path / "test.db")
        HistoricalStore(db_path=db)
        HistoricalStore(db_path=db)


class TestSchemaVersion:
    """schema_version is a diagnostic stamp -- see the DDL comment in
    data/historical_store.py for what it is (and is not) a guard against."""

    def test_fresh_db_stamped_with_current_version(self, tmp_path):
        from data.historical_store import CURRENT_SCHEMA_VERSION

        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION

    def test_older_version_row_is_bumped_on_init(self, tmp_path):
        from data.historical_store import CURRENT_SCHEMA_VERSION

        db = str(tmp_path / "test.db")
        # Seed a pre-existing DB stamped at an older version.
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE schema_version SET version = 0 WHERE id = 1")
            conn.commit()

        store = HistoricalStore(db_path=db)
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION

    def test_newer_version_row_is_left_alone_and_warns(self, tmp_path, caplog):
        """A DB stamped by a newer build must not be silently downgraded --
        only warned about (CONSTRAINT #6: diagnostic, not a hard gate)."""
        import logging

        db = str(tmp_path / "test.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE schema_version SET version = 999 WHERE id = 1")
            conn.commit()

        with caplog.at_level(logging.WARNING, logger="data.historical_store"):
            store = HistoricalStore(db_path=db)

        assert store.get_schema_version() == 999
        assert any("NEWER" in rec.message for rec in caplog.records)

    def test_get_schema_version_none_when_unset(self, tmp_path):
        """A row-less schema_version table (e.g. DB predating this stamp)
        degrades to None, never a fabricated version number."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM schema_version")
            conn.commit()
        assert store.get_schema_version() is None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — TestLatestBarDate
# ─────────────────────────────────────────────────────────────────────────────

class TestLatestBarDate:
    def test_none_on_empty_db(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        assert store.latest_bar_date("AAPL") is None

    def test_returns_most_recent(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        df = _make_ohlcv(10)   # ends today
        provider = _make_provider(df)
        store.get_bars("AAPL", lookback_days=30, provider=provider)
        latest = store.latest_bar_date("AAPL")
        assert latest is not None
        expected_last = df.index[-1].normalize()
        assert latest.normalize() == expected_last


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — TestGetBars
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBars:
    def test_first_fetch_full_backfill(self, tmp_path):
        """Cold-start: provider called once with settings.BARS_BACKFILL_DAYS lookback."""
        from settings import settings  # real default: 504

        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)

        # Produce enough rows to cover the default lookback window.
        df_big = _make_ohlcv(settings.BARS_BACKFILL_DAYS)
        provider = _make_provider(df_big)

        result = store.get_bars("AAPL", lookback_days=settings.BARS_BACKFILL_DAYS, provider=provider)

        # Provider called exactly once on a cold start.
        assert provider.get_intraday_bars.call_count == 1
        # The lookback_days passed to the provider equals BARS_BACKFILL_DAYS.
        lookback_passed = provider.get_intraday_bars.call_args[1]["lookback_days"]
        assert lookback_passed == settings.BARS_BACKFILL_DAYS
        # DB was populated and result is non-empty.
        assert len(result) > 0
        with sqlite3.connect(db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM price_bars WHERE symbol='AAPL'"
            ).fetchone()[0]
        assert count > 0

    def test_incremental_delta_only(self, tmp_path):
        """Warm-start: provider fetches a small delta, NOT a full backfill."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)

        # Seed the DB with rows ending 5 business days ago so max_date is recent.
        five_days_ago = pd.Timestamp.now().normalize() - pd.offsets.BDay(5)
        df_seed = _make_ohlcv(200, end=five_days_ago)
        store._upsert_bars("AAPL", df_seed, source="yfinance")

        assert store.latest_bar_date("AAPL") is not None

        # Delta: only a tiny frame for the missing days.
        df_delta = _make_ohlcv(7)  # ends today
        provider = _make_provider(df_delta)

        result = store.get_bars("AAPL", lookback_days=250, provider=provider)

        assert provider.get_intraday_bars.call_count == 1
        delta_lookback = provider.get_intraday_bars.call_args[1]["lookback_days"]
        # Delta lookback must be well under the full BARS_BACKFILL_DAYS (504).
        assert delta_lookback < 100, (
            f"Expected small incremental lookback but got {delta_lookback}"
        )
        assert not result.empty

    def test_shape_matches_data_engine(self, tmp_path):
        """Returned DataFrame must satisfy the shape contract."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        df = _make_ohlcv(30)  # ends today → within the 60-day lookback window
        provider = _make_provider(df)
        result = store.get_bars("AAPL", lookback_days=60, provider=provider)

        assert not result.empty
        assert result.index.tz is None, "Index must be tz-naive"
        assert list(result.columns) == _DF_COLUMNS, f"Columns: {list(result.columns)}"
        assert result.index.is_monotonic_increasing

    def test_no_fabrication_on_total_failure(self, tmp_path):
        """Empty DB + provider raises → empty DataFrame, never fabricated rows."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        provider = _make_raising_provider()
        result = store.get_bars("AAPL", lookback_days=504, provider=provider)
        assert result.empty
        assert list(result.columns) == _DF_COLUMNS

    def test_dead_letter_db_error(self, tmp_path):
        """sqlite3.connect raises → falls back to live provider, never raises."""
        df = _make_ohlcv(10)
        provider = _make_provider(df)
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk error")):
            result = store.get_bars("AAPL", lookback_days=20, provider=provider)

        assert not result.empty

    def test_upsert_idempotent(self, tmp_path):
        """Calling _upsert_bars twice with the same rows keeps row count stable."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        df = _make_ohlcv(30)

        store._upsert_bars("AAPL", df, source="yfinance")
        store._upsert_bars("AAPL", df, source="yfinance")  # second write

        with sqlite3.connect(db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM price_bars WHERE symbol='AAPL'"
            ).fetchone()[0]
        assert count == len(df)  # no duplicates

    def test_up_to_date_skips_provider(self, tmp_path):
        """If max_date == today, the network round-trip is skipped entirely."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)

        # Seed with rows ending exactly on today's *calendar* date. Note:
        # _make_ohlcv() uses pd.bdate_range, which rolls back to the prior
        # business day when today is a weekend/holiday — that would seed a
        # max_date short of "today" and defeat the up-to-date check this
        # test exists to exercise. Build the frame directly so the last row
        # always lands on today regardless of what day of the week it is.
        today = pd.Timestamp.now().normalize()
        df_today = _make_ohlcv(9)
        df_today = pd.concat([
            df_today,
            _make_ohlcv(1, end=today).set_axis([today]),
        ])
        store._upsert_bars("AAPL", df_today, source="yfinance")
        assert store.latest_bar_date("AAPL").normalize() == today

        provider = _make_provider(_make_ohlcv(10))
        store.get_bars("AAPL", lookback_days=30, provider=provider)

        # Provider must NOT be called — we're already up to date.
        assert provider.get_intraday_bars.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — TestGetBarsBulk (2026-08 performance fix)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBarsBulk:
    """get_bars_bulk() concurrently fetches multiple symbols via a bounded
    ThreadPoolExecutor. Two concerns proven here: (1) the method actually
    exists and runs without NameError (PR 725's version omitted the
    `ThreadPoolExecutor` import entirely, guaranteeing one on every call);
    (2) one symbol's failure never drops any other symbol's successful
    result (CLAUDE.md's per-ticker try/except convention).
    """

    def test_mixed_success_and_failure_isolates_per_symbol(self, tmp_path, monkeypatch):
        """One symbol raising inside get_bars() must not affect the others --
        the bulk call returns only the symbols that actually succeeded."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        good_df = _make_ohlcv(10)

        def _fake_get_bars(symbol, lookback_days=504, *, provider=None):
            if symbol == "BAD":
                raise RuntimeError("provider exploded for BAD")
            return good_df

        monkeypatch.setattr(store, "get_bars", _fake_get_bars)

        result = store.get_bars_bulk(["AAPL", "BAD", "MSFT"], lookback_days=30)

        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert "BAD" not in result
        for df in result.values():
            assert not df.empty

    def test_multi_symbol_call_exercises_thread_pool_without_nameerror(self, tmp_path, monkeypatch):
        """A basic multi-symbol call with workers > 1 must not raise NameError
        (the exact bug PR 725 shipped by omitting the ThreadPoolExecutor
        import) and must return a result for every symbol."""
        from settings import settings as _settings

        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        good_df = _make_ohlcv(10)
        monkeypatch.setattr(store, "get_bars", lambda symbol, lookback_days=504, **kw: good_df)
        monkeypatch.setattr(_settings, "DATA_FETCH_MAX_CONCURRENCY", 4, raising=False)

        symbols = ["AAPL", "MSFT", "GOOG", "TSLA"]
        result = store.get_bars_bulk(symbols, lookback_days=30)

        assert set(result.keys()) == set(symbols)

    def test_empty_symbol_list_returns_empty_dict(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        assert store.get_bars_bulk([], lookback_days=30) == {}

    def test_symbols_uppercased(self, tmp_path, monkeypatch):
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        good_df = _make_ohlcv(5)
        seen = []

        def _fake_get_bars(symbol, lookback_days=504, *, provider=None):
            seen.append(symbol)
            return good_df

        monkeypatch.setattr(store, "get_bars", _fake_get_bars)
        result = store.get_bars_bulk(["aapl"], lookback_days=30)

        assert seen == ["AAPL"]
        assert "AAPL" in result

    def test_all_symbols_fail_returns_empty_dict(self, tmp_path, monkeypatch):
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))

        def _always_raise(symbol, lookback_days=504, *, provider=None):
            raise RuntimeError("total outage")

        monkeypatch.setattr(store, "get_bars", _always_raise)
        result = store.get_bars_bulk(["AAPL", "MSFT"], lookback_days=30)
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — TestColumnContract
# ─────────────────────────────────────────────────────────────────────────────

class TestColumnContract:
    def test_adj_close_stored_but_not_in_output(self, tmp_path):
        """adj_close is stored in the DB but not exposed in the public DataFrame."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        df = _make_ohlcv(5)
        df["Adj Close"] = df["Close"] * 0.99
        provider = _make_provider(df)
        result = store.get_bars("AAPL", lookback_days=10, provider=provider)
        assert "Adj Close" not in result.columns
        assert "adj_close" not in result.columns
        assert list(result.columns) == _DF_COLUMNS

    def test_volume_is_present(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        df = _make_ohlcv(5)
        provider = _make_provider(df)
        result = store.get_bars("AAPL", lookback_days=10, provider=provider)
        assert "Volume" in result.columns
        assert result["Volume"].notna().all()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — TestAccountSnapshotPersistence
# ─────────────────────────────────────────────────────────────────────────────

class TestAccountSnapshotPersistence:
    """Tests for save_account_snapshot / latest_account_snapshot /
    account_snapshot_history."""

    def test_save_and_load_round_trip(self, tmp_path):
        """Save a 3-position snapshot; loading returns an equal AccountSnapshot."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        original = _make_account_snapshot(age_hours=0.5, n_positions=3)

        snapshot_id = store.save_account_snapshot(original)
        assert snapshot_id > 0, "Expected a positive snapshot_id on success"

        loaded = store.latest_account_snapshot()
        assert loaded is not None

        # Account-level fields
        assert loaded.buying_power == pytest.approx(original.buying_power)
        assert loaded.total_equity == pytest.approx(original.total_equity)
        assert loaded.total_dividends == pytest.approx(original.total_dividends)

        # fetched_at round-trips losslessly through ISO-8601
        dt_delta = abs((loaded.fetched_at - original.fetched_at).total_seconds())
        assert dt_delta < 0.001, f"fetched_at drifted by {dt_delta}s"

        # Positions
        assert set(loaded.positions.keys()) == set(original.positions.keys())
        for sym, orig_pos in original.positions.items():
            loaded_pos = loaded.positions[sym]
            assert loaded_pos.quantity == pytest.approx(orig_pos.quantity)
            assert loaded_pos.average_cost == pytest.approx(orig_pos.average_cost)
            assert loaded_pos.current_price == pytest.approx(orig_pos.current_price)
            assert loaded_pos.market_value == pytest.approx(orig_pos.market_value)
            assert loaded_pos.unrealized_pl == pytest.approx(orig_pos.unrealized_pl)
            assert loaded_pos.dividends_received == pytest.approx(orig_pos.dividends_received)
            assert loaded_pos.name == orig_pos.name

    def test_save_failure_does_not_raise(self, tmp_path):
        """DB connect error → save_account_snapshot returns -1, never raises."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        snap = _make_account_snapshot()

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("full disk")):
            result = store.save_account_snapshot(snap)

        assert result == -1

    def test_latest_with_empty_db(self, tmp_path):
        """Empty DB → latest_account_snapshot returns None."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        assert store.latest_account_snapshot() is None

    def test_multiple_snapshots_returns_newest(self, tmp_path):
        """With two snapshots stored, latest_account_snapshot returns the newer one."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)

        older = _make_account_snapshot(age_hours=2.0)
        newer = _make_account_snapshot(age_hours=1.0)

        # Save older first, then newer
        store.save_account_snapshot(older)
        store.save_account_snapshot(newer)

        loaded = store.latest_account_snapshot()
        assert loaded is not None
        # The newer snapshot's fetched_at should be closer to now
        assert loaded.fetched_at >= older.fetched_at

    def test_history_dataframe_shape(self, tmp_path):
        """Saving 3 snapshots → history() returns 3-row DataFrame with 4 columns."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)

        for i in range(3):
            store.save_account_snapshot(_make_account_snapshot(age_hours=float(i)))

        history = store.account_snapshot_history()
        assert not history.empty
        assert len(history) == 3
        expected_cols = {"fetched_at", "buying_power", "total_equity", "total_dividends"}
        assert expected_cols.issubset(set(history.columns))

    def test_no_secrets_in_db(self, tmp_path):
        """Neither account_snapshots nor account_positions contains credential columns."""
        db = str(tmp_path / "test.db")
        HistoricalStore(db_path=db)

        forbidden = {"password", "mfa", "token", "secret", "credential"}
        with sqlite3.connect(db) as conn:
            for table in ("account_snapshots", "account_positions"):
                pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
                col_names = {row[1].lower() for row in pragma}
                hits = col_names & forbidden
                assert not hits, (
                    f"Table '{table}' has forbidden column(s): {hits}"
                )

    def test_history_since_filter(self, tmp_path):
        """account_snapshot_history(since=T) only returns snapshots after T."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)

        old = _make_account_snapshot(age_hours=5.0)
        recent = _make_account_snapshot(age_hours=1.0)
        store.save_account_snapshot(old)
        store.save_account_snapshot(recent)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
        history = store.account_snapshot_history(since=cutoff)
        assert len(history) == 1  # only the 1-hour-old one qualifies

    def test_history_error_returns_empty_df(self, tmp_path):
        """DB error → account_snapshot_history returns an empty DataFrame."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk")):
            df = store.account_snapshot_history()
        assert df.empty
        assert "fetched_at" in df.columns

    def test_save_empty_positions(self, tmp_path):
        """Snapshot with no positions saves and loads without error."""
        from data.robinhood_portfolio import AccountSnapshot

        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        snap = AccountSnapshot(
            positions={},
            buying_power=500.0,
            total_equity=500.0,
            total_dividends=0.0,
            fetched_at=datetime.now(timezone.utc),
        )
        sid = store.save_account_snapshot(snap)
        assert sid > 0

        loaded = store.latest_account_snapshot()
        assert loaded is not None
        assert loaded.positions == {}
        assert loaded.buying_power == pytest.approx(500.0)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — fundamentals (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def _make_raw_fundamentals() -> dict:
    """Return a yfinance-style fundamentals dict with all expected keys."""
    return {
        "trailingPE":       25.0,
        "priceToBook":      4.5,
        "returnOnEquity":   0.32,
        "dividendYield":    0.015,
        "marketCap":        3_000_000_000.0,
        "trailingEps":      5.50,
        "operatingMargins": 0.25,
        "debtToEquity":     50.0,   # yfinance percent format → DB stores 0.50
    }


def _make_mock_provider(raw: dict | None = None) -> MagicMock:
    """Return a mock provider whose get_fundamentals returns *raw*."""
    p = MagicMock()
    p.get_fundamentals.return_value = raw if raw is not None else _make_raw_fundamentals()
    p.source_name = "yfinance_test"
    return p


def _make_raising_fund_provider() -> MagicMock:
    """Return a mock provider whose get_fundamentals raises."""
    p = MagicMock()
    p.get_fundamentals.side_effect = RuntimeError("provider down")
    p.source_name = "yfinance_test"
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — TestFundamentalsHistory
# ─────────────────────────────────────────────────────────────────────────────

class TestFundamentalsHistory:
    """Tests for get_fundamentals / get_fundamentals_history."""

    def test_first_fetch_writes_row(self, tmp_path):
        """Empty DB + mock provider → get_fundamentals returns typed dict;
        DB has one row with as_of=today and raw_json set."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider()

        result = store.get_fundamentals("AAPL", provider=provider)

        assert isinstance(result, dict)
        assert result.get("pe_ratio") == pytest.approx(25.0)
        assert result.get("pb_ratio") == pytest.approx(4.5)
        # provider was called exactly once (cache miss on empty DB)
        provider.get_fundamentals.assert_called_once_with("AAPL")

        # Verify DB row was written
        with _sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT as_of, raw_json FROM fundamentals_history WHERE symbol='AAPL'"
            ).fetchone()
        assert row is not None
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert row[0] == today_str
        assert "trailingPE" in row[1]  # raw_json preserved

    def test_within_max_age_skips_provider(self, tmp_path):
        """Seed DB with today's row; a second call must NOT hit the provider."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider()

        # First call writes the row
        store.get_fundamentals("AAPL", max_age_days=1, provider=provider)
        call_count_after_first = provider.get_fundamentals.call_count

        # Second call with max_age_days=1 — row is fresh (just written today)
        store.get_fundamentals("AAPL", max_age_days=1, provider=provider)
        # provider must NOT be called a second time
        assert provider.get_fundamentals.call_count == call_count_after_first

    def test_stale_row_refetches(self, tmp_path):
        """Row 5 days old with max_age_days=1 → provider IS called again."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider()

        # Manually insert a stale row (5 days ago)
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        with _sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fundamentals_history
                    (symbol, as_of, pe_ratio, raw_json, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("AAPL", five_days_ago, 20.0, '{"trailingPE":20.0}',
                 "yfinance", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

        # Call with max_age_days=1 — the 5-day-old row is stale
        result = store.get_fundamentals("AAPL", max_age_days=1, provider=provider)

        # Provider must have been called to refetch
        provider.get_fundamentals.assert_called_once()
        # Result should reflect the fresh provider data (pe_ratio=25)
        assert result.get("pe_ratio") == pytest.approx(25.0)

    def test_missing_fields_are_nan_not_zero(self, tmp_path):
        """Provider returns only trailingPE → pb_ratio must be NaN, not 0.0.
        CONSTRAINT #4: never fabricate a zero for a missing field."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider(raw={"trailingPE": 18.0})

        result = store.get_fundamentals("GOOG", provider=provider)

        assert result.get("pe_ratio") == pytest.approx(18.0)
        # All missing fields must be NaN — not 0.0
        for col in ("pb_ratio", "roe", "dividend_yield", "market_cap",
                    "eps", "operating_margin", "debt_to_equity"):
            val = result.get(col)
            assert val is not None, f"{col} must be present (NaN sentinel)"
            assert math.isnan(val), (
                f"{col} should be NaN for a missing field; got {val}"
            )

    def test_total_failure_returns_empty_dict(self, tmp_path):
        """Provider raises AND DB error → get_fundamentals returns {}; never raises."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_raising_fund_provider()

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk full")):
            result = store.get_fundamentals("FAIL", provider=provider)

        assert result == {}

    def test_empty_provider_response_not_cached_as_fresh(self, tmp_path):
        """Provider returns {} (not a raise, just nothing) → the all-NaN result
        must NOT be upserted into the DB. Caching it would make the next call
        within max_age_days read back a stale "fresh" cache hit instead of
        retrying the live fetch — a cache-poisoning regression."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider(raw={})

        result = store.get_fundamentals("EMPTY", max_age_days=1, provider=provider)

        # Result is the all-NaN sentinel dict, never fabricated zeros.
        assert isinstance(result, dict)
        for val in result.values():
            assert math.isnan(val)

        # No row was written for an empty response.
        with _sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT 1 FROM fundamentals_history WHERE symbol='EMPTY'"
            ).fetchone()
        assert row is None

        # A second call within max_age_days must retry the provider — there
        # is no cached row to (wrongly) treat as fresh.
        store.get_fundamentals("EMPTY", max_age_days=1, provider=provider)
        assert provider.get_fundamentals.call_count == 2

    def test_fundamentals_history_dataframe_shape(self, tmp_path):
        """After two daily writes, get_fundamentals_history returns correct columns."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider()

        # Write one fresh row
        store.get_fundamentals("MSFT", provider=provider)

        hist = store.get_fundamentals_history("MSFT")
        assert not hist.empty
        expected_cols = {"as_of", "pe_ratio", "pb_ratio", "roe",
                         "dividend_yield", "market_cap"}
        assert expected_cols.issubset(set(hist.columns))

    def test_fundamentals_history_empty_returns_correct_schema(self, tmp_path):
        """Empty DB → get_fundamentals_history returns DataFrame with correct columns."""
        store = HistoricalStore(db_path=str(tmp_path / "fund.db"))
        hist = store.get_fundamentals_history("UNKNOWN")
        assert hist.empty
        assert "as_of" in hist.columns
        assert "pe_ratio" in hist.columns

    def test_debt_to_equity_converted_from_percent(self, tmp_path):
        """yfinance returns debtToEquity as percent (e.g. 50.0); DB stores /100."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider(raw={"debtToEquity": 150.0})

        result = store.get_fundamentals("XOM", provider=provider)

        # 150.0 / 100 = 1.5
        assert result.get("debt_to_equity") == pytest.approx(1.5)


# ─────────────────────────────────────────────────────────────────────────────
# Phase A2 — TestGetFundamentalsRaw
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFundamentalsRaw:
    """Tests for get_fundamentals_raw() — the full-raw-dict counterpart to
    get_fundamentals()'s narrow 8-typed-column shape, needed by
    engine/advisory.py so FundamentalDataDTO.from_raw_dict() doesn't silently
    lose fields (sector, company_name, book_value, payout_ratio, etc.) that
    the typed columns don't carry."""

    def test_fresh_cache_hit_returns_raw_dict_no_provider_call(self, tmp_path):
        """A fresh row (written moments ago) must return the FULL raw dict
        parsed from raw_json, WITHOUT calling the provider again."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        raw = _make_raw_fundamentals()
        raw["sector"] = "Technology"
        raw["shortName"] = "Test Co"
        provider = _make_mock_provider(raw=raw)

        # First call writes the row (cache miss).
        first = store.get_fundamentals_raw("AAPL", provider=provider)
        assert first.get("sector") == "Technology"
        assert first.get("shortName") == "Test Co"
        assert provider.get_fundamentals.call_count == 1

        # Second call within max_age_days — must be a pure cache hit.
        second = store.get_fundamentals_raw("AAPL", max_age_days=1, provider=provider)
        assert second.get("sector") == "Technology"
        assert second.get("shortName") == "Test Co"
        # Provider must NOT have been called again.
        assert provider.get_fundamentals.call_count == 1

    def test_stale_or_missing_row_calls_provider_and_persists(self, tmp_path):
        """A missing row falls straight through to a live fetch, and persists
        (both typed columns AND raw_json) via the same upsert path
        get_fundamentals() uses."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider()

        result = store.get_fundamentals_raw("MSFT", provider=provider)

        assert isinstance(result, dict)
        assert "trailingPE" in result
        provider.get_fundamentals.assert_called_once_with("MSFT")

    def test_stale_row_refetches(self, tmp_path):
        """A row older than max_age_days triggers a live refetch."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider(raw={"trailingPE": 30.0, "sector": "Energy"})

        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        with _sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fundamentals_history
                    (symbol, as_of, pe_ratio, raw_json, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("XOM", five_days_ago, 20.0, '{"sector": "Old Sector"}',
                 "yfinance", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

        result = store.get_fundamentals_raw("XOM", max_age_days=1, provider=provider)

        provider.get_fundamentals.assert_called_once()
        assert result.get("sector") == "Energy"

    def test_total_failure_returns_empty_dict(self, tmp_path):
        """DB error + provider error → {} (never fabricated — CONSTRAINT #4)."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_raising_fund_provider()

        def _broken_conn(*a, **kw):
            raise RuntimeError("simulated DB failure")

        store._get_conn = _broken_conn  # type: ignore[assignment]

        result = store.get_fundamentals_raw("FAIL", provider=provider)
        assert result == {}

    def test_missing_raw_json_falls_through_to_live_fetch(self, tmp_path):
        """A fresh row whose raw_json is NULL (e.g. written by an older code
        path) must fall through to a live fetch rather than returning {}."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider(raw={"trailingPE": 22.0, "sector": "Healthcare"})

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with _sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fundamentals_history
                    (symbol, as_of, pe_ratio, raw_json, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("NVDA", today_str, 40.0, None, "yfinance", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

        result = store.get_fundamentals_raw("NVDA", max_age_days=1, provider=provider)

        provider.get_fundamentals.assert_called_once()
        assert result.get("sector") == "Healthcare"

    def test_round_trip_consistency_with_get_fundamentals(self, tmp_path):
        """After the SAME upsert, get_fundamentals()'s typed columns and
        get_fundamentals_raw()'s raw dict must agree on overlapping fields."""
        db = str(tmp_path / "fund.db")
        store = HistoricalStore(db_path=db)
        provider = _make_mock_provider(raw={"trailingPE": 18.5, "sector": "Financials"})

        raw_result = store.get_fundamentals_raw("JPM", provider=provider)
        typed_result = store.get_fundamentals("JPM", provider=provider)

        assert raw_result.get("trailingPE") == pytest.approx(18.5)
        assert typed_result.get("pe_ratio") == pytest.approx(18.5)
        # Only ONE provider call total across both methods (second is a cache hit).
        assert provider.get_fundamentals.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — macro (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def _make_macro_df(n: int = 100, *, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Return a synthetic macro DataFrame with VIXCLS and T10Y2Y columns."""
    if end is None:
        end = pd.Timestamp.now(tz=None).normalize()
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(
        {
            "VIXCLS": [15.0 + i * 0.05 for i in range(n)],
            "T10Y2Y": [0.5  + i * 0.01 for i in range(n)],
        },
        index=dates,
    )


def _make_mock_data_engine(macro_df: pd.DataFrame) -> MagicMock:
    """Return a mock DataEngine whose fetch_macro_history returns *macro_df*."""
    de = MagicMock()
    de.fetch_macro_history.return_value = macro_df
    return de


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — TestMacroHistory
# ─────────────────────────────────────────────────────────────────────────────

class TestMacroHistory:
    """Tests for get_macro."""

    def test_macro_round_trip(self, tmp_path):
        """Mock DataEngine with 100-row frame → get_macro('VIXCLS') returns
        a 100-element Series with correct values."""
        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)
        macro_df = _make_macro_df(100)
        de = _make_mock_data_engine(macro_df)

        series = store.get_macro("VIXCLS", data_engine=de)

        assert isinstance(series, pd.Series)
        assert len(series) == 100
        assert series.name == "VIXCLS"
        assert series.index.tz is None, "Index must be tz-naive"
        # Spot-check a value
        assert series.iloc[0] == pytest.approx(15.0)
        assert series.iloc[-1] == pytest.approx(15.0 + 99 * 0.05)

    def test_macro_incremental(self, tmp_path):
        """Pre-seed DB with 90 rows; a second call should NOT re-insert them all."""
        import sqlite3 as _sqlite3

        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)

        # First call seeds the DB with 90 rows
        macro_df_90 = _make_macro_df(90)
        de = _make_mock_data_engine(macro_df_90)
        store.get_macro("VIXCLS", data_engine=de)

        # Count initial rows
        with _sqlite3.connect(db) as conn:
            count_after_first = conn.execute(
                "SELECT COUNT(*) FROM macro_history WHERE series_id='VIXCLS'"
            ).fetchone()[0]
        assert count_after_first == 90

        # Force stale so a top-up fires (patch fetched_at to 25 hours ago)
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with _sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE macro_history SET fetched_at=? WHERE series_id='VIXCLS'",
                (stale_ts,),
            )
            conn.commit()

        # Second call: DataEngine returns same 90 rows → INSERT OR REPLACE is idempotent
        de2 = _make_mock_data_engine(macro_df_90)
        store.get_macro("VIXCLS", data_engine=de2)

        with _sqlite3.connect(db) as conn:
            count_after_second = conn.execute(
                "SELECT COUNT(*) FROM macro_history WHERE series_id='VIXCLS'"
            ).fetchone()[0]
        # INSERT OR REPLACE is idempotent — count must not grow beyond 90
        assert count_after_second == 90
        # DataEngine was called on second run (forced stale)
        de2.fetch_macro_history.assert_called_once()

    def test_macro_upsert_applies_fred_revision(self, tmp_path):
        """A later top-up that returns a DIFFERENT value for an already-stored
        date (e.g. FRED revises a past VIXCLS/T10Y2Y print) must overwrite the
        stored value with the latest one, not silently keep the first-written
        value or raise on the primary-key (series_id, date) conflict.

        macro_history's write path (_upsert_macro) uses ``INSERT OR REPLACE``
        keyed on (series_id, date), which is SQLite's upsert idiom — this test
        locks in that a revision is actually applied end-to-end through
        get_macro(), not just at the raw SQL level.
        """
        import sqlite3 as _sqlite3

        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)

        # First top-up: VIXCLS on the last date = 15.0 + 29*0.05 = 16.45
        macro_df_v1 = _make_macro_df(30)
        de_v1 = _make_mock_data_engine(macro_df_v1)
        series_v1 = store.get_macro("VIXCLS", data_engine=de_v1)
        last_date = macro_df_v1.index[-1]
        original_value = float(macro_df_v1["VIXCLS"].iloc[-1])
        assert series_v1.loc[last_date] == pytest.approx(original_value)

        # Force stale so the next call actually tops up again.
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with _sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE macro_history SET fetched_at=? WHERE series_id='VIXCLS'",
                (stale_ts,),
            )
            conn.commit()

        # Second top-up: FRED "revises" the same last date to a different value.
        macro_df_v2 = macro_df_v1.copy()
        revised_value = original_value + 5.0
        macro_df_v2.loc[last_date, "VIXCLS"] = revised_value
        de_v2 = _make_mock_data_engine(macro_df_v2)
        series_v2 = store.get_macro("VIXCLS", data_engine=de_v2)

        # The stored/returned value for that date must be the LATEST write,
        # not the first one.
        assert series_v2.loc[last_date] == pytest.approx(revised_value)
        assert series_v2.loc[last_date] != pytest.approx(original_value)

        # Row count for that date must still be exactly 1 (upsert, not a
        # duplicate insert alongside the stale row).
        with _sqlite3.connect(db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM macro_history WHERE series_id='VIXCLS' AND date=?",
                (last_date.strftime("%Y-%m-%d"),),
            ).fetchone()[0]
        assert count == 1

    def test_macro_fresh_cache_skips_data_engine(self, tmp_path):
        """Fresh rows (fetched_at < MACRO_REFRESH_HOURS ago) skip the top-up."""
        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)

        macro_df = _make_macro_df(30)
        de_first = _make_mock_data_engine(macro_df)

        # First call seeds the DB (fetched_at = now)
        store.get_macro("VIXCLS", data_engine=de_first)

        # Second call — rows are fresh
        de_second = _make_mock_data_engine(macro_df)
        store.get_macro("VIXCLS", data_engine=de_second)

        # DataEngine must NOT be called on the second call
        de_second.fetch_macro_history.assert_not_called()

    def test_macro_total_failure_empty_series(self, tmp_path):
        """DB error + DataEngine error → empty Series, no raise (CONSTRAINT #6)."""
        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)

        failing_de = MagicMock()
        failing_de.fetch_macro_history.side_effect = RuntimeError("FRED down")

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk")):
            result = store.get_macro("VIXCLS", data_engine=failing_de)

        assert isinstance(result, pd.Series)
        assert result.empty

    def test_macro_gap_rows_are_excluded_not_returned_as_nan(self, tmp_path):
        """FRED gap dates (NULL ``value`` rows in ``macro_history``) must be
        OMITTED from the returned Series, not included as NaN entries.

        ``macro_history`` stores a dense one-row-per-calendar-day skeleton
        per series; a sparsely-published series (e.g. a monthly one, or one
        with a genuine multi-year backfill gap) is mostly NULL rows. Passing
        those through as NaN breaks two real downstream consumers in
        ``scripts/refresh_validations.py``: a ``.rolling(window=N)`` over the
        raw series (needs N consecutive REAL observations) and
        ``_asof_align``'s ``merge_asof(direction="backward")`` (must forward-
        fill from the nearest REAL prior value, not match onto — and
        propagate — a NULL placeholder row).
        """
        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)
        macro_df = _make_macro_df(30)
        # Blank out all but the last 5 dates of T10Y2Y, mimicking a series
        # with a long real-data gap (e.g. BAMLH0A0HYM2 in production, whose
        # backfill only covers the most recent few years).
        real_dates = macro_df.index[-5:]
        macro_df.loc[macro_df.index[:-5], "T10Y2Y"] = float("nan")
        de = _make_mock_data_engine(macro_df)

        series = store.get_macro("T10Y2Y", data_engine=de)

        assert len(series) == 5
        assert not series.isna().any(), "gap rows must be omitted, never returned as NaN"
        for d in real_dates:
            assert pd.Timestamp(d) in series.index
        for d in macro_df.index[:-5]:
            assert pd.Timestamp(d) not in series.index

        # VIXCLS was never blanked out -- unaffected, still fully dense.
        vix_series = store.get_macro("VIXCLS", data_engine=de)
        assert len(vix_series) == 30
        assert not vix_series.isna().any()

    def test_macro_lookback_slices_tail(self, tmp_path):
        """lookback_days=10 returns at most ~10 business days of rows."""
        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)
        macro_df = _make_macro_df(200)
        de = _make_mock_data_engine(macro_df)
        store.get_macro("VIXCLS", data_engine=de)

        # Re-read with forced stale so it actually builds the series
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        import sqlite3 as _sqlite3
        with _sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE macro_history SET fetched_at=?", (stale_ts,)
            )
            conn.commit()

        de2 = _make_mock_data_engine(macro_df)
        series = store.get_macro("VIXCLS", lookback_days=10, data_engine=de2)

        # The tail should be ≤ 10 trading days (~14 calendar days)
        assert len(series) <= 14

    def test_macro_t10y2y_series_coexists(self, tmp_path):
        """fetch_macro_history returns both VIXCLS and T10Y2Y; both are stored
        and retrievable independently."""
        db = str(tmp_path / "macro.db")
        store = HistoricalStore(db_path=db)
        macro_df = _make_macro_df(50)
        de = _make_mock_data_engine(macro_df)

        vix = store.get_macro("VIXCLS", data_engine=de)
        t10y = store.get_macro("T10Y2Y", data_engine=de)

        assert not vix.empty
        assert not t10y.empty
        assert len(vix) == 50
        assert len(t10y) == 50

    def test_settings_fundamentals_refresh_days(self):
        """FUNDAMENTALS_REFRESH_DAYS's coded default == 1.

        Checked against the field's own coded default, not the live
        singleton -- an operator may legitimately have a different
        FUNDAMENTALS_REFRESH_DAYS in their own real .env, which is a valid
        deployment choice, not a violation of the class default this test
        is pinning down."""
        from settings import Settings
        assert Settings.model_fields["FUNDAMENTALS_REFRESH_DAYS"].default == 1

    def test_settings_macro_refresh_hours(self):
        """MACRO_REFRESH_HOURS's coded default == 12 (see docstring above)."""
        from settings import Settings
        assert Settings.model_fields["MACRO_REFRESH_HOURS"].default == 12


class TestNewsSentimentHistory:
    """Tests for save_news_sentiment / get_news_sentiment_history — the
    news_history read/write round trip, against a real temp SQLite DB
    (mirrors TestMacroHistory's convention for its sibling table)."""

    def test_round_trip_preserves_real_values(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment(
            {"AAPL": 0.42, "MSFT": -0.15}, datetime(2026, 7, 1, tzinfo=timezone.utc)
        )

        series = store.get_news_sentiment_history("AAPL")
        assert isinstance(series, pd.Series)
        assert len(series) == 1
        assert series.iloc[0] == pytest.approx(0.42)
        assert series.index.tz is None, "Index must be tz-naive"

    def test_nan_score_persists_as_null_and_reads_back_as_nan(self, tmp_path):
        """The exact honesty contract this table exists for: a NaN score
        (news_catalyst.py's fetch-failure/no-headlines sentinel) is stored
        as SQL NULL and reconstituted as NaN, never silently becoming 0.0
        at either the write or the read boundary."""
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment(
            {"AAPL": float("nan")}, datetime(2026, 7, 1, tzinfo=timezone.utc)
        )

        with sqlite3.connect(db) as conn:
            raw = conn.execute(
                "SELECT score FROM news_history WHERE symbol='AAPL'"
            ).fetchone()[0]
        assert raw is None  # genuine SQL NULL, not a stored 0.0

        series = store.get_news_sentiment_history("AAPL")
        assert len(series) == 1
        assert math.isnan(series.iloc[0])

    def test_multi_day_history_sorted_ascending(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment({"AAPL": 0.1}, datetime(2026, 7, 3, tzinfo=timezone.utc))
        store.save_news_sentiment({"AAPL": 0.2}, datetime(2026, 7, 1, tzinfo=timezone.utc))
        store.save_news_sentiment({"AAPL": 0.3}, datetime(2026, 7, 2, tzinfo=timezone.utc))

        series = store.get_news_sentiment_history("AAPL")
        assert len(series) == 3
        assert list(series.index) == sorted(series.index)
        assert series.iloc[0] == pytest.approx(0.2)  # July 1st, earliest

    def test_lookback_days_filters_tail(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        old_date = datetime.now(timezone.utc) - timedelta(days=90)
        recent_date = datetime.now(timezone.utc) - timedelta(days=2)
        store.save_news_sentiment({"AAPL": 0.5}, old_date)
        store.save_news_sentiment({"AAPL": 0.6}, recent_date)

        series = store.get_news_sentiment_history("AAPL", lookback_days=30)
        assert len(series) == 1
        assert series.iloc[0] == pytest.approx(0.6)

    def test_symbol_scoping_does_not_leak(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment(
            {"AAPL": 0.4, "MSFT": -0.4}, datetime(2026, 7, 1, tzinfo=timezone.utc)
        )
        series = store.get_news_sentiment_history("AAPL")
        assert len(series) == 1
        assert series.iloc[0] == pytest.approx(0.4)

    def test_symbol_is_case_insensitive(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        store.save_news_sentiment({"AAPL": 0.4}, datetime(2026, 7, 1, tzinfo=timezone.utc))
        series = store.get_news_sentiment_history("aapl")
        assert len(series) == 1

    def test_no_history_returns_empty_series_not_none(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        series = store.get_news_sentiment_history("ZZZZ")
        assert isinstance(series, pd.Series)
        assert series.empty

    def test_blank_symbol_returns_empty_series(self, tmp_path):
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)
        series = store.get_news_sentiment_history("   ")
        assert series.empty

    def test_db_error_returns_empty_series_never_raises(self, tmp_path):
        """self.Session is bound to self.engine at construction time, so
        mutating self._db_path afterward wouldn't actually redirect it (and
        would trivially pass either way, since a fresh store has no rows
        yet regardless). Patch self.Session itself to force session_scope's
        session_factory() call to raise, exercising the real dead-letter
        path (CONSTRAINT #6)."""
        db = str(tmp_path / "news.db")
        store = HistoricalStore(db_path=db)

        def _boom():
            raise RuntimeError("db unavailable")

        store.Session = _boom
        series = store.get_news_sentiment_history("AAPL")  # must not raise
        assert series.empty


# ─────────────────────────────────────────────────────────────────────────────
# Phase D1 — TestPITFundamentals
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone
import math

class TestPITFundamentals:
    def test_upsert_fundamentals_pit_idempotency(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))
        
        typed = {"pe_ratio": 15.0, "pb_ratio": 2.5, "roe": 0.15, "eps": 3.0}
        raw = {"mock": "data"}
        
        # Insert once
        store.upsert_fundamentals_pit("AAPL", typed, raw, report_date="2020-01-15", source="edgar")
        
        # Insert again with same report_date
        store.upsert_fundamentals_pit("AAPL", typed, raw, report_date="2020-01-15", source="edgar")
        
        hist = store.get_fundamentals_history("AAPL")
        assert len(hist) == 1
        assert hist.iloc[0]["report_date"] == "2020-01-15"
        assert hist.iloc[0]["pe_ratio"] == 15.0

    def test_get_fundamentals_asof_latest_leq_cutoff(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))
        
        # Insert two filings
        store.upsert_fundamentals_pit("AAPL", {"pe_ratio": 10.0, "roe": 0.1}, {}, report_date="2019-10-30", source="edgar")
        store.upsert_fundamentals_pit("AAPL", {"pe_ratio": 15.0, "roe": 0.2}, {}, report_date="2020-01-30", source="edgar")
        
        # Query before first filing
        out_early = store.get_fundamentals_asof("AAPL", datetime(2019, 10, 29, tzinfo=timezone.utc))
        assert math.isnan(out_early["pe_ratio"])
        
        # Query exactly on first filing
        out_first = store.get_fundamentals_asof("AAPL", datetime(2019, 10, 30, tzinfo=timezone.utc))
        assert out_first["pe_ratio"] == 10.0
        assert out_first["earnings_yield"] == 0.1
        
        # Query between filings
        out_mid = store.get_fundamentals_asof("AAPL", datetime(2019, 12, 31, tzinfo=timezone.utc))
        assert out_mid["pe_ratio"] == 10.0
        
        # Query after second filing
        out_latest = store.get_fundamentals_asof("AAPL", datetime(2020, 2, 1, tzinfo=timezone.utc))
        assert out_latest["pe_ratio"] == 15.0

    def test_get_fundamentals_raw_json_asof_latest_leq_cutoff(self, tmp_path):
        """Regression (secondary audit, 2026-08-24 -- see
        docs/known_issues/sector_selection_similarity_lookahead.md): the raw
        JSON blob (holding longBusinessSummary, consumed by
        data.sector_embeddings.resolve_target_description) must respect the
        SAME report_date <= as_of_date point-in-time cutoff as
        get_fundamentals_asof's typed-numeric-field sibling above -- a
        symbol scored as of a past date must never see a LATER filing's
        description."""
        import json
        store = HistoricalStore(db_path=str(tmp_path / "pit_raw_json.db"))

        store.upsert_fundamentals_pit(
            "AAPL", {"pe_ratio": 10.0}, {"longBusinessSummary": "2019-era description."},
            report_date="2019-10-30", source="edgar",
        )
        store.upsert_fundamentals_pit(
            "AAPL", {"pe_ratio": 15.0}, {"longBusinessSummary": "2020-era description."},
            report_date="2020-01-30", source="edgar",
        )

        # Before the first filing -> no PIT-eligible row at all.
        assert store.get_fundamentals_raw_json_asof("AAPL", datetime(2019, 10, 29, tzinfo=timezone.utc)) is None

        # Exactly on / between the first filing and the second -> the FIRST
        # (older) filing's description, never the later one.
        raw_first = store.get_fundamentals_raw_json_asof("AAPL", datetime(2019, 10, 30, tzinfo=timezone.utc))
        assert json.loads(raw_first)["longBusinessSummary"] == "2019-era description."
        raw_mid = store.get_fundamentals_raw_json_asof("AAPL", datetime(2019, 12, 31, tzinfo=timezone.utc))
        assert json.loads(raw_mid)["longBusinessSummary"] == "2019-era description."

        # After the second filing -> the newer description.
        raw_latest = store.get_fundamentals_raw_json_asof("AAPL", datetime(2020, 2, 1, tzinfo=timezone.utc))
        assert json.loads(raw_latest)["longBusinessSummary"] == "2020-era description."

    def test_get_fundamentals_raw_json_asof_unknown_symbol_returns_none(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit_raw_json.db"))
        assert store.get_fundamentals_raw_json_asof("NOSUCH", datetime(2020, 1, 1, tzinfo=timezone.utc)) is None

    def test_get_fundamentals_raw_json_asof_excludes_row_with_no_report_date(self, tmp_path):
        """Realistic degrade case for the actual production write path
        (secondary audit, 2026-08-24 -- verified against
        _upsert_fundamentals/_extract_report_date_str's real behavior, not
        just asserted): a provider payload that carries longBusinessSummary
        but none of REPORT_DATE_KEYS (mostRecentQuarter/lastFiscalYearEnd/
        report_date/earningsTimestamp) writes report_date=NULL via the
        REGULAR (non-PIT) get_fundamentals_raw() caching path -- confirmed
        this is the realistic shape by reading
        data/yahoo_fundamentals.py::compute_fundamentals's return keys (no
        longBusinessSummary at all) vs. YFinanceProvider's raw .info
        passthrough (has both longBusinessSummary AND a REPORT_DATE_KEYS
        field from the same dict). A row like this must be excluded from
        the point-in-time lookup (report_date IS NOT NULL), not silently
        treated as eligible -- get_fundamentals_raw_json_asof must return
        None rather than leaking an unverifiable-timing description into a
        point-in-time-scored caller.
        """
        store = HistoricalStore(db_path=str(tmp_path / "pit_raw_json.db"))

        class _FakeProvider:
            source_name = "test_provider"

            def get_fundamentals(self, symbol):
                return {
                    "longBusinessSummary": "Current-era description with no report date.",
                    "sector": "Technology",
                }

        raw = store.get_fundamentals_raw("AAPL", provider=_FakeProvider())
        assert raw.get("longBusinessSummary")  # sanity: the row really was written

        # The row exists (get_fundamentals_history sees it) but has no
        # report_date -- the point-in-time lookup must not use it.
        hist = store.get_fundamentals_history("AAPL")
        assert len(hist) == 1
        assert hist.iloc[0]["report_date"] is None

        result = store.get_fundamentals_raw_json_asof("AAPL", datetime.now(timezone.utc))
        assert result is None

    def test_get_fundamentals_history_additive(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))
        store.upsert_fundamentals_pit("AAPL", {"pe_ratio": 12.0}, {"raw": 1}, report_date="2021-05-01", source="edgar")
        
        hist = store.get_fundamentals_history("AAPL")
        # Ensure it has both the new columns and the old ones
        assert "report_date" in hist.columns
        assert "raw_json" in hist.columns
        assert "eps" in hist.columns
        assert len(hist) == 1
        assert hist.iloc[0]["report_date"] == "2021-05-01"


class TestGetPitReportDates:
    """get_pit_report_dates powers the EDGAR backfill's incremental skip — it MUST
    be a source-scoped SET (not a MAX), honour the `since` filter, and degrade to
    set() on error (never raise)."""

    def test_returns_only_edgar_source_dates(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))
        store.upsert_fundamentals_pit("AAPL", {"pe_ratio": 1.0}, {}, report_date="2020-01-15", source="edgar")
        store.upsert_fundamentals_pit("AAPL", {"pe_ratio": 2.0}, {}, report_date="2021-01-15", source="edgar")
        # A daily yahoo_computed row at another date must NOT leak into the edgar set.
        store.upsert_fundamentals_pit("AAPL", {"pe_ratio": 3.0}, {}, report_date="2099-01-01", source="yahoo_computed")

        got = store.get_pit_report_dates("AAPL", source="edgar")
        assert got == {"2020-01-15", "2021-01-15"}
        assert store.get_pit_report_dates("AAPL", source="yahoo_computed") == {"2099-01-01"}

    def test_since_filter_slices_the_set(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))
        for d in ("2018-06-01", "2020-06-01", "2022-06-01"):
            store.upsert_fundamentals_pit("MSFT", {"pe_ratio": 1.0}, {}, report_date=d, source="edgar")

        assert store.get_pit_report_dates("MSFT", source="edgar", since="2020-01-01") == {
            "2020-06-01", "2022-06-01",
        }
        # No `since` returns the full set.
        assert store.get_pit_report_dates("MSFT", source="edgar") == {
            "2018-06-01", "2020-06-01", "2022-06-01",
        }

    def test_empty_and_unknown_symbol_return_empty_set(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))
        assert store.get_pit_report_dates("NOSUCH", source="edgar") == set()

    def test_error_degrades_to_empty_set(self, tmp_path, monkeypatch):
        """A DB failure yields set() (process everything), never a raise —
        a broken skip must cost time, not rows (CONSTRAINT #6)."""
        store = HistoricalStore(db_path=str(tmp_path / "pit.db"))

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(store, "_get_conn", _boom)
        assert store.get_pit_report_dates("AAPL", source="edgar") == set()


# ─────────────────────────────────────────────────────────────────────────────
# readonly=True — DATABASE-LEVEL read-only store
# ─────────────────────────────────────────────────────────────────────────────

class TestReadonlyMode:
    def test_reads_data_written_by_a_write_mode_store(self, tmp_path):
        db = str(tmp_path / "t.db")
        writer = HistoricalStore(db_path=db)
        original = _make_account_snapshot(age_hours=0.5, n_positions=2)
        writer.save_account_snapshot(original)

        reader = HistoricalStore(db_path=db, readonly=True)
        loaded = reader.latest_account_snapshot()
        assert loaded is not None
        assert loaded.total_equity == pytest.approx(original.total_equity)

    def test_write_is_blocked_and_leaves_no_trace(self, tmp_path):
        db = str(tmp_path / "t.db")
        HistoricalStore(db_path=db)  # write-mode: creates the schema first
        reader = HistoricalStore(db_path=db, readonly=True)
        snap_id = reader.save_account_snapshot(_make_account_snapshot(age_hours=0.1, n_positions=1))
        assert snap_id == -1  # documented failure sentinel, never fabricated success
        writer = HistoricalStore(db_path=db)
        assert writer.latest_account_snapshot() is None

    def test_degrades_to_empty_on_missing_tables(self, tmp_path):
        """No write-mode store has ever run -> the tables don't exist. A
        readonly instance must degrade gracefully (CONSTRAINT #6), not crash.

        get_bars() is deliberately NOT asserted here (see the dedicated
        get_bars tests below) -- it is a write-through cache, not a pure
        reader, so its degrade path is a live-provider fallback, not empty."""
        db = str(tmp_path / "never_written.db")
        open(db, "w").close()
        reader = HistoricalStore(db_path=db, readonly=True)
        assert reader.latest_account_snapshot() is None
        assert reader.account_snapshot_history().empty

    def test_construction_skips_ensure_tables(self, tmp_path, monkeypatch):
        """readonly=True must not attempt DDL at construction."""
        calls = []
        monkeypatch.setattr(
            HistoricalStore, "_ensure_tables", lambda self: calls.append("ensure")
        )
        HistoricalStore(db_path=str(tmp_path / "t.db"), readonly=True)
        assert calls == []

    def test_readonly_connection_reads_a_non_wal_db_without_raising(self, tmp_path):
        """Regression for the WAL trap (see db_config.create_readonly_db_engine):
        reading a NON-WAL db read-only must not raise a journal_mode write."""
        db = str(tmp_path / "plain.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE account_snapshots (snapshot_id INTEGER PRIMARY KEY "
            "AUTOINCREMENT, fetched_at TEXT NOT NULL, buying_power REAL, "
            "total_equity REAL, total_dividends REAL, source TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
        assert not (tmp_path / "plain.db-wal").exists()  # confirm non-WAL

        reader = HistoricalStore(db_path=db, readonly=True)
        # Would raise here if the readonly hook issued journal_mode=WAL.
        assert reader.latest_account_snapshot() is None


# ─────────────────────────────────────────────────────────────────────────────
# get_bars() + readonly=True — documented NOT-a-safe-hardening-target finding
# ─────────────────────────────────────────────────────────────────────────────
# get_bars() is a write-through cache (top up stale/missing ranges by fetching
# live and persisting the delta), not a pure reader. A readonly instance never
# crashes here, but the write-back is unconditionally blocked at the DB level,
# so EVERY call falls through to a live-only fetch -- silently defeating the
# cache's entire purpose. This is why evaluation_engine.py's
# recommendation_tracking_report deliberately stays on a write-mode
# HistoricalStore() despite only calling get_bars() (a read, in isolation) --
# see the comment at that call site. These tests pin the exact mechanism.
#
# _get_bars_db_path now short-circuits on self._readonly BEFORE attempting
# the doomed fetch_days-wide top-up + write, going straight to the same
# lookback_days-wide live fetch get_bars()'s outer except used to fall back
# to once the write raised OperationalError -- identical data returned, one
# fewer live-provider round-trip, and no more "attempt to write a readonly
# database" WARNING log. The caching semantics above are UNCHANGED by this:
# a readonly store with a stale cache still always live-fetches instead of
# genuinely caching; see tests below for what's actually pinned now.

class TestReadonlyGetBarsFallsBackToLive:
    def test_readonly_store_with_a_fully_fresh_cache_needs_no_write(self, tmp_path):
        """When the cache already ends today, get_bars() skips the provider
        entirely (see TestGetBars.test_up_to_date_skips_provider) -- no top-up
        write is needed, so THIS case works perfectly fine readonly."""
        db = str(tmp_path / "t.db")
        writer = HistoricalStore(db_path=db)
        today = pd.Timestamp.now().normalize()
        df_today = pd.concat([_make_ohlcv(9), _make_ohlcv(1, end=today).set_axis([today])])
        writer._upsert_bars("AAPL", df_today, source="yfinance")

        reader = HistoricalStore(db_path=db, readonly=True)
        live_provider = _make_provider(_make_ohlcv(5))
        result = reader.get_bars("AAPL", lookback_days=30, provider=live_provider)

        assert live_provider.get_intraday_bars.call_count == 0  # served from cache
        assert not result.empty

    def test_readonly_store_with_a_stale_cache_falls_back_to_live(self, tmp_path):
        """When the cache is STALE (ends days ago), a top-up write is genuinely
        needed. A readonly store cannot perform it, so get_bars() skips the
        write attempt entirely and live-fetches directly -- this is the case
        that makes get_bars() unsuitable for a readonly-hardened call site."""
        db = str(tmp_path / "t.db")
        writer = HistoricalStore(db_path=db)
        five_days_ago = pd.Timestamp.now().normalize() - pd.offsets.BDay(5)
        writer._upsert_bars("AAPL", _make_ohlcv(30, end=five_days_ago), source="yfinance")

        reader = HistoricalStore(db_path=db, readonly=True)
        live_provider = _make_provider(_make_ohlcv(5))
        result = reader.get_bars("AAPL", lookback_days=30, provider=live_provider)

        # The provider WAS reached (the cache alone could not satisfy the
        # request) and real data still came back despite the write being
        # blocked -- see test_readonly_stale_cache_skips_the_doomed_topup_
        # attempt below for the exact, now-pinned call count.
        assert live_provider.get_intraday_bars.call_count >= 1
        assert not result.empty

    def test_readonly_stale_cache_skips_the_doomed_topup_attempt(self, tmp_path, caplog):
        """Tightens the '>= 1' assertion above into an exact pin: readonly +
        stale cache now reaches the provider exactly ONCE (the wide
        lookback_days-width fetch), never twice (the old behavior wasted a
        fetch_days-width top-up fetch first, then failed the write and did a
        second, wider fetch via get_bars()'s outer except). Also asserts no
        "readonly database" WARNING is logged -- the whole point of the fix."""
        import logging

        db = str(tmp_path / "t.db")
        writer = HistoricalStore(db_path=db)
        five_days_ago = pd.Timestamp.now().normalize() - pd.offsets.BDay(5)
        writer._upsert_bars("AAPL", _make_ohlcv(30, end=five_days_ago), source="yfinance")

        reader = HistoricalStore(db_path=db, readonly=True)
        live_provider = _make_provider(_make_ohlcv(5))
        with caplog.at_level(logging.WARNING):
            result = reader.get_bars("AAPL", lookback_days=30, provider=live_provider)

        assert live_provider.get_intraday_bars.call_count == 1
        assert not result.empty
        assert not any("readonly database" in rec.message for rec in caplog.records)

    def test_readonly_get_bars_write_attempt_leaves_the_cache_unchanged(self, tmp_path):
        """The blocked top-up write (on a STALE cache -- see the test above for
        why a fresh one doesn't exercise this path) must not have partially
        landed."""
        db = str(tmp_path / "t.db")
        writer = HistoricalStore(db_path=db)
        five_days_ago = pd.Timestamp.now().normalize() - pd.offsets.BDay(5)
        writer._upsert_bars("MSFT", _make_ohlcv(30, end=five_days_ago), source="yfinance")
        before = writer.latest_bar_date("MSFT")

        reader = HistoricalStore(db_path=db, readonly=True)
        reader.get_bars("MSFT", lookback_days=30, provider=_make_provider(_make_ohlcv(3)))

        after = HistoricalStore(db_path=db).latest_bar_date("MSFT")
        assert after == before


# ─────────────────────────────────────────────────────────────────────────────
# FMP feed tables — analyst_history / earnings_events / insider_stats /
# sector_snapshots (wave-0 scaffolding for the Financial Modeling Prep series)
# ─────────────────────────────────────────────────────────────────────────────

_FMP_TABLES = ("analyst_history", "earnings_events", "insider_stats", "sector_snapshots")
_FMP_INDEXES = (
    "idx_analyst_history_symbol",
    "idx_earnings_events_symbol_date",
    "idx_insider_stats_symbol_period",
    "idx_sector_snapshots_date",
)


def _table_names(db: str) -> set:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}


def _index_names(db: str) -> set:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}


class TestFMPTableCreation:
    """All four tables are created by ``_ensure_tables()`` via plain additive
    ``CREATE TABLE IF NOT EXISTS``, which is this module's documented upgrade
    mechanism -- deliberately WITHOUT a ``CURRENT_SCHEMA_VERSION`` bump (see
    that constant's DDL comment: the stamp exists only for the drift additive
    DDL cannot self-detect)."""

    def test_fresh_db_creates_all_four_tables_and_indexes(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        HistoricalStore(db_path=db)

        tables = _table_names(db)
        indexes = _index_names(db)
        for name in _FMP_TABLES:
            assert name in tables, f"{name} not created on a fresh DB"
        for name in _FMP_INDEXES:
            assert name in indexes, f"{name} not created on a fresh DB"

    def test_creation_is_idempotent(self, tmp_path):
        """Constructing twice must not raise (CREATE TABLE IF NOT EXISTS)."""
        db = str(tmp_path / "twice.db")
        HistoricalStore(db_path=db)
        HistoricalStore(db_path=db)
        assert set(_FMP_TABLES) <= _table_names(db)

    def test_legacy_db_gains_the_tables_without_losing_data(self, tmp_path):
        """A DB written by a build predating these tables must gain them on the
        next construction, and its existing rows must survive untouched -- the
        whole point of additive-only DDL."""
        db = str(tmp_path / "legacy.db")
        store = HistoricalStore(db_path=db)
        store._upsert_bars("AAPL", _make_ohlcv(5), source="yfinance")

        # Simulate a legacy DB: drop the four new tables entirely.
        with sqlite3.connect(db) as conn:
            for name in _FMP_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {name}")
            conn.commit()
        assert not (set(_FMP_TABLES) & _table_names(db))

        reopened = HistoricalStore(db_path=db)
        assert set(_FMP_TABLES) <= _table_names(db)
        # Pre-existing data untouched.
        assert reopened.latest_bar_date("AAPL") is not None

    def test_schema_version_is_not_bumped_by_these_tables(self, tmp_path):
        from data.historical_store import CURRENT_SCHEMA_VERSION

        db = str(tmp_path / "ver.db")
        store = HistoricalStore(db_path=db)
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION


class TestAnalystHistory:
    def test_upsert_and_read_back(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_analyst_snapshot(
            "aapl", "2026-07-30",
            target_consensus=250.0, target_median=248.0,
            target_high=300.0, target_low=200.0,
            grade_score=4.1, source="fmp",
        ) == 1

        row = store.get_analyst_snapshot("AAPL")
        assert row["symbol"] == "AAPL"
        assert row["as_of"] == "2026-07-30"
        assert row["target_consensus"] == pytest.approx(250.0)
        assert row["grade_score"] == pytest.approx(4.1)
        assert row["source"] == "fmp"

    def test_unreported_figures_stay_null_never_zero(self, tmp_path):
        """CONSTRAINT #4: "no analyst coverage" and "a target of zero" are
        different facts."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_analyst_snapshot("AAPL", "2026-07-30", target_consensus=250.0)

        row = store.get_analyst_snapshot("AAPL")
        assert row["target_high"] is None
        assert row["target_low"] is None
        assert row["grade_score"] is None

    def test_nan_is_stored_as_null_not_zero(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_analyst_snapshot(
            "AAPL", "2026-07-30", target_consensus=float("nan"),
        )
        assert store.get_analyst_snapshot("AAPL")["target_consensus"] is None

    def test_as_of_cutoff_excludes_later_rows(self, tmp_path):
        """Storage-layer causality, same contract as get_etf_holdings."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_analyst_snapshot("AAPL", "2026-06-01", target_consensus=200.0)
        store.upsert_analyst_snapshot("AAPL", "2026-07-30", target_consensus=250.0)

        assert store.get_analyst_snapshot("AAPL")["target_consensus"] == pytest.approx(250.0)
        cut = store.get_analyst_snapshot("AAPL", as_of="2026-07-01")
        assert cut["as_of"] == "2026-06-01"
        assert cut["target_consensus"] == pytest.approx(200.0)

    def test_same_day_refetch_replaces_rather_than_duplicates(self, tmp_path):
        db = str(tmp_path / "t.db")
        store = HistoricalStore(db_path=db)
        store.upsert_analyst_snapshot("AAPL", "2026-07-30", target_consensus=250.0)
        store.upsert_analyst_snapshot("AAPL", "2026-07-30", target_consensus=255.0)

        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM analyst_history").fetchone()[0]
        assert count == 1
        assert store.get_analyst_snapshot("AAPL")["target_consensus"] == pytest.approx(255.0)

    def test_empty_sentinels_and_never_raises(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.get_analyst_snapshot("NOPE") == {}
        assert store.get_analyst_snapshot("") == {}
        assert store.upsert_analyst_snapshot("", "2026-07-30") == 0
        assert store.upsert_analyst_snapshot("AAPL", "") == 0
        assert store.latest_analyst_as_of("NOPE") is None

    def test_latest_as_of_for_cadence_gate(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_analyst_snapshot("AAPL", "2026-06-01")
        store.upsert_analyst_snapshot("AAPL", "2026-07-30")
        assert store.latest_analyst_as_of("aapl") == "2026-07-30"


class TestEarningsEvents:
    def _rows(self):
        return [
            # Reported quarter.
            {"symbol": "AAPL", "event_date": "2026-05-01", "eps_actual": 1.52,
             "eps_estimated": 1.40, "revenue_actual": 9.0e10,
             "revenue_estimated": 8.8e10, "last_updated": "2026-05-02",
             "source": "fmp"},
            # Scheduled but not yet reported -- NULL actuals is normal.
            {"symbol": "AAPL", "event_date": "2026-08-01", "eps_actual": None,
             "eps_estimated": 1.60, "last_updated": "2026-07-15", "source": "fmp"},
        ]

    def test_upsert_and_read_back(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_earnings_events(self._rows()) == 2

        rows = store.get_earnings_events("AAPL")
        assert len(rows) == 2
        assert rows[0]["event_date"] == "2026-08-01"  # newest first by default

    def test_future_row_keeps_null_actual_never_zero(self, tmp_path):
        """A future-dated row with a NULL actual is normal and correct. Reading
        it back as 0.0 would turn every unreported quarter into a 100% miss."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_earnings_events(self._rows())

        future = [
            r for r in store.get_earnings_events("AAPL")
            if r["event_date"] == "2026-08-01"
        ][0]
        assert future["eps_actual"] is None
        assert future["eps_estimated"] == pytest.approx(1.60)
        # revenue fields were not supplied at all -> NULL, not 0.0
        assert future["revenue_actual"] is None

    def test_actuals_only_plus_date_cutoff_excludes_a_wrongly_populated_future_row(self, tmp_path):
        """BOTH filters, never the date filter alone: a vendor bug that puts an
        actual on a FUTURE row must not slip through."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        rows = self._rows()
        rows.append({
            "symbol": "AAPL", "event_date": "2026-11-01",
            "eps_actual": 99.0,  # vendor bug: an actual on a future row
            "eps_estimated": 1.70, "source": "fmp",
        })
        store.upsert_earnings_events(rows)

        trailing = store.get_earnings_events(
            "AAPL", on_or_before="2026-07-31", actuals_only=True,
        )
        assert [r["event_date"] for r in trailing] == ["2026-05-01"]

    def test_after_cutoff_returns_the_next_scheduled_date_ascending(self, tmp_path):
        """Knowing a publicly-announced future DATE is not lookahead; knowing
        the RESULT is. ``after=`` returns ascending so [0] is the next event."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_earnings_events(self._rows())

        upcoming = store.get_earnings_events("AAPL", after="2026-07-31")
        assert [r["event_date"] for r in upcoming] == ["2026-08-01"]

    def test_last_updated_is_persisted_verbatim(self, tmp_path):
        """The only thing enabling a future PIT replay -- imperfect (a
        backfilled actual with a stale stamp defeats it), which is exactly why
        it must be stored rather than derived."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_earnings_events(self._rows())
        reported = store.get_earnings_events("AAPL", on_or_before="2026-07-31")[0]
        assert reported["last_updated"] == "2026-05-02"

    def test_rows_without_a_pk_anchor_are_skipped_not_defaulted(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        written = store.upsert_earnings_events([
            {"symbol": "", "event_date": "2026-05-01"},
            {"symbol": "AAPL", "event_date": ""},
            {"symbol": "AAPL", "event_date": "2026-05-01"},
        ])
        assert written == 1

    def test_refetch_upgrades_a_scheduled_row_in_place(self, tmp_path):
        db = str(tmp_path / "t.db")
        store = HistoricalStore(db_path=db)
        store.upsert_earnings_events([
            {"symbol": "AAPL", "event_date": "2026-08-01", "eps_estimated": 1.60},
        ])
        store.upsert_earnings_events([
            {"symbol": "AAPL", "event_date": "2026-08-01",
             "eps_estimated": 1.60, "eps_actual": 1.71, "last_updated": "2026-08-02"},
        ])
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM earnings_events").fetchone()[0] == 1
        assert store.get_earnings_events("AAPL")[0]["eps_actual"] == pytest.approx(1.71)

    def test_empty_sentinels_and_never_raises(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_earnings_events([]) == 0
        assert store.get_earnings_events("NOPE") == []
        assert store.get_earnings_events("") == []
        assert store.latest_earnings_fetched_at("NOPE") is None

    def test_latest_fetched_at_is_wall_clock_not_event_date(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_earnings_events(self._rows())
        stamp = store.latest_earnings_fetched_at("AAPL")
        assert stamp is not None
        # fetched_at is an ISO UTC wall-clock stamp, never one of the event dates.
        assert stamp not in {"2026-05-01", "2026-08-01"}


class TestInsiderStats:
    def _rows(self):
        return [
            {"symbol": "AAPL", "year": 2026, "quarter": 1,
             "acquired_transactions": 12, "disposed_transactions": 30,
             "acquired_disposed_ratio": 0.4, "total_acquired": 1000.0,
             "total_disposed": 2500.0, "total_purchases": 5, "total_sales": 11,
             "source": "fmp"},
            {"symbol": "AAPL", "year": 2026, "quarter": 2,
             "acquired_transactions": 4, "disposed_transactions": 9,
             "source": "fmp"},
        ]

    def test_upsert_and_read_back_newest_first(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_insider_stats(self._rows()) == 2

        rows = store.get_insider_stats("AAPL")
        assert [(r["year"], r["quarter"]) for r in rows] == [(2026, 2), (2026, 1)]
        assert rows[1]["acquired_disposed_ratio"] == pytest.approx(0.4)

    def test_unreported_aggregates_stay_null(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_insider_stats(self._rows())
        q2 = store.get_insider_stats("AAPL")[0]
        assert q2["acquired_disposed_ratio"] is None
        assert q2["total_acquired"] is None
        assert q2["total_purchases"] is None

    def test_restated_quarter_replaces_rather_than_duplicates(self, tmp_path):
        """A quarter's aggregate keeps changing as late Form 4s land -- the
        newest read of a quarter supersedes the older one."""
        db = str(tmp_path / "t.db")
        store = HistoricalStore(db_path=db)
        store.upsert_insider_stats([
            {"symbol": "AAPL", "year": 2026, "quarter": 1, "acquired_transactions": 12},
        ])
        store.upsert_insider_stats([
            {"symbol": "AAPL", "year": 2026, "quarter": 1, "acquired_transactions": 15},
        ])
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM insider_stats").fetchone()[0] == 1
        assert store.get_insider_stats("AAPL")[0]["acquired_transactions"] == 15

    def test_reader_does_not_apply_the_min_lag_filter_itself(self, tmp_path):
        """The minimum-lag filter is a consumer-side judgment call
        (settings.FMP_INSIDER_MIN_LAG_DAYS). A storage helper that silently
        dropped rows would make the archive un-auditable."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        now = datetime.now(timezone.utc)
        store.upsert_insider_stats([
            {"symbol": "AAPL", "year": now.year, "quarter": ((now.month - 1) // 3) + 1,
             "acquired_transactions": 3},
        ])
        # The current (still-accruing) quarter IS returned by the storage read.
        assert len(store.get_insider_stats("AAPL")) == 1

    def test_rows_without_a_pk_anchor_are_skipped(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        written = store.upsert_insider_stats([
            {"symbol": "AAPL", "year": None, "quarter": 1},
            {"symbol": "AAPL", "year": 2026, "quarter": None},
            {"symbol": "", "year": 2026, "quarter": 1},
            {"symbol": "AAPL", "year": 2026, "quarter": 1},
        ])
        assert written == 1

    def test_empty_sentinels_and_never_raises(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_insider_stats([]) == 0
        assert store.get_insider_stats("NOPE") == []
        assert store.get_insider_stats("") == []
        assert store.latest_insider_fetched_at("NOPE") is None


class TestSectorSnapshots:
    def test_upsert_and_read_back_keyed_by_sector(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_sector_snapshots([
            {"sector": "Technology", "date": "2026-07-30", "pe": 31.2,
             "change_pct": 0.0084, "source": "fmp"},
            {"sector": "Energy", "date": "2026-07-30", "pe": 12.4,
             "change_pct": -0.0031, "source": "fmp"},
        ]) == 2

        snap = store.get_sector_snapshots()
        assert set(snap) == {"Technology", "Energy"}
        assert snap["Technology"]["pe"] == pytest.approx(31.2)
        assert snap["Energy"]["change_pct"] == pytest.approx(-0.0031)

    def test_as_of_cutoff_is_genuinely_point_in_time(self, tmp_path):
        """The one new FMP feed with a real PIT story: both endpoints are
        date-parameterized, so a dated read must return THAT date's figures."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_sector_snapshots([
            {"sector": "Technology", "date": "2026-06-01", "pe": 28.0},
            {"sector": "Technology", "date": "2026-07-30", "pe": 31.2},
        ])
        assert store.get_sector_snapshots()["Technology"]["pe"] == pytest.approx(31.2)
        cut = store.get_sector_snapshots(as_of="2026-07-01")["Technology"]
        assert cut["date"] == "2026-06-01"
        assert cut["pe"] == pytest.approx(28.0)

    def test_each_sector_resolves_its_own_latest_qualifying_date(self, tmp_path):
        """A sector missing from the cutoff date's snapshot falls back to its
        own last known one rather than disappearing."""
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_sector_snapshots([
            {"sector": "Technology", "date": "2026-07-30", "pe": 31.2},
            {"sector": "Energy", "date": "2026-05-01", "pe": 11.0},
        ])
        snap = store.get_sector_snapshots(as_of="2026-07-30")
        assert snap["Technology"]["date"] == "2026-07-30"
        assert snap["Energy"]["date"] == "2026-05-01"

    def test_unreported_figures_stay_null(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_sector_snapshots([{"sector": "Utilities", "date": "2026-07-30"}])
        row = store.get_sector_snapshots()["Utilities"]
        assert row["pe"] is None
        assert row["change_pct"] is None

    def test_rows_without_a_pk_anchor_are_skipped(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        written = store.upsert_sector_snapshots([
            {"sector": "", "date": "2026-07-30"},
            {"sector": "Technology", "date": ""},
            {"sector": "Technology", "date": "2026-07-30"},
        ])
        assert written == 1

    def test_empty_sentinels_and_never_raises(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        assert store.upsert_sector_snapshots([]) == 0
        assert store.get_sector_snapshots() == {}
        assert store.latest_sector_snapshot_date() is None

    def test_latest_snapshot_date_for_the_per_cycle_cadence_gate(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "t.db"))
        store.upsert_sector_snapshots([
            {"sector": "Technology", "date": "2026-06-01"},
            {"sector": "Energy", "date": "2026-07-30"},
        ])
        assert store.latest_sector_snapshot_date() == "2026-07-30"


class TestSourceNamePrefersEmbeddedSource:
    """``_source_name`` gained an optional ``raw`` argument so a per-symbol
    ``_source`` key embedded by a fallback-capable provider wins over the
    provider OBJECT's chain-level label. Without it,
    ``fundamentals_history.source`` would claim a provenance that isn't true
    for a symbol the chain fell back on -- and that column is the ground-truth
    operator query for "did the chain fall back on me?"."""

    def test_embedded_source_wins(self):
        from data.historical_store import _source_name

        provider = MagicMock()
        provider.source_name = "composite"
        assert _source_name(provider, {"_source": "yahoo_computed"}) == "yahoo_computed"

    def test_unchanged_when_raw_is_none(self):
        from data.historical_store import _source_name

        provider = MagicMock()
        provider.source_name = "yahoo_computed"
        assert _source_name(provider) == "yahoo_computed"
        assert _source_name(provider, None) == "yahoo_computed"

    def test_unchanged_when_raw_lacks_the_key(self):
        from data.historical_store import _source_name

        provider = MagicMock()
        provider.source_name = "yahoo_computed"
        assert _source_name(provider, {"trailingPE": 21.0}) == "yahoo_computed"

    def test_empty_or_non_dict_raw_falls_back(self):
        """An empty-string / None ``_source``, or a non-dict ``raw``, must NOT
        produce an empty provenance label."""
        from data.historical_store import _source_name

        provider = MagicMock()
        provider.source_name = "yahoo_computed"
        assert _source_name(provider, {}) == "yahoo_computed"
        assert _source_name(provider, {"_source": ""}) == "yahoo_computed"
        assert _source_name(provider, {"_source": None}) == "yahoo_computed"
        assert _source_name(provider, ["not", "a", "dict"]) == "yahoo_computed"

    def test_falls_back_to_lowercased_class_name_without_source_name_attr(self):
        from data.historical_store import _source_name

        class FakeProvider:
            pass

        assert _source_name(FakeProvider()) == "fakeprovider"
