import pytest
import numpy as np
import pandas as pd
from validation.purged_cv import CombinatorialPurgedCV

def test_cpcv_paths_combinations():
    """Verify that C(10, 2) yields exactly 45 unique path combinations."""
    # Generate 500 samples of data
    df = pd.DataFrame(np.random.randn(500, 2), index=pd.date_range("2020-01-01", periods=500))
    cv = CombinatorialPurgedCV(n_splits=10, n_test_splits=2)
    
    splits = list(cv.split(df))
    assert len(splits) == 45
    
    # Check that each split is unique
    path_ids = [s[2] for s in splits]
    assert len(set(path_ids)) == 45
    
    # Check that train_idx and test_idx are valid index arrays
    for train_idx, test_idx, path_id in splits:
        assert isinstance(train_idx, np.ndarray)
        assert isinstance(test_idx, np.ndarray)
        # Verify no intersection between train and test
        intersection = np.intersect1d(train_idx, test_idx)
        assert len(intersection) == 0
        # Ensure test set is non-empty
        assert len(test_idx) > 0

@pytest.fixture
def multiindex_df():
    dates = pd.date_range("2020-01-01", periods=100)
    tickers = ["AAPL", "MSFT"]
    idx = pd.MultiIndex.from_product([dates, tickers], names=["Date", "Ticker"])
    df = pd.DataFrame(np.random.randn(200, 2), index=idx)
    return df

def test_cpcv_multiindex_requires_explicit_t1(multiindex_df):
    cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2)
    with pytest.raises(ValueError, match="MultiIndex"):
        list(cv.split(multiindex_df))

def test_cpcv_multiindex_purge_uses_date_level_only(multiindex_df):
    cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2, embargo_pct=0.0)
    # Explicit t1: next day for each row
    t1_dates = multiindex_df.index.get_level_values("Date") + pd.Timedelta(days=1)
    t1 = pd.Series(t1_dates, index=multiindex_df.index)
    
    splits = list(cv.split(multiindex_df, t1=t1))
    assert len(splits) == 10  # C(5, 2) = 10
    
    for train_idx, test_idx, _ in splits:
        train_dates = set(multiindex_df.index.get_level_values("Date")[train_idx])
        test_dates = set(multiindex_df.index.get_level_values("Date")[test_idx])
        # Assert no date overlap between train and test blocks
        assert len(train_dates.intersection(test_dates)) == 0

def test_cpcv_plain_index_unaffected_by_multiindex_branch():
    df = pd.DataFrame(np.random.randn(500, 2), index=pd.date_range("2020-01-01", periods=500))
    cv = CombinatorialPurgedCV(n_splits=10, n_test_splits=2)
    
    splits = list(cv.split(df))
    assert len(splits) == 45
    
    path_ids = [s[2] for s in splits]
    assert len(set(path_ids)) == 45
    
    for train_idx, test_idx, _ in splits:
        intersection = np.intersect1d(train_idx, test_idx)
        assert len(intersection) == 0
