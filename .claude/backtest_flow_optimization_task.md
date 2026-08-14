# Task: Accelerate Backtest Execution Performance

- [x] **Phase 1: Vectorize CombinatorialPurgedCV Splitting** <!-- id: p1 -->
  - [x] Implement vectorized boolean mask splitting in `validation/purged_cv.py` <!-- id: p1_1 -->
  - [x] Create `tests/test_purged_cv_vectorization.py` verifying exact split parity across index types <!-- id: p1_2 -->
  - [x] Verify CPCV and MultiIndex test suite (`tests/test_cpcv_paths.py`, `tests/test_harness_multiindex_t1.py`) <!-- id: p1_3 -->
- [x] **Phase 2: Optimize Strategy Slicing & Parallel Validation Runner** <!-- id: p2 -->
  - [x] Optimize slice indexing in `_make_strategy_fn` in `scripts/refresh_validations.py` <!-- id: p2_1 -->
  - [x] Implement `_validate_single_strategy` and `max_workers` concurrent execution in `run_validations` <!-- id: p2_2 -->
  - [x] Add `--workers` flag to `scripts/refresh_validations.py` CLI <!-- id: p2_3 -->
  - [x] Add unit tests for parallel worker execution in `tests/test_refresh_validations.py` <!-- id: p2_4 -->
- [x] **Phase 3: Documentation & Verification** <!-- id: p3 -->
  - [x] Update `docs/architecture/validation-and-signals.md` <!-- id: p3_1 -->
  - [x] Update `CLAUDE.md` and `AGENTS.md` <!-- id: p3_2 -->
  - [x] Run full test suite to ensure zero regressions <!-- id: p3_3 -->
  - [x] Copy artifacts to `.claude/` <!-- id: p3_4 -->
