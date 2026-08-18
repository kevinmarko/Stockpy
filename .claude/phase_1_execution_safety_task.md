# Task Tracker: Phase 1 — Backend Execution Integrity & Safety Gating

## Status Overview
- **Implementation Status**: Complete
- **Audit & Verification Status**: 100% Passed (101/101 Tests Passed originally; 105/105 after code-review fixes below, re-run 2026-08-17)

**Code-review fixes (2026-08-17)**: an independent review found `_resolve_symbol_beta` called
`HistoricalStore.get_symbol_beta()`, a method that never existed anywhere in the codebase --
every real call silently `AttributeError`'d, was swallowed, and fell back to a hardcoded
`beta=1.0` for every symbol, every time, making the beta-weighting feature below fully inert in
production despite a passing (monkeypatch-masked) test. Separately, `main.py`'s outer gate for
the new 0DTE exit block never included `OPTIONS_0DTE_ENABLED`, so enabling that flag alone never
reached the safety exit via `main.py` (only via the daemon path). Both fixed; see
`phase_1_execution_safety_walkthrough.md` for the detail.

## Task Checklist

### 1. Options Risk Engine & Beta Hedging
- [x] Implement `_resolve_symbol_betas` (batched, real regression beta via `HistoricalStore.get_bars` + `data/fmp_fundamentals.py::compute_beta` -- **not** the originally-implemented `_resolve_symbol_beta`, whose `HistoricalStore.get_symbol_beta()` call never existed; fixed during code review, 2026-08-17)
- [x] Update `calculate_portfolio_greeks` with true $\sum (\text{Dollar Delta}_i \times \beta_i) / S_{\text{SPY}}$ over positions with a measurable beta; a position with no measurable beta is excluded from that sum specifically (never fabricated to 1.0) and reported in `beta_data_unavailable_symbols`
- [x] Verify `pilots/options_hedging.py` delta hedging order generation

### 2. 0DTE 15:45 ET Auto-Liquidation & Daemon Periodic Loop
- [x] Wire `manage_0dte_exits` into `desktop/daemon_runtime.py`
- [x] Wire `manage_0dte_exits` into `main.py` options lifecycle block -- **corrected 2026-08-17**: the outer gate originally omitted `OPTIONS_0DTE_ENABLED`, so setting only that flag never reached the block; both call sites now share one gate helper, `pilots.zero_dte_engine.is_0dte_auto_exit_enabled()`, instead of duplicating the flag expression

### 3. ML Meta-Labeler Startup Lifecycle
- [x] Verify `_ensure_meta_labeler_loaded()` in `execution/options_paper_executor.py`

### 4. FIX 4.4 Protocol Gateway
- [x] Verify session sequence gap recovery in `execution/fix_gateway.py`

### 5. Verification & Testing
- [x] Run `pytest tests/test_options_risk.py tests/test_options_hedging.py tests/test_zero_dte_engine.py tests/test_options_paper_executor.py tests/test_daemon_runtime.py -v` (101 passed)
- [x] Run `bandit -r . -x './tests,./webapp,./.venv,./node_modules,./gui' -ll -ii` (0 issues)
- [x] Run `stockpy_codebase_auditor.py --root . --fail-on HIGH` (0 Critical / 0 High)

