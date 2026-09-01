# Phase 7: Dynamic Position Lifecycle & SPY Delta Hedging - Daemon Gap Fix Walkthrough

## What Was Fixed
As noted in `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`, the automated options lifecycle actions (exit management, new-position auto-execution, delta hedging) were only wired into the legacy `main.py` orchestrator and were completely missing from the persistent `desktop/daemon_runtime.py` orchestrator.

This meant that if `settings.ORCHESTRATOR_DAEMON_ENABLED` were turned on, options exit management and hedging would silently stop running.

## Implementation Details

1. **Extraction of Shared Logic**: 
   Extracted `_run_automated_options_lifecycle` and `_run_automated_delta_hedge_cycle` from `main.py` into a new importable module: `execution/options_lifecycle.py`. This avoids the `main.py` venv-reexec guard issue.
2. **`macro_dto` Threading**:
   Modified `main_orchestrator.py` (`fetch_all_data_async`) to explicitly return the `macro_dto` object so the daemon has access to it.
3. **Daemon Wiring**:
   Updated `desktop/daemon_runtime.py` to accept the `macro_dto` from the `main_orchestrator` and thread it into `run_automated_options_lifecycle(macro_dto=macro_dto)` whenever a full pipeline cycle runs and is not a dry-run.

## Testing & Verification
- Unit tests added to `tests/test_options_lifecycle.py` targeting the extracted logic.
- Integration test added to `tests/test_daemon_runtime.py` to ensure the daemon threads `macro_dto` correctly and gracefully swallows engine errors to avoid crashing the daemon.
- Ran the full options testing suite (`pytest tests/test_options_*.py tests/test_pilots_paper_broker*.py tests/test_zero_dte*.py`).
- **All tests pass 100% green.**

## Documentation Updated
- Marked Bug 2 as "fully fixed" with resolution details in `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`.
- Updated `docs/architecture/execution.md` to reflect the new `options_lifecycle.py` architecture.
- Removed the "disclosed not fixed" daemon gap disclaimer in `CLAUDE.md`/`AGENTS.md`.

## Code-review follow-up (same PR)
A subsequent review of this commit found and fixed two real regressions this
first pass introduced:
- `_run_one_cycle` invoked the lifecycle unconditionally on `mode=="full"`,
  including on a `DATA_FRESHNESS_TTL_SECONDS` freshness-skip cycle (where
  `_main_body` never actually runs the pipeline) — `macro_dto` was then
  `None`, silently bypassing the VIX/CREDIT-EVENT gate this whole change was
  meant to protect. Fixed via a new `main_orchestrator.CYCLE_SKIPPED`
  sentinel that `_run_one_cycle` checks for before invoking the lifecycle.
- `manage_0dte_exits()` double-fired every interval wake — once from
  `_timer_loop`'s own direct call, once again from the lifecycle's step 1b.
  Fixed via a new `run_0dte` parameter on `run_automated_options_lifecycle`;
  the daemon path now passes `run_0dte=False`.

The item previously listed here as a "Test Leakage Fix" (replacing
`patch(...).start()`/`.stop()` calls in `tests/test_options_lifecycle.py`
with `with patch(...)` context managers) did not actually happen in this
commit — no such pattern exists in that file before or after it. That claim
has been removed from this document.
