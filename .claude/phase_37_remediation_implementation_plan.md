# Phase 37: Integrity & Architecture Remediation

This plan formalizes Phase 37, delegating the most critical "Known Open Gaps" and deferred architecture items to a strike team of 4 concurrent subagents.

## User Review Required
> [!IMPORTANT]
> Because this touches the core validation gate (Constraint #5), universe resolution (Constraint #4), and the unsupervised daemon (Constraint #1), explicit approval is required before I dispatch the 4 agents to execute this plan.

## Proposed Changes (4-Agent Split)

---
### Work Package A: Options Strategies Deployability Gate (Agent 1)
**Target:** `earnings_crush`, `vol_mispricing`, `dispersion_trading`

Currently, these submit paper trades but bypass the deployability gate entirely.
- **Action**: Register them in `STRATEGY_REGISTRY` (`scripts/refresh_validations.py`).
- **Action**: Build standard adapter functions in `validation/options_harness.py`.
- **Action**: Run the harness. If they fail (PBO/DSR/Sharpe/MaxDD), document the honest FAIL in `docs/VALIDATION_STRATEGY_FIX_LOG.md` and their respective `docs/signals/*.md` files.

---
### Work Package B: Equity Strategies Deployability Gate (Agent 2)
**Target:** `zero_dte_engine`, `gamma_scalper`, `copula_stat_arb`

- **Action**: Register them in `STRATEGY_REGISTRY`. Note that `copula_stat_arb` already has a registry entry per the Phase 19-30 audit, but we will ensure it and the other two are properly wired for routine re-validation.
- **Action**: Enforce the `validation/stress_scenarios.py` gate (since these are options/complex strategies).
- **Action**: Update `VALIDATION_STRATEGY_FIX_LOG.md`.

---
### Work Package C: Universe Disconnect (Agent 3)
**Target:** `universe_engine.py`, `forecasting_engine.py`, `main_orchestrator.py`

The active trading universe (~430 symbols) is disconnected from the forecast universe (26 symbols), causing `forecast_available=False` for almost all holdings.
- **Action**: Unify the forecast universe generation to pull directly from the active `portfolio_sync.resolve_universe()` output.
- **Action**: Ensure that symbols without sufficient history degrade to an honest `NaN` forecast without crashing the pipeline (Constraint #4 & #6).

---
### Work Package D: Daemon Automated Lifecycle (Agent 4)
**Target:** `desktop/daemon_runtime.py`, `execution/options_paper_executor.py`

Options auto-exits and delta hedging currently only run in `main.py` and are skipped if the persistent daemon is used.
- **Action**: Wire the `_run_automated_options_lifecycle()` block into `OrchestratorDaemon._timer_loop`.
- **Action**: Enforce Constraint #1 (Advisory-only) by ensuring the daemon cannot submit live trades, only routing to `FMPPaperBroker`.

## Verification Plan
### Automated Tests
- The agents will run `pytest` for their respective domains (`test_refresh_validations.py`, `test_daemon_runtime.py`, `test_portfolio_sync.py`).
- Agent 1 & 2 will execute the `validation/harness.py` for the new registry entries.

### Manual Verification
- We will run `make verify` on the merged branch.
- We will verify the `VALIDATION_STRATEGY_FIX_LOG.md` documentation updates exist.
