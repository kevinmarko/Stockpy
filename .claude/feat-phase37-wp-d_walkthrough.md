# Phase 37 Work Package D - Walkthrough

## Goal
Address the known gap where the automated options lifecycle (delta hedging, auto exits, strategy execution, 0DTE exits) only ran in `main.py` and was skipped by the Orchestrator Daemon (`desktop/daemon_runtime.py`). Ensure the lifecycle complies with Constraint #1 (Advisory-only) by interacting strictly with the Paper Account/Broker.

## Changes Made
1. **Extracted Options Lifecycle**:
   - Created a new shared module `execution/options_lifecycle.py`.
   - Moved `_run_automated_options_lifecycle` and `_run_automated_delta_hedge_cycle` from `main.py` into this new module (renaming them without the leading underscore).
   - This decouples the logic from `main.py`'s `venv`-reexec guard, making it safe to import in `daemon_runtime.py`.

2. **Wired into Daemon**:
   - Modified `main_orchestrator.py` (`_main_body` and `_main_body_impl`) to return the `macro_dto` object at the end of the pipeline run.
   - Updated `desktop/daemon_runtime.py`'s `_run_one_cycle` method to capture this `macro_dto`.
   - Wired `run_automated_options_lifecycle(macro_dto=macro_dto)` into the daemon immediately after the orchestration cycle completes, correctly feeding it the real macro context from the run.

3. **Ensured Advisory-only Constraint (#1)**:
   - The logic remains cleanly separated, strictly instantiating and utilizing the `OptionsPaperExecutor` (interacting *only* with the Paper Broker/Account), ensuring live Robinhood orders are never placed via this automated flow.

4. **Updated Tests**:
   - Updated `tests/test_main.py` to point to the new import paths for `run_automated_options_lifecycle` and `run_automated_delta_hedge_cycle`.
   - Confirmed `pytest tests/test_orchestrator_daemon.py tests/test_daemon_runtime.py` pass cleanly.
   - Confirmed `pytest tests/test_main.py` passes cleanly.

## Validation Results
- [x] Daemon tests pass.
- [x] Main pipeline tests pass.
- [x] `macro_dto` properly threaded to avoid nullifying the regime gates.
- [x] 0DTE execution and lifecycle idempotency preserved.
