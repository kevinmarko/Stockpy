"""
tests/test_calibration.py — Conviction calibration tracker (Tier 1 / 1.2).

Tests for:
  - evaluation_engine.calibration_curve()
  - transactions_store.TransactionsStore.record_trade() conviction kwarg
  - Schema migration (_ensure_conviction_column)

All tests use an in-memory SQLite DB so the production quant_platform.db
is never touched (CONSTRAINT #4 / test isolation).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from transactions_store import TransactionsStore
from evaluation_engine import calibration_curve, _empty_calibration_df, _CALIBRATION_COLUMNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store() -> TransactionsStore:
    return TransactionsStore(db_url="sqlite:///:memory:")


def add_closed_trade(
    store: TransactionsStore,
    *,
    symbol: str = "AAPL",
    side: str = "long",
    entry_price: float = 100.0,
    exit_price: float = 110.0,
    conviction: float | None = None,
    days_ago: int = 1,
) -> int:
    now = datetime.utcnow()
    entry_ts = now - timedelta(days=days_ago + 1)
    exit_ts = now - timedelta(days=days_ago)
    tid = store.record_trade(
        symbol=symbol,
        side=side,
        entry_ts=entry_ts,
        entry_price=entry_price,
        shares=1.0,
        conviction=conviction,
    )
    store.close_trade(tid, exit_ts=exit_ts, exit_price=exit_price)
    return tid


# ===========================================================================
# TestSchema
# ===========================================================================

class TestSchema:
    """calibration_curve return schema is always correct."""

    def test_empty_store_returns_correct_columns(self):
        store = make_store()
        df = calibration_curve(store)
        assert list(df.columns) == _CALIBRATION_COLUMNS

    def test_empty_store_returns_zero_rows(self):
        store = make_store()
        df = calibration_curve(store)
        assert len(df) == 0

    def test_no_conviction_column_fallback(self, monkeypatch):
        """Store without conviction column → empty DataFrame (dead-letter)."""
        store = make_store()
        # Patch closed_trades_df to return a frame without conviction
        df_no_conv = pd.DataFrame({"exit_price": [110.0], "entry_price": [100.0], "side": ["long"]})
        monkeypatch.setattr(store, "closed_trades_df", lambda: df_no_conv)
        result = calibration_curve(store)
        assert result.empty
        assert list(result.columns) == _CALIBRATION_COLUMNS

    def test_all_null_conviction_returns_empty(self):
        store = make_store()
        # Add trade with no conviction
        add_closed_trade(store, conviction=None)
        result = calibration_curve(store)
        assert result.empty

    def test_store_read_failure_returns_empty(self, monkeypatch):
        """Dead-letter: read failure → empty DataFrame, no exception raised."""
        store = make_store()
        monkeypatch.setattr(store, "closed_trades_df", lambda: (_ for _ in ()).throw(RuntimeError("DB down")))
        result = calibration_curve(store)
        assert result.empty
        assert list(result.columns) == _CALIBRATION_COLUMNS

    def test_count_column_is_int(self):
        store = make_store()
        for i in range(5):
            add_closed_trade(store, conviction=0.75, exit_price=110.0)
        result = calibration_curve(store)
        assert result["count"].dtype == int or np.issubdtype(result["count"].dtype, np.integer)


# ===========================================================================
# TestWinRateLogic
# ===========================================================================

class TestWinRateLogic:
    """Win/loss classification is correct and side-aware."""

    def test_long_win(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, side="long", entry_price=100.0, exit_price=110.0, conviction=0.75)
        df = calibration_curve(store, n_bins=1)
        assert len(df) == 1
        assert df.iloc[0]["win_rate"] == pytest.approx(1.0)

    def test_long_loss(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, side="long", entry_price=100.0, exit_price=90.0, conviction=0.75)
        df = calibration_curve(store, n_bins=1)
        assert df.iloc[0]["win_rate"] == pytest.approx(0.0)

    def test_short_win(self):
        # Short: exit < entry = win
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, side="short", entry_price=100.0, exit_price=90.0, conviction=0.65)
        df = calibration_curve(store, n_bins=1)
        assert df.iloc[0]["win_rate"] == pytest.approx(1.0)

    def test_short_loss(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, side="short", entry_price=100.0, exit_price=110.0, conviction=0.65)
        df = calibration_curve(store, n_bins=1)
        assert df.iloc[0]["win_rate"] == pytest.approx(0.0)

    def test_mixed_win_rate(self):
        store = make_store()
        for _ in range(6):
            add_closed_trade(store, conviction=0.55, exit_price=110.0)  # win
        for _ in range(4):
            add_closed_trade(store, conviction=0.55, exit_price=90.0)   # loss
        df = calibration_curve(store, n_bins=1)
        assert df.iloc[0]["win_rate"] == pytest.approx(0.6)

    def test_exact_entry_eq_exit_is_not_a_win(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, conviction=0.80, entry_price=100.0, exit_price=100.0)
        df = calibration_curve(store, n_bins=1)
        assert df.iloc[0]["win_rate"] == pytest.approx(0.0)


# ===========================================================================
# TestBinning
# ===========================================================================

class TestBinning:
    """Binning and n_bins parameter."""

    def test_n_bins_parameter(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, conviction=0.5, exit_price=110.0)
        df5 = calibration_curve(store, n_bins=5)
        df20 = calibration_curve(store, n_bins=20)
        assert len(df5) == 5
        assert len(df20) == 20

    def test_bin_bounds_span_zero_to_one(self):
        store = make_store()
        add_closed_trade(store, conviction=0.1, exit_price=110.0)
        df = calibration_curve(store, n_bins=10)
        # pd.cut(include_lowest=True) shifts the leftmost edge down by 0.1% of
        # the bin width so that 0.0 is included — bin_low may be ≈ -0.001.
        assert df["bin_low"].min() == pytest.approx(0.0, abs=2e-3)
        assert df["bin_high"].max() == pytest.approx(1.0, abs=2e-3)

    def test_bin_center_is_midpoint(self):
        store = make_store()
        df = calibration_curve(make_store(), n_bins=4)
        for _, row in df.iterrows():
            assert row["bin_center"] == pytest.approx((row["bin_low"] + row["bin_high"]) / 2, abs=1e-9)

    def test_perfect_calibration_equals_bin_center(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, conviction=0.5, exit_price=110.0)
        df = calibration_curve(store)
        for _, row in df.iterrows():
            assert row["perfect_calibration"] == pytest.approx(row["bin_center"], abs=1e-9)

    def test_trades_land_in_correct_bins(self):
        store = make_store()
        # 10 low-conviction trades (≈ bin 0.1), 10 high-conviction (≈ bin 0.9)
        for _ in range(10):
            add_closed_trade(store, conviction=0.15, exit_price=110.0)
        for _ in range(10):
            add_closed_trade(store, conviction=0.85, exit_price=90.0)
        df = calibration_curve(store, n_bins=10)
        low_bins = df[df["bin_center"] < 0.3]
        high_bins = df[df["bin_center"] > 0.7]
        assert low_bins["count"].sum() == 10
        assert high_bins["count"].sum() == 10


# ===========================================================================
# TestMinTradesGate
# ===========================================================================

class TestMinTradesGate:
    """Bins below min_trades_per_bin get win_rate=NaN."""

    def test_below_threshold_is_nan(self):
        store = make_store()
        for _ in range(3):  # below default min=5
            add_closed_trade(store, conviction=0.55, exit_price=110.0)
        df = calibration_curve(store, n_bins=1, min_trades_per_bin=5)
        assert math.isnan(df.iloc[0]["win_rate"])

    def test_at_threshold_is_not_nan(self):
        store = make_store()
        for _ in range(5):
            add_closed_trade(store, conviction=0.55, exit_price=110.0)
        df = calibration_curve(store, n_bins=1, min_trades_per_bin=5)
        assert not math.isnan(df.iloc[0]["win_rate"])

    def test_empty_bin_conviction_mean_is_nan(self):
        store = make_store()
        for _ in range(10):
            add_closed_trade(store, conviction=0.15, exit_price=110.0)
        df = calibration_curve(store, n_bins=10)
        empty_bins = df[df["count"] == 0]
        assert empty_bins["conviction_mean"].isna().all()


# ===========================================================================
# TestRecordTradeConviction
# ===========================================================================

class TestRecordTradeConviction:
    """TransactionsStore.record_trade accepts and persists conviction."""

    def test_conviction_kwarg_accepted(self):
        store = make_store()
        tid = store.record_trade(
            symbol="MSFT", side="long",
            entry_ts=datetime.utcnow(), entry_price=300.0, shares=2.0,
            conviction=0.82,
        )
        assert tid > 0

    def test_conviction_persisted_and_readable(self):
        store = make_store()
        add_closed_trade(store, conviction=0.75, exit_price=110.0)
        df = store.closed_trades_df()
        assert "conviction" in df.columns
        assert df["conviction"].iloc[0] == pytest.approx(0.75)

    def test_none_conviction_is_null_in_db(self):
        store = make_store()
        add_closed_trade(store, conviction=None, exit_price=110.0)
        df = store.closed_trades_df()
        assert pd.isna(df["conviction"].iloc[0])

    def test_conviction_column_present_in_open_trades(self):
        store = make_store()
        store.record_trade(
            symbol="NVDA", side="long",
            entry_ts=datetime.utcnow(), entry_price=500.0, shares=1.0,
            conviction=0.90,
        )
        df = store.open_trades_df()
        assert "conviction" in df.columns
