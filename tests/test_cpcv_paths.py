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
