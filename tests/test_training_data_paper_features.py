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
        {"client_order_id": "1", "symbol": "AAPL", "side": "buy", "qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-03-20")},
        # 2. 15 days ago (should be included)
        {"client_order_id": "2", "symbol": "AAPL", "side": "buy", "qty": 20, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-16")},
        # 3. 5 days ago (should be included)
        {"client_order_id": "3", "symbol": "AAPL", "side": "sell", "qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-26")},
        # 4. Exactly on as_of_date (should be strictly excluded - lookahead!)
        {"client_order_id": "4", "symbol": "AAPL", "side": "buy", "qty": 100, "filled_qty": 100, "timestamp": pd.Timestamp("2023-05-01")},
        # 5. Different symbol (should be excluded)
        {"client_order_id": "5", "symbol": "MSFT", "side": "buy", "qty": 10, "filled_qty": 10, "timestamp": pd.Timestamp("2023-04-20")},
    ])
    
    row = _pit_ticker_row(close, symbol, as_of_date, paper_orders)
    
    # We expect only orders 2 and 3 to be included in the 30d window.
    
    # Total qty = 20 + 10 = 30
    # Filled qty = 10 + 10 = 20
    # Fill rate = 20 / 30 = 0.666...
    assert pytest.approx(row["paper_fill_rate_30d"], 0.001) == 20 / 30


def test_pit_ticker_row_empty_paper_orders():
    dates = pd.date_range("2023-01-01", periods=100, freq="B")
    close = pd.Series(np.linspace(100, 110, 100), index=dates)
    as_of_date = pd.Timestamp("2023-05-01")
    symbol = "AAPL"
    
    # Empty DataFrame with same columns
    paper_orders = pd.DataFrame(columns=["client_order_id", "symbol", "side", "qty", "filled_qty", "timestamp"])
    
    row = _pit_ticker_row(close, symbol, as_of_date, paper_orders)
    
    assert np.isnan(row["paper_fill_rate_30d"])


def test_pit_ticker_row_no_paper_orders_passed():
    dates = pd.date_range("2023-01-01", periods=100, freq="B")
    close = pd.Series(np.linspace(100, 110, 100), index=dates)
    
    row = _pit_ticker_row(close)
    
    assert np.isnan(row["paper_fill_rate_30d"])
