"""
InvestYo Quant Platform - Combinatorial Purged Cross-Validation
==============================================================
Implements Combinatorial Purged Cross-Validation (CPCV) to split data
into training and testing paths while preventing lookahead leakages and
serial correlation leakage through purging and embargoing.
"""

import logging
from itertools import combinations
from typing import Generator, Tuple, List
import numpy as np
import pandas as pd

# Set up module logger
logger = logging.getLogger("Purged_CV")

class CombinatorialPurgedCV:
    """
    Combinatorial Purged Cross-Validation (CPCV).
    Divides N groups into splits, picks k test splits, and generates C(N, k) paths.
    Applies purging and embargo to remove training overlaps.
    """
    def __init__(self, n_splits: int = 10, n_test_splits: int = 2, embargo_pct: float = 0.01):
        if n_splits <= n_test_splits:
            raise ValueError("n_splits must be greater than n_test_splits")
        self.n_splits = n_splits
        self.n_test_splits = n_test_splits
        self.embargo_pct = embargo_pct

    def split(
        self, 
        X: pd.DataFrame, 
        y: pd.Series = None, 
        t1: pd.Series = None
    ) -> Generator[Tuple[np.ndarray, np.ndarray, Tuple[int, ...]], None, None]:
        """
        Yields (train_idx, test_idx, path_id).
        
        Args:
            X: Input features DataFrame.
            y: Input target Series.
            t1: Series of event end times (values) indexed by start times (index matching X).
                If None, defaults to start_time + 1 bar.
        """
        n_samples = len(X)
        if n_samples < self.n_splits:
            raise ValueError("Number of samples is less than n_splits")

        # Define default t1 if not provided
        if t1 is None:
            # Each event ends at the next index/timestamp
            t1_times = pd.Series(X.index).shift(-1)
            t1_times.iloc[-1] = X.index[-1] + pd.Timedelta(days=1) if isinstance(X.index, pd.DatetimeIndex) else X.index[-1] + 1
            t1 = pd.Series(t1_times.values, index=X.index)

        # 1. Partition observations into contiguous blocks
        indices = np.arange(n_samples)
        block_size = n_samples // self.n_splits
        blocks = []
        for i in range(self.n_splits):
            start = i * block_size
            end = (i + 1) * block_size if i < self.n_splits - 1 else n_samples
            blocks.append(indices[start:end])

        # 2. Get all combinations C(N, k)
        combos = list(combinations(range(self.n_splits), self.n_test_splits))
        
        # Calculate embargo size (in index bars)
        embargo_size = int(n_samples * self.embargo_pct)

        for combo in combos:
            # Test indices are the union of the selected test blocks
            test_idx = np.concatenate([blocks[b] for b in combo])
            test_idx = np.sort(test_idx)
            
            # Initial train indices are the union of the remaining blocks
            train_idx_list = [blocks[b] for b in range(self.n_splits) if b not in combo]
            if not train_idx_list:
                train_idx = np.array([], dtype=int)
            else:
                train_idx = np.concatenate(train_idx_list)
                train_idx = np.sort(train_idx)

            # 3. Purging and Embargo
            # For each test block, we find its time range and remove overlapping train samples
            purged_train_idx = set(train_idx)
            
            for b in combo:
                block_indices = blocks[b]
                test_start_time = X.index[block_indices[0]]
                test_end_time = X.index[block_indices[-1]]
                
                # Get max t1 in the test block
                test_t1 = t1.iloc[block_indices]
                max_test_t1 = test_t1.max()
                
                # Purging and Embargo: we check each training index
                for tr_idx in list(purged_train_idx):
                    tr_time = X.index[tr_idx]
                    tr_t1 = t1.iloc[tr_idx]
                    
                    # Purge if training event overlaps with test block
                    # Case 1: Train starts within test block
                    starts_within = (tr_time >= test_start_time) and (tr_time <= test_end_time)
                    # Case 2: Train ends after test start, but starts before test end (overlaps start)
                    overlaps_start = (tr_t1 >= test_start_time) and (tr_time <= test_start_time)
                    # Case 3: Train starts before max_test_t1 and ends after test start (overlaps end)
                    overlaps_end = (tr_time >= test_start_time) and (tr_time <= max_test_t1)
                    
                    if starts_within or overlaps_start or overlaps_end:
                        purged_train_idx.discard(tr_idx)
                        continue
                    
                    # Embargo: Purge if train starts within embargo window after test block end index
                    test_end_idx = block_indices[-1]
                    if tr_idx > test_end_idx and tr_idx <= test_end_idx + embargo_size:
                        purged_train_idx.discard(tr_idx)

            yield np.sort(list(purged_train_idx)), test_idx, combo
