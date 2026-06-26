"""
tests/test_historical_store.py — Tier 2.3 Phase 1

All tests are fully offline: no network calls, no real quant_platform.db.
Every test uses a fresh temporary SQLite database via pytest's tmp_path fixture.

Data convention: _make_ohlcv(...) generates rows ending at TODAY by default so
that _read_from_db's date-cutoff filter (today - lookback_days) always includes
the test data.  Tests that exercise the incremental-delta logic seed the DB with
rows ending N business days ago and provide a delta frame ending today.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.historical_store import HistoricalStore, _DF_COLUMNS


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
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
# TestTableCreation
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

    def test_init_idempotent(self, tmp_path):
        """Calling __init__ twice must not raise or corrupt the DB."""
        db = str(tmp_path / "test.db")
        HistoricalStore(db_path=db)
        HistoricalStore(db_path=db)  # second init is a no-op


# ─────────────────────────────────────────────────────────────────────────────
# TestLatestBarDate
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
        # Should equal the last date in the synthetic data
        expected_last = df.index[-1].normalize()
        assert latest.normalize() == expected_last


# ─────────────────────────────────────────────────────────────────────────────
# TestGetBars — full suite
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
        # DB has the rows.
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
        # tz-naive index
        assert result.index.tz is None, "Index must be tz-naive"
        # Exact column order matches DataEngine.fetch_technical_raw()
        assert list(result.columns) == _DF_COLUMNS, f"Columns: {list(result.columns)}"
        # Sorted ascending
        assert result.index.is_monotonic_increasing

    def test_no_fabrication_on_total_failure(self, tmp_path):
        """Empty DB + provider raises → empty DataFrame, never fabricated rows."""
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        provider = _make_raising_provider()
        result = store.get_bars("AAPL", lookback_days=504, provider=provider)
        assert result.empty
        # Correct schema even when empty.
        assert list(result.columns) == _DF_COLUMNS

    def test_dead_letter_db_error(self, tmp_path):
        """sqlite3.connect raises → falls back to live provider, never raises."""
        df = _make_ohlcv(10)
        provider = _make_provider(df)
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk error")):
            result = store.get_bars("AAPL", lookback_days=20, provider=provider)

        # Live fallback must still return data.
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

        # Seed with rows ending today.
        df_today = _make_ohlcv(10)  # ends today
        store._upsert_bars("AAPL", df_today, source="yfinance")

        provider = _make_provider(_make_ohlcv(10))
        store.get_bars("AAPL", lookback_days=30, provider=provider)

        # Provider must NOT be called — we're already up to date.
        assert provider.get_intraday_bars.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# TestColumnContract
# ─────────────────────────────────────────────────────────────────────────────

class TestColumnContract:
    def test_adj_close_stored_but_not_in_output(self, tmp_path):
        """adj_close is written to the DB but NOT exposed in the public DataFrame."""
        db = str(tmp_path / "test.db")
        store = HistoricalStore(db_path=db)
        df = _make_ohlcv(5)
        df["Adj Close"] = df["Close"] * 0.99
        provider = _make_provider(df)
        result = store.get_bars("AAPL", lookback_days=10, provider=provider)
        assert "Adj Close" not in result.columns
        assert "adj_close" not in result.columns
        assert list(result.columns) == _DF_COLUMNS

    def test_volume_present_and_non_null(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "test.db"))
        df = _make_ohlcv(5)
        provider = _make_provider(df)
        result = store.get_bars("AAPL", lookback_days=10, provider=provider)
        assert "Volume" in result.columns
        assert result["Volume"].notna().all()
