"""
tests/test_training_data_paper_features.py
=========================================
Tests for the new paper execution features in ml/training_data.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.training_data import _pit_ticker_row
from ml.triple_barrier import apply_triple_barrier


def test_pit_ticker_row_with_paper_orders():
    # Setup dummy price history to avoid NaN for technical features
    dates = pd.date_range("2023-01-01", periods=100, freq="B")
    close = pd.Series(np.linspace(100, 110, 100), index=dates)
    # tz-aware as_of_date to test timezone coercion!
    as_of_date = pd.Timestamp("2023-05-01", tz="America/New_York")
    symbol = "AAPL"
    
    # Paper orders history (some outside 30d window, some exactly on/after as_of_date)
    paper_orders = pd.DataFrame([
        # 1. 40 days ago (should be excluded - outside 30d window)
        {"client_order_id": "1", "symbol": "AAPL", "side": "buy", "qty": 10, "target_qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-03-20")},
        # 2. 15 days ago (should be included)
        {"client_order_id": "2", "symbol": "AAPL", "side": "buy", "qty": 20, "target_qty": 25, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-16")},
        # 3. 5 days ago (should be included)
        {"client_order_id": "3", "symbol": "AAPL", "side": "sell", "qty": 10, "target_qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-26")},
        # 4. Exactly on as_of_date (should be strictly excluded - lookahead!)
        {"client_order_id": "4", "symbol": "AAPL", "side": "buy", "qty": 100, "target_qty": 100, "filled_qty": 100, "timestamp": pd.Timestamp("2023-05-01")},
        # 5. Different symbol (should be excluded)
        {"client_order_id": "5", "symbol": "MSFT", "side": "buy", "qty": 10, "target_qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-20")},
    ])
    
    row = _pit_ticker_row(close, symbol, as_of_date, paper_orders)
    
    # We expect only orders 2 and 3 to be included in the 30d window.
    # History flag should be 1.0 (metadata for monitoring)
    assert row["paper_has_history_30d"] == 1.0
    
    # Total qty = 20 + 10 = 30
    # Filled qty = 10 + 10 = 20
    # Fill rate = 20 / 30 = 0.666...
    assert pytest.approx(row["paper_fill_rate_30d"], 0.001) == 20 / 30

    # Conviction features
    assert row["paper_order_count_30d"] == 2.0
    assert pytest.approx(row["paper_size_variance_30d"], 0.001) == 50.0  # var of [20, 10] = 50.0
    # Qty sum = 30. Target qty sum = 25 + 10 = 35. Ratio = 30 / 35.
    assert pytest.approx(row["paper_size_vs_kelly_ratio_30d"], 0.001) == 30.0 / 35.0
    
    # Outcome features - mock doesn't trigger triple_barrier without price movement
    # So we don't assert the exact hit rate here yet, just check they are present or NaN
    assert "paper_hit_rate_30d" in row
    assert "paper_avg_realized_pnl_30d" in row


def test_pit_ticker_row_empty_paper_orders():
    dates = pd.date_range("2023-01-01", periods=100, freq="B")
    close = pd.Series(np.linspace(100, 110, 100), index=dates)
    as_of_date = pd.Timestamp("2023-05-01")
    symbol = "AAPL"
    
    # Empty DataFrame with same columns
    paper_orders = pd.DataFrame(columns=["client_order_id", "symbol", "side", "qty", "filled_qty", "timestamp"])
    
    row = _pit_ticker_row(close, symbol, as_of_date, paper_orders)
    
    assert row["paper_has_history_30d"] == 0.0
    assert np.isnan(row["paper_fill_rate_30d"])
    assert np.isnan(row["paper_order_count_30d"])
    assert np.isnan(row["paper_size_variance_30d"])
    assert np.isnan(row["paper_size_vs_kelly_ratio_30d"])
    assert np.isnan(row["paper_hit_rate_30d"])
    assert np.isnan(row["paper_avg_realized_pnl_30d"])

def test_pit_ticker_row_no_paper_orders_passed():
    dates = pd.date_range("2023-01-01", periods=100, freq="B")
    close = pd.Series(np.linspace(100, 110, 100), index=dates)

    row = _pit_ticker_row(close)

    assert row["paper_has_history_30d"] == 0.0
    assert np.isnan(row["paper_fill_rate_30d"])
    assert np.isnan(row["paper_order_count_30d"])
    assert np.isnan(row["paper_size_variance_30d"])
    assert np.isnan(row["paper_size_vs_kelly_ratio_30d"])
    assert np.isnan(row["paper_hit_rate_30d"])
    assert np.isnan(row["paper_avg_realized_pnl_30d"])


def test_pit_ticker_row_vertical_timeout_requires_full_window_elapsed():
    """Regression test for the "premature vertical timeout" lookahead-adjacent
    bug: apply_triple_barrier reports ``barrier_hit == "vertical"`` BOTH for a
    genuine 5-business-day timeout AND for an order whose future price
    history was simply truncated (by as_of_date) before the full window could
    be evaluated. ``_pit_ticker_row`` must only count the latter as a known,
    resolved outcome once the order's own full intended holding period has
    actually elapsed by ``as_of_date`` -- not merely because ``t1 <
    as_of_naive`` is trivially true for an under-observed order.
    """
    # 150 business days of PIT-truncated history, ending the business day
    # before as_of_date (mirrors how callers slice `close` strictly before
    # as_of_date in production).
    dates = pd.bdate_range(end="2023-04-28", periods=150)
    as_of_date = pd.Timestamp("2023-05-01")  # the next business day
    symbol = "AAPL"

    prices = np.full(150, 100.0)
    # Real historical wiggle (indices 0..139) so EWMA vol is genuinely > 0 at
    # both orders' t0 below (needed for apply_triple_barrier to emit a row at
    # all -- a degenerate sigma <= 0 skips the event entirely).
    prices[:140] = 100.0 + 0.5 * np.sin(np.arange(140) * 0.3)
    flat_value = prices[139]
    # Flat thereafter (indices 140..148) -- covers the "old" order's t0 AND
    # its entire 5-business-day future window, so it genuinely times out at
    # exactly 0% realized return with no barrier ever touched.
    prices[140:149] = flat_value
    # A small, distinct bump on the very last available bar (index 149) --
    # the ONLY future bar available for the "recent" order below. Small
    # enough to stay well inside both barriers (computed from the historical
    # wiggle's sigma), so this also resolves via the "vertical" branch, not a
    # genuine touch -- isolating exactly the case this test targets.
    bump_eps = 0.0005  # 0.05%, far inside the barrier width implied by the wiggle
    prices[149] = flat_value * (1.0 + bump_eps)
    close = pd.Series(prices, index=dates)

    # "recent": placed 2 business days before as_of_date. Only ONE future bar
    # (dates[-1]) is available in this PIT-truncated series -- nowhere near
    # the full 5-business-day window. Its real deadline (t0 + 5 BDay) falls
    # AFTER as_of_date, so this order's outcome is NOT genuinely known yet.
    t0_recent = dates[-2]
    # "old": placed 7 business days before as_of_date -- the full 5-business
    # -day window has genuinely elapsed with real price data available all
    # the way to its deadline. Its outcome IS genuinely known by as_of_date.
    t0_old = dates[-7]

    # Sanity-check the fixture's own premise directly against
    # apply_triple_barrier (the function under test's dependency, unchanged
    # by this fix) before asserting on the higher-level feature.
    barrier_df = apply_triple_barrier(
        events=pd.DatetimeIndex([t0_recent, t0_old]),
        close=close,
        pt_sl_multiples=(2.0, 1.0),
        vertical_barrier_days=5,
        vol_span=100,
    )
    assert barrier_df.loc[t0_recent, "barrier_hit"] == "vertical"
    assert barrier_df.loc[t0_old, "barrier_hit"] == "vertical"
    # The "recent" order's reported t1 falls far short of its real deadline.
    real_deadline_recent = t0_recent + pd.tseries.offsets.BDay(5)
    assert barrier_df.loc[t0_recent, "t1"] == dates[-1]
    assert barrier_df.loc[t0_recent, "t1"] < real_deadline_recent
    assert real_deadline_recent > as_of_date
    # The "old" order's real deadline has already passed by as_of_date, with
    # real (flat) price data observed all the way to it.
    real_deadline_old = t0_old + pd.tseries.offsets.BDay(5)
    assert barrier_df.loc[t0_old, "t1"] == real_deadline_old
    assert real_deadline_old <= as_of_date

    paper_orders = pd.DataFrame([
        {"client_order_id": "recent", "symbol": symbol, "side": "buy", "qty": 10,
         "target_qty": 10, "filled_qty": 10, "timestamp": t0_recent},
        {"client_order_id": "old", "symbol": symbol, "side": "buy", "qty": 10,
         "target_qty": 10, "filled_qty": 10, "timestamp": t0_old},
    ])

    row = _pit_ticker_row(close, symbol, as_of_date, paper_orders)

    # Both orders still count toward the plain sizing/conviction features --
    # only the OUTCOME features (triple-barrier resolution) are gated.
    assert row["paper_order_count_30d"] == 2.0

    # Only the "old" order's outcome is genuinely resolved as of as_of_date;
    # the "recent" order must be excluded from the resolved set entirely.
    # The "old" order is exactly flat (0% return) by construction, so if the
    # resolved set is correct, paper_avg_realized_pnl_30d == 0.0 exactly. If
    # the bug were present, the "recent" order's non-zero bump_eps return
    # would also be averaged in, giving a distinctly non-zero result
    # (bump_eps / 2), so this assertion actually exercises the fix rather
    # than passing for either behavior.
    assert not np.isnan(row["paper_hit_rate_30d"])
    assert row["paper_hit_rate_30d"] == 0.0
    assert not np.isnan(row["paper_avg_realized_pnl_30d"])
    assert row["paper_avg_realized_pnl_30d"] == pytest.approx(0.0, abs=1e-9)
