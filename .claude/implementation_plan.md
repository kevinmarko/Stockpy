# Implementation Plan: Optimize Backtest Execution Flow & Acceleration

Accelerate strategy validation and backtesting execution flow across the platform. This plan details vectorizing Combinatorial Purged Cross-Validation (CPCV) splitting, eliminating redundant pandas index intersection overhead in strategy adapters, and adding multi-worker parallel execution to `scripts/refresh_validations.py`.

## User Review Required

> [!NOTE]
> All optimizations are strictly algorithmic, vectorized, and non-breaking:
> 1. **Vectorized CPCV Splitting (`validation/purged_cv.py`)**: Replaces the $O(\text{combos} \times \text{blocks} \times N)$ per-row Python iteration and iloc lookups with vectorized NumPy boolean array broadcasting, producing bit-identical train/test index splits.
> 2. **Fast Slice Indexing in Adapters (`scripts/refresh_validations.py`)**: Replaces repetitive DatetimeIndex `.intersection()` calls with fast subset indexing when parent indices align.
> 3. **Parallel Strategy Validation (`scripts/refresh_validations.py`)**: Adds an opt-in/configurable multi-worker pool (`--workers N` / `max_workers`) for running independent strategy harness evaluations concurrently, defaulting to sequential (`max_workers=1`) for deterministic logging and CI compatibility.

## Proposed Changes

---

### 1. Vectorized CPCV Split Engine

#### [MODIFY] [purged_cv.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/optimize_backtest_execution_flow/validation/purged_cv.py)
- In `CombinatorialPurgedCV.split()`:
  - Extract `X_times` (level 0 if `MultiIndex`, otherwise index numpy array) and `t1_vals = t1.to_numpy()` once at the top of the function.
  - Pre-allocate the full sample indices array `indices = np.arange(n_samples)`.
  - For each combo in `combinations(range(n_splits), n_test_splits)`:
    - Build `test_mask` from union of test blocks.
    - Vectorize the purge and embargo drop conditions against all training samples using NumPy boolean operations:
      - `starts_within = (X_times >= test_start_time) & (X_times <= test_end_time)`
      - `overlaps_start = (t1_vals >= test_start_time) & (X_times <= test_start_time)`
      - `overlaps_end = (X_times >= test_start_time) & (X_times <= max_test_t1)`
      - `embargo = (indices > test_end_idx) & (indices <= test_end_idx + embargo_size)`
    - Form `purged_train_idx = indices[~test_mask & ~drop_mask]`.
  - Retain all MultiIndex monotonic checks, type guards, and error messages.

---

### 2. Strategy Adapter Slice Indexing & Parallel Runner

#### [MODIFY] [refresh_validations.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/optimize_backtest_execution_flow/scripts/refresh_validations.py)
- In `_make_strategy_fn`:
  - Optimize the `full_rets` slicing for `train_returns` and `test_returns`:
    - Fast path if `full_rets.index.equals(y_train.index)`: return `full_rets` directly.
    - Fast path with `try: full_rets.loc[y_train.index] except KeyError:` fallback to `full_rets.loc[full_rets.index.intersection(y_train.index)]`.
- Refactor single-strategy validation into a standalone helper:
  - `_validate_single_strategy(name, strategy_tuple, closes_df, shares, output_dir, n_cpcv_splits, n_test_splits, cost_model)`
- Update `run_validations`:
  - Add parameter `max_workers: Optional[int] = 1`.
  - When `max_workers == 1`: execute sequentially as today.
  - When `max_workers > 1`: execute via `concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)` to run independent strategy validation harnesses concurrently.
- Update CLI `main()` in `scripts/refresh_validations.py`:
  - Add `--workers` / `-w` (int, default 1) to argparse.

---

### 3. Documentation Updates

#### [MODIFY] [validation-and-signals.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/optimize_backtest_execution_flow/docs/architecture/validation-and-signals.md)
- Update the documentation for `validation/purged_cv.py` and `scripts/refresh_validations.py` to describe the vectorized CPCV split engine and multi-worker validation runner (`--workers`).

#### [MODIFY] [CLAUDE.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/optimize_backtest_execution_flow/CLAUDE.md)
- Document the new `--workers` flag on `python scripts/refresh_validations.py`.

---

### 4. Test Suite Enhancements

#### [MODIFY] [test_refresh_validations.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/optimize_backtest_execution_flow/tests/test_refresh_validations.py)
- Add unit tests verifying `max_workers > 1` concurrency in `run_validations`.
- Add test verifying CLI `--workers` flag parsing and execution.

#### [NEW] [test_purged_cv_vectorization.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/optimize_backtest_execution_flow/tests/test_purged_cv_vectorization.py)
- Add comprehensive parity tests asserting that vectorized `CombinatorialPurgedCV.split()` produces byte-identical index splits to the reference logic across single DatetimeIndex, MultiIndex, IntegerIndex, and Custom Label Index.

## Verification Plan

### Automated Tests
- Run targeted CPCV tests:
  ```bash
  pytest tests/test_cpcv_paths.py tests/test_purged_cv_vectorization.py tests/test_metrics_cpcv_oos_aggregates.py tests/test_harness_multiindex_t1.py
  ```
- Run validation runner tests:
  ```bash
  pytest tests/test_refresh_validations.py
  ```
- Run full validation suite:
  ```bash
  pytest tests/test_validation_*.py
  ```

### Manual Verification
- Benchmark execution time of `scripts/refresh_validations.py` before and after optimization.
