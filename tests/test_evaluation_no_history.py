import pytest
import numpy as np
import pandas as pd
from evaluation_engine import EvaluationEngine
import transactions_store

def test_evaluation_no_history_returns_nan():
    # Use an in-memory SQLite database to ensure it's empty
    store = transactions_store.TransactionsStore(db_url="sqlite:///:memory:")
    
    # Patch the TransactionsStore in the transactions_store module
    original_store_init = transactions_store.TransactionsStore.__init__
    
    try:
        # Override the constructor to use empty sqlite in-memory DB
        def mock_init(self, db_url=None, *, readonly=False, **kwargs):
            # `readonly` is accepted and dropped: create_readonly_db_engine
            # deliberately rejects :memory: (a read-only in-memory DB is
            # definitionally empty/pointless), so this test-isolation shim
            # must not forward it -- see tests/_db_isolation.py's identical
            # handling for the shared redirect_class_to_memory_db() helper.
            original_store_init(self, db_url="sqlite:///:memory:")
        transactions_store.TransactionsStore.__init__ = mock_init
        
        ee = EvaluationEngine()
        test_df = pd.DataFrame({
            'Symbol': ['XYZ'],
            'sector': ['Technology'],
            'position_size': [10000.0],
            'stop_loss_pct': [0.05],
            'Relative_Strength': [0.0]
        })
        
        benchmark_df = pd.DataFrame({
            'sector': ['Technology'],
            'weight': [1.0],
            'return': [0.02]
        })
        
        processed_df = ee.evaluate_portfolio(test_df, benchmark_df)
        
        # Verify that MAE, MFE, and Edge Ratio are all NaN due to no history
        assert np.isnan(processed_df.iloc[0]['MAE'])
        assert np.isnan(processed_df.iloc[0]['MFE'])
        assert np.isnan(processed_df.iloc[0]['Edge Ratio'])
        
    finally:
        # Restore the original constructor
        transactions_store.TransactionsStore.__init__ = original_store_init
