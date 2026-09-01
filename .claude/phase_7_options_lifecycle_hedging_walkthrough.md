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
4. **Test Leakage Fix**:
   Fixed a widespread issue where tests in `tests/test_options_lifecycle.py` used `patch("data.paper_account_store.PaperAccountStore").start()` without `.stop()`, polluting downstream tests (like `test_zero_dte_engine.py`). Replaced all with `with patch(...)` context managers.

## Testing & Verification
- Unit tests added to `tests/test_options_lifecycle.py` targeting the extracted logic.
- Integration test added to `tests/test_daemon_runtime.py` to ensure the daemon threads `macro_dto` correctly and gracefully swallows engine errors to avoid crashing the daemon.
- Ran the full options testing suite (`pytest tests/test_options_*.py tests/test_pilots_paper_broker*.py tests/test_zero_dte*.py`).
- **All tests pass 100% green.**

## Documentation Updated
- Marked Bug 2 as "fully fixed" with resolution details in `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`.
- Updated `docs/architecture/execution.md` to reflect the new `options_lifecycle.py` architecture.
- Removed the "disclosed not fixed" daemon gap disclaimer in `CLAUDE.md`/`AGENTS.md`.
