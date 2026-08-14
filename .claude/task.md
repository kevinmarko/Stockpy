# Task Tracker: Worktree Reconciliation, Gap Closure & Widget Rollout

- [x] **Phase 0 — Reconcile the Worktree (4 Specialized Agents)**
  - [x] Step 1: Run raw pytest suites to `/tmp/widget_test_output.txt` (350 passed)
  - [x] Step 2: Categorize assertions in `tests/test_investyo_mcp_widgets.py` (Tier A vs Tier B)
  - [x] Step 3: Run `git diff HEAD --stat` and audit diff surface against `walkthrough.md`
  - [x] Step 4: Output the Per-Widget Triage & Decision Table
- [x] **Phase 1 — Close Functional Gaps**
  - [x] 1a. Calibrate `MAX_PORTFOLIO_GROSS` (Calibrated to `2.0` in `settings.py` with written Reg-T margin & sizing rationale; documented in `docs/architecture/signal-engines.md`)
  - [x] 1b. Verify CNN-LSTM Purge & Embargo (`purged_train_val_split` and expanding `fit_scalers_walkforward_windows` verified; created standalone `tests/test_cnn_lstm_leakage_audit.py`)
- [x] **Phase 2 — Promote Diagnostic Widgets with Known-Bad Testing**
  - [x] 2a. PIT Coverage & Audit Matrix (`pit-audit-matrix.html`, known-bad empty/lookahead leak tests passing)
  - [x] 2b. Model Diagnostics & Drift (`model-diagnostics.html`, synthetic injected drift tests passing)
- [x] **Phase 3 — Promote Quant & Trading Widgets**
  - [x] 3a. Backtest Tearsheet (`backtest-tearsheet.html` / `run_backtest` & `run_validation_harness` schema tests passing)
  - [x] 3b. Macro Regime Radar (`macro-regime-radar.html` / `get_regime_status` & `trigger_macro_engine` schema tests passing)
  - [x] 3c. Order Ticket (`order-ticket.html` / `propose_paper_trade_for_review` RLHF schema tests passing)
- [x] **Phase 4 — Promote Strategy Tuner, DevTools Integration & Final Release Gate**
  - [x] 4a. Promote `strategy-tuner.html` (`tune_strategy_parameters`) with parameter sensitivity bounds tests
  - [x] 4b. Verify WebApp DevTools utilities (`visual-diff.html`, `network-trace.html`, `devtools-inspector.html`, `lighthouse-scorecard.html`)
  - [x] 4c. Run preflight readiness check and update documentation
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
