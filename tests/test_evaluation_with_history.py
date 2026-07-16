import pytest
import math
import numpy as np
import pandas as pd
from datetime import datetime
from evaluation_engine import EvaluationEngine
import transactions_store

def test_evaluation_with_history_computes_excursions():
    # Use in-memory SQLite database
    store = transactions_store.TransactionsStore(db_url="sqlite:///:memory:")
    
    # 1. Record a long trade for AAPL
    entry_ts = datetime(2026, 6, 20, 9, 30, 0)
    trade_id = store.record_trade(
        symbol="AAPL",
        side="long",
        entry_ts=entry_ts,
        entry_price=100.0,
        shares=50.0
    )
    
    # Patch the TransactionsStore
    original_store_init = transactions_store.TransactionsStore.__init__
    
    try:
        def mock_init(self, db_url=None, *, readonly=False, **kwargs):
            # Point TransactionsStore to our prepared test DB. `readonly` is
            # accepted (evaluate_portfolio's own construction now passes
            # readonly=True) and ignored -- this mock always points at the
            # same prepared, write-capable in-memory engine regardless.
            self.engine = store.engine
            self.Session = store.Session
        transactions_store.TransactionsStore.__init__ = mock_init
        
        # Prepare historical price series for hold period
        # Let's say entry_price is 100
        # High reaches 110 (+10%), Low drops to 95 (-5%)
        date_range = pd.date_range(start="2026-06-20", end="2026-06-24", freq="D")
        mock_history = pd.DataFrame({
            "High": [100.0, 105.0, 110.0, 108.0, 104.0],
            "Low":  [100.0, 98.0,  95.0,  97.0,  101.0],
            "Close": [100.0, 103.0, 107.0, 105.0, 103.0]
        }, index=date_range)
        
        # Set timezone to naive for testing
        mock_history.index = mock_history.index.tz_localize(None)
        
        data_provider = {"AAPL": mock_history}
        
        ee = EvaluationEngine()
        test_df = pd.DataFrame({
            'Symbol': ['AAPL'],
            'sector': ['Technology'],
            'position_size': [5000.0],
            'stop_loss_pct': [0.05],
            'Relative_Strength': [0.0]
        })
        
        benchmark_df = pd.DataFrame({
            'sector': ['Technology'],
            'weight': [1.0],
            'return': [0.02]
        })
        
        processed_df = ee.evaluate_portfolio(test_df, benchmark_df, data_provider=data_provider)
        
        # Verify MAE, MFE, Edge Ratio
        # MAE: low of 95, so (100 - 95)/100 = 0.05 (positive magnitude)
        # MFE: high of 110, so (110 - 100)/100 = 0.10
        # Edge Ratio: MFE / MAE = 0.10 / 0.05 = 2.0
        assert math.isclose(processed_df.iloc[0]['MAE'], 0.05, abs_tol=1e-3)
        assert math.isclose(processed_df.iloc[0]['MFE'], 0.10, abs_tol=1e-3)
        assert math.isclose(processed_df.iloc[0]['Edge Ratio'], 2.0, abs_tol=1e-3)
        
    finally:
        transactions_store.TransactionsStore.__init__ = original_store_init
