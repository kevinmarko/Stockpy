import pytest
import os
import tempfile
import pandas as pd
from datetime import datetime, timedelta
from transactions_store import TransactionsStore, Trade

def test_transactions_store_crud():
    # Use an in-memory SQLite database for testing CRUD
    store = TransactionsStore(db_url="sqlite:///:memory:")
    
    # 1. Insert a new open trade
    entry_ts = datetime(2026, 6, 20, 10, 0, 0)
    trade_id = store.record_trade(
        symbol="AAPL",
        side="long",
        entry_ts=entry_ts,
        entry_price=180.5,
        shares=100.0,
        strategy="Momentum",
        notes="Test trade entry"
    )
    assert isinstance(trade_id, int)
    
    # Verify it is in open trades
    open_df = store.open_trades_df()
    assert len(open_df) == 1
    assert open_df.iloc[0]["symbol"] == "AAPL"
    assert open_df.iloc[0]["side"] == "long"
    assert open_df.iloc[0]["entry_price"] == 180.5
    assert open_df.iloc[0]["shares"] == 100.0
    assert open_df.iloc[0]["strategy"] == "Momentum"
    assert open_df.iloc[0]["exit_ts"] is None
    
    # Verify trade history is returned correctly
    hist_df = store.get_trade_history("AAPL")
    assert len(hist_df) == 1
    assert hist_df.iloc[0]["trade_id"] == trade_id
    
    # 2. Close the trade
    exit_ts = datetime(2026, 6, 22, 16, 0, 0)
    store.close_trade(trade_id, exit_ts=exit_ts, exit_price=190.2)
    
    # Verify open trades is now empty
    open_df = store.open_trades_df()
    assert len(open_df) == 0
    
    # Verify closed trades has the closed trade
    closed_df = store.closed_trades_df()
    assert len(closed_df) == 1
    assert closed_df.iloc[0]["symbol"] == "AAPL"
    assert closed_df.iloc[0]["exit_price"] == 190.2
    assert closed_df.iloc[0]["exit_ts"] is not None
