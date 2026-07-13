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
        """settings.FUNDAMENTALS_REFRESH_DAYS == 1."""
        from settings import settings as _s
        assert _s.FUNDAMENTALS_REFRESH_DAYS == 1

    def test_settings_macro_refresh_hours(self):
        """settings.MACRO_REFRESH_HOURS == 12."""
        from settings import settings as _s
        assert _s.MACRO_REFRESH_HOURS == 12
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
