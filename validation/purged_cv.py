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

        is_multi = isinstance(X.index, pd.MultiIndex)

        if is_multi:
            # The block-partitioning and purge/embargo logic below both assume
            # positional row order matches chronological order (the same
            # assumption already implicit for a plain DatetimeIndex). For a
            # MultiIndex that assumption is easy to violate silently -- e.g. a
            # frame built via pd.concat per-ticker rather than sorted by date
            # first -- so it's verified explicitly rather than trusted.
            date_level = X.index.get_level_values(0)
            if not date_level.is_monotonic_increasing:
                raise ValueError(
                    "CombinatorialPurgedCV.split(): X's MultiIndex level 0 "
                    "(treated as the Date level) must be sorted "
                    "(monotonic increasing) -- CPCV's contiguous block "
                    "partitioning assumes positional order matches "
                    "chronological order. Sort X by its Date level "
                    "(e.g. X.sort_index(level=0)) before calling split()."
                )

        # Define default t1 if not provided
        if t1 is None:
            if is_multi:
                raise ValueError(
                    "CombinatorialPurgedCV.split(): a default t1 cannot be safely "
                    "synthesized for a MultiIndex -- shifting the raw index by -1 "
                    "would mix across the index's non-date levels (e.g. tickers) "
                    "unless the frame is guaranteed sorted with each entity's rows "
                    "contiguous, which this method cannot verify. Pass t1 "
                    "explicitly (indexed the same way as X)."
                )
            # Each event ends at the next index/timestamp.
            # The last element can't be shifted forward; we set it to one step
            # beyond the final index value.  For string/label indices (e.g. ticker
            # symbols) string + int would raise TypeError, so fall back to re-using
            # the final label itself — a zero-duration sentinel that is still the
            # correct type for the downstream string comparisons in the purge loop.
            t1_times = pd.Series(X.index).shift(-1)
            if isinstance(X.index, pd.DatetimeIndex):
                t1_times.iloc[-1] = X.index[-1] + pd.Timedelta(days=1)
            elif pd.api.types.is_integer_dtype(X.index.dtype):
                t1_times.iloc[-1] = X.index[-1] + 1
            else:
                # String or other label index: sentinel = the label itself
                t1_times.iloc[-1] = X.index[-1]
            t1 = pd.Series(t1_times.values, index=X.index)
        elif is_multi and len(t1) and isinstance(t1.iloc[0], tuple):
            # Guard against a caller passing t1 values that are themselves
            # MultiIndex tuples (e.g. built off X.index directly) rather than
            # plain, level-0-comparable scalars -- this would otherwise fail
            # silently as a tuple-vs-timestamp comparison later in the purge
            # loop instead of raising here.
            raise ValueError(
                "CombinatorialPurgedCV.split(): t1 values must be plain "
                "timestamps/scalars comparable to the Date level, not "
                "MultiIndex tuples -- extract the Date level first, e.g. "
                "t1 = pd.Series(X.index.get_level_values('Date') + offset, "
                "index=X.index)."
            )

        # 1. Partition observations into contiguous blocks
        indices = np.arange(n_samples)
        block_size = n_samples // self.n_splits
        blocks = []
        for i in range(self.n_splits):
            start = i * block_size
            end = (i + 1) * block_size if i < self.n_splits - 1 else n_samples
            blocks.append(indices[start:end])

        # 2. Extract 1D array representations for vectorized comparisons
        if is_multi:
            X_times = X.index.get_level_values(0).to_numpy()
        else:
            X_times = X.index.to_numpy()
        t1_vals = t1.to_numpy()

        # Calculate embargo size (in index bars)
        embargo_size = int(n_samples * self.embargo_pct)

        # 3. Precompute per-block masks (test mask & drop mask)
        # For each block b, precalculate which samples would be purged/embargoed
        # if b is in the test set.
        block_test_masks: List[np.ndarray] = []
        block_drop_masks: List[np.ndarray] = []

        for b in range(self.n_splits):
            block_indices = blocks[b]
            b_mask = np.zeros(n_samples, dtype=bool)
            b_mask[block_indices] = True
            block_test_masks.append(b_mask)

            test_start_time = X_times[block_indices[0]]
            test_end_time = X_times[block_indices[-1]]
            max_test_t1 = t1_vals[block_indices].max()
            test_end_idx = block_indices[-1]

            # Purging conditions for block b:
            # Case 1: Train starts within test block
            starts_within = (X_times >= test_start_time) & (X_times <= test_end_time)
            # Case 2: Train ends after test start, but starts before test end (overlaps start)
            overlaps_start = (t1_vals >= test_start_time) & (X_times <= test_start_time)
            # Case 3: Train starts before max_test_t1 and ends after test start (overlaps end)
            overlaps_end = (X_times >= test_start_time) & (X_times <= max_test_t1)
            # Embargo: Purge if train starts within embargo window after test block end index
            embargo = (indices > test_end_idx) & (indices <= test_end_idx + embargo_size)

            drop_mask = starts_within | overlaps_start | overlaps_end | embargo
            block_drop_masks.append(drop_mask)

        # 4. Generate combination paths
        combos = list(combinations(range(self.n_splits), self.n_test_splits))

        for combo in combos:
            test_mask = np.zeros(n_samples, dtype=bool)
            combo_drop_mask = np.zeros(n_samples, dtype=bool)

            for b in combo:
                test_mask |= block_test_masks[b]
                combo_drop_mask |= block_drop_masks[b]

            train_mask = (~test_mask) & (~combo_drop_mask)
            purged_train_idx = indices[train_mask]
            test_idx = indices[test_mask]

            yield purged_train_idx, test_idx, combo

