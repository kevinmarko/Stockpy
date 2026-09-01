# Phase 7: Dynamic Position Lifecycle & SPY Delta Hedging (Daemon Integration)

## §0 Dependency Check
*   `execution/options_paper_executor.py` and `pilots/options_hedging.py` (Core logic is already fully implemented)
*   `desktop/daemon_runtime.py` and `main_orchestrator.py` (Daemon orchestration)
*   `main.py` (Legacy orchestration, backward compatibility)
*   `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` (Known issue outlining the remaining Phase 7 daemon gap)

## Goal Description

Implement the final missing piece of Phase 7: **Daemon Integration**. The core execution logic for Phase 7 (automated profit-taking, stop-loss exits, 21-DTE gamma management, atomic rolls, and SPY beta-weighted dynamic delta hedging with tolerance bands) is already fully implemented and tested in the codebase (via `OptionsPaperExecutor` and `options_hedging.py`). However, as documented in `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` (Bug 2), these automated lifecycle actions are currently only wired into the legacy `main.py` orchestrator and are *not* called by the persistent daemon (`desktop/daemon_runtime.py`).

This plan extracts the execution logic into a shared module and wires it into the persistent daemon, formally completing Phase 7.

## User Review Required

> [!IMPORTANT]
> **Key Architectural Decisions for User Alignment**:
> 1. **Cadence Decision**: Strategy auto-execution, auto-exits, and delta hedging will be run on every *full daemon pipeline cycle* (inside `_run_one_cycle`, i.e., gated by `DATA_FRESHNESS_TTL_SECONDS`), rather than on every raw timer wake. This aligns with when new directives and market data are refreshed.
> 2. **Macro Context Threading**: `main_orchestrator.py::_main_body` will be modified to return the `macro_dto` (or the full `RunContext`) to `desktop/daemon_runtime.py::_run_one_cycle`. This is required so `execute_strategy_directives(macro_dto=...)` can properly evaluate the VIX/CREDIT-EVENT premium-selling regime gate in the daemon, avoiding a silent safety-gate regression.

## Open Questions

> [!WARNING]
> The core features for Phase 7 are already completely built and wired into the UI. Is the completion of the daemon integration (fixing the known gap) what you intended for Phase 7, or did you intend to move on to Phase 8 (Volatility Surface)?

## Proposed Changes

### Execution
#### [NEW] `execution/options_lifecycle.py`
- Extract `_run_automated_options_lifecycle` and `_run_automated_delta_hedge_cycle` from `main.py` into this new, shared module.
- Provide clean, importable entrypoints for both the legacy `main.py` loop and the new daemon loop without side-effects.

### Orchestration
#### [MODIFY] `main_orchestrator.py`
- Update `_main_body` and `_main_body_impl` return signatures. Instead of returning `None`, return the `macro_dto` (or a dict containing it) so the daemon caller has the real macro context for the execution gates.

#### [MODIFY] `desktop/daemon_runtime.py`
- Update `_run_one_cycle` to capture the returned `macro_dto` from `main_orchestrator._main_body`.
- Call the newly extracted `execution.options_lifecycle` methods after a successful pipeline run to trigger strategy execution, auto-exits, and delta hedging.

#### [MODIFY] `main.py`
- Remove the inline `_run_automated_options_lifecycle` and `_run_automated_delta_hedge_cycle` methods.
- Update `main.py` to import and call the extracted functions from `execution.options_lifecycle.py` for backward compatibility.

### Documentation
#### [MODIFY] `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`
- Update the known issue doc to reflect that Bug 2 has been fixed and the options lifecycle is fully wired into the persistent daemon.

## Verification Plan

### Automated Tests
- Run `uv run pytest tests/test_main.py` to ensure legacy orchestration still passes.
- Run `uv run pytest tests/test_daemon_runtime.py` to ensure the daemon properly schedules the lifecycle logic.
- Run the full verification suite (`make verify`).
