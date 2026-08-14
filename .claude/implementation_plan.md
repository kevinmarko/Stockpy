# Implementation Plan: Worktree Reconciliation, Gap Closure, and Diagnostic Widget Rollout

Adopting the roadmap to reconcile `/Users/kevinlee/Stockpy-live` and the `integrate_mcp_devtools_widget` worktree, verify leakage and gross position limits, and sequence widget rollout with rigorous "known-bad" test coverage.

---

## User Review Required

> [!IMPORTANT]
> **No Premature Merges to Main**: All findings from Phase 0 (test output capture, assertion categorization, diff inspection) and Phase 1 (gross cap sweep and purge/embargo audit) will be presented with raw command outputs and diffs before proposing any promotion or merge.

---

## Phase Breakdown

### Phase 0: Worktree Reconciliation & Test Assertion Audit (Immediate)
1. **Raw Test Suite Execution**:
   - Run `pytest tests/test_investyo_mcp_widgets.py -v` and `pytest tests/test_investyo_mcp_server.py -v`, capturing raw output to `/tmp/widget_test_output.txt`.
2. **Assertion Categorization**:
   - Audit `tests/test_investyo_mcp_widgets.py` line-by-line: categorize checks into (a) registration/placeholder substitution vs. (b) behavioral schema & degradation assertions.
3. **Change Surface & Diff Analysis**:
   - Review `git diff origin/main...HEAD --stat` and verify that `walkthrough.md` matches the actual code surface.
4. **Widget Triage Table**:
   - Break down all 10 unmerged widgets:
     - **Diagnostic Priority (Phase 2)**: `pit-audit-matrix.html`, `model-diagnostics.html`
     - **Quant & Trading Core (Phase 3)**: `backtest-tearsheet.html`, `macro-regime-radar.html`, `order-ticket.html`
     - **PWA Dev Tools (Deferred)**: `visual-diff.html`, `network-trace.html`, `devtools-inspector.html`, `lighthouse-scorecard.html`
     - **Parameter Sensitivity (Deferred to post-1a)**: `strategy-tuner.html`

---

### Phase 1: Close Functionally Incomplete Gaps
1. **1a. Calibrate `MAX_PORTFOLIO_GROSS`**:
   - Audit `sizing/position_sizer.py::apply_portfolio_gross_cap()` and `settings.MAX_PORTFOLIO_GROSS`.
   - Run historical evaluation across candidate gross caps (1.0, 1.5, 2.0, 3.0) to observe binding frequency and drawdown impact.
   - Document calibrated default and rationale.
2. **1b. Verify CNN-LSTM Leakage Mitigation**:
   - Audit `cnn_lstm_worker.py::purge()` and cross-sectional normalization fold scoping.
   - Verify purge/embargo boundaries across all walk-forward splits.
   - Write a standalone test/script asserting no training timestamp $\ge$ validation timestamp minus embargo.
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

### Phase 2: Promote Diagnostic Widgets with "Known-Bad" Test Cases
1. **2a. PIT Fundamentals Matrix (`pit-audit-matrix.html`)**:
   - Wire against post-Phase-1 validated pipeline.
   - Write unit tests with synthetic known-bad inputs (lookahead filing dated post-evaluation, missing 45d lag buffer) to verify the report and widget flag them red.
2. **2b. Model Diagnostics & Drift (`model-diagnostics.html`)**:
   - Write unit tests with synthetic injected drift (>15% skill decay, PSI spikes) asserting drift warnings fire.

---

### Phase 3: Sequence Trading & Quant Widgets
- Validate and promote `backtest-tearsheet.html`, `macro-regime-radar.html`, and `order-ticket.html` with verified constraints.
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
```bash
# Capture raw outputs
pytest tests/test_investyo_mcp_widgets.py -v > /tmp/widget_test_output.txt 2>&1
pytest tests/test_investyo_mcp_server.py -v >> /tmp/widget_test_output.txt 2>&1

# Known-bad regression tests
pytest tests/test_pit_leakage_regression.py -v
pytest tests/test_model_drift_synthetic.py -v
```

### Manual & Diff Inspection
- Review raw test logs in `/tmp/widget_test_output.txt`.
- Inspect `git diff origin/main...HEAD --stat`.
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
