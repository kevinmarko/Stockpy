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

def test_cpcv_multiindex_rejects_unsorted_date_level(multiindex_df):
    """A MultiIndex whose Date level isn't monotonic increasing must raise --
    block partitioning silently assumes positional order == chronological
    order, and an unsorted frame would violate that without any other signal."""
    shuffled = multiindex_df.sample(frac=1.0, random_state=0)
    t1_dates = shuffled.index.get_level_values("Date") + pd.Timedelta(days=1)
    t1 = pd.Series(t1_dates, index=shuffled.index)
    cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2)
    with pytest.raises(ValueError, match="sorted"):
        list(cv.split(shuffled, t1=t1))

def test_cpcv_multiindex_rejects_tuple_valued_t1(multiindex_df):
    """A t1 Series whose VALUES are themselves MultiIndex tuples (e.g. built
    directly off X.index rather than the extracted Date level) must raise
    with a clear message instead of silently comparing tuples to timestamps
    deep in the purge loop."""
    # Deliberately wrong: values are (Date, Ticker) tuples, not plain dates.
    t1 = pd.Series(list(multiindex_df.index), index=multiindex_df.index)
    cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2)
    with pytest.raises(ValueError, match="tuple"):
        list(cv.split(multiindex_df, t1=t1))

def test_cpcv_multiindex_purge_correct_with_uneven_group_sizes():
    """Regression for the case the primary MultiIndex fixture happens not to
    exercise: block boundaries (drawn purely from row position) landing
    mid-date when different dates carry different numbers of tickers -- the
    realistic shape of real market data where not every symbol trades every
    day. Purging must still be correct because it compares dates, not block
    membership."""
    rng = np.random.RandomState(0)
    rows = []
    dates = pd.date_range("2020-01-01", periods=50)
    for d in dates:
        n_tickers = rng.choice([1, 2, 3])
        for t in range(n_tickers):
            rows.append((d, f"T{t}"))
    idx = pd.MultiIndex.from_tuples(rows, names=["Date", "Ticker"])
    df = pd.DataFrame(rng.randn(len(idx), 2), index=idx)

    t1 = pd.Series(df.index.get_level_values("Date") + pd.Timedelta(days=1), index=df.index)
    cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=2, embargo_pct=0.0)
    splits = list(cv.split(df, t1=t1))
    assert len(splits) == 10  # C(5, 2)

    for train_idx, test_idx, _ in splits:
        train_dates = set(df.index.get_level_values("Date")[train_idx])
        test_dates = set(df.index.get_level_values("Date")[test_idx])
        assert len(train_dates.intersection(test_dates)) == 0
