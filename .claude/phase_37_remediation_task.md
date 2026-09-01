# Phase 37 Task Tracker

- [ ] **Work Package A: Options Strategies Deployability Gate (Agent 1)**
  - [ ] Register `earnings_crush`, `vol_mispricing`, `dispersion_trading`.
  - [ ] Run validation harness.
  - [ ] Document in `VALIDATION_STRATEGY_FIX_LOG.md` and `docs/signals/`.
- [x] **Work Package B: Equity/Complex Strategies Deployability Gate (Agent 2)**
  - [x] Register `zero_dte_engine`, `gamma_scalper`, `copula_stat_arb`.
  - [x] Run validation harness.
  - [x] Document in `VALIDATION_STRATEGY_FIX_LOG.md` and `docs/signals/`.
- [ ] **Work Package C: Universe Disconnect (Agent 3)**
  - [ ] Unify forecast universe with active trading universe.
  - [ ] Enforce NaN fallback for missing history.
- [ ] **Work Package D: Daemon Automated Lifecycle (Agent 4)**
  - [ ] Wire `_run_automated_options_lifecycle()` into `OrchestratorDaemon._timer_loop`.
  - [ ] Enforce advisory-only/paper-only constraint.
