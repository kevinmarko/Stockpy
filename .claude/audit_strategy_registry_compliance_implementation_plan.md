# Strategy Registry & Execution Boundary Audit

This plan orchestrates 6 subagents to deeply investigate and fix the 4 documented "open gaps" listed in the Master Session Prompt regarding live execution paths and strategy registry data compliance.

## Goal
To rigidly enforce Constraint #1 (Advisory-only is absolute) and Constraint #4 (Never fabricate a metric) across the repository.

## Components

### 1. `manage_0dte_exits` Hard Stop Enforcement
Ensure that `manage_0dte_exits` is called in all orchestration pathways.
- [x] Verified in `desktop/daemon_runtime.py` and `main.py`.
- [x] Discovered gap in `main_orchestrator.py` standalone run.
- [x] Fix: Wire `manage_0dte_exits` into `main_orchestrator.py:main()`.

### 2. Strategy Registry & Mock/Live API Parity
Enforce strategy registration for `earnings_crush`, `dispersion_trading`, `zero_dte_engine`, `gamma_scalper`, `vol_mispricing`, `copula_stat_arb`.
- [x] Verified `vol_mispricing` and `copula_stat_arb` have genuine backtest adapters.
- [x] Verified `earnings_crush`, `dispersion_trading`, `zero_dte_engine`, and `gamma_scalper` are explicitly registered as `UNGATEABLE_DATA_GAP` or `UNGATEABLE_NOT_A_STRATEGY`.
- [x] Fix: Update `docs/VALIDATION_STRATEGY_FIX_LOG.md` and `docs/signals/*.md` to reflect actual `UNGATEABLE_DATA_GAP` registration status.
- [x] Fix: Add `gamma_scalper` to `OPTIONS_DESK_DEPLOYABILITY_GATES` in `api/pilots_api.py` and return `gate_status` in its endpoint for mock/live parity.

### 3. Universe Re-alignment
Investigate the alleged 430-symbol active vs 26-symbol forecast universe disconnect.
- [x] Proved disconnect was a hallucinated bug based on a regex match (`2026-08`).
- [x] Verified `main.py::_build_universe` passes the wide 500+ symbol list cleanly into `ForecastingEngine`.

### 4. Live Execution Pathways (Robinhood MCP)
Dismantle any capability to interact with live execution.
- [x] Delete `.claude/skills/robinhood-execution/SKILL.md` and `.agents/skills/robinhood-execution/SKILL.md`.
- [x] Purge live-trade approval endpoints from `api/pilots_api.py`.
- [x] Delete `pilots/live_trade_proposals.py` and its tests.

## Verification
- Audited by Honesty Auditor and Execution Auditor.
- Pytest passing cleanly after live execution deletion.
