# Task Tracker: Gaussian HMM Regime Model Parameter Refinement & Audit

- [x] **Area 1: HMM Hyperparameter Tuning & Multi-Covariance Support** <!-- id: 0 -->
  - [x] Add `HMM_COVARIANCE_TYPE` (`"diag"`, `"full"`, `"spherical"`, `"tied"`) with validation in `settings.py` and `gui/env_io.py`. <!-- id: 1 -->
  - [x] Add `HMM_N_ITER` and `HMM_TOL` settings. <!-- id: 2 -->
  - [x] Implement multi-start random restart seeding (`n_inits`) and ridge regularization (`min_covar`). <!-- id: 3 -->
  - [x] Implement AIC/BIC diagnostics and `select_optimal_model` helper in `validation/regime_diagnostics.py`. <!-- id: 4 -->

- [x] **Area 2: Feature Engineering Matrix Extensions** <!-- id: 5 -->
  - [x] Add optional Credit Spread (`BAMLH0A0HYM2`) feature in `build_feature_matrix` and thread through `macro_engine.py`. <!-- id: 6 -->
  - [x] Add optional Realized Vol Term Structure Spread (20D vs 60D RV). <!-- id: 7 -->
  - [x] Add 10-Year Breakeven Inflation (`T10YIE`) feature and `HMM_INFLATION_FEATURE_ENABLED` switch. <!-- id: 8 -->
  - [x] Add causal 252-day rolling z-score standardization flag. <!-- id: 9 -->

- [x] **Area 3: Risk Gate & Dynamic Threshold Calibration** <!-- id: 10 -->
  - [x] Add `HMM_RISK_ON_DOWNGRADE_THRESHOLD` and `HMM_RISK_OFF_AGREEMENT_THRESHOLD` settings. <!-- id: 11 -->
  - [x] Add `KILLSWITCH_VIX_THRESHOLD_AGREED` and `KILLSWITCH_SAHM_THRESHOLD_AGREED` settings. <!-- id: 12 -->
  - [x] Implement dynamic property resolvers in `MacroEconomicDTO` and `risk_gate.py`. <!-- id: 13 -->
  - [x] Integrate HMM bear regime trend biasing into `technical_options_engine.py`. <!-- id: 14 -->

- [x] **Area 4: Observability, Diagnostics Engine & Audit CLI** <!-- id: 15 -->
  - [x] Implement causal expanding-window walk-forward evaluator in `validation/regime_diagnostics.py`. <!-- id: 16 -->
  - [x] Build CLI `scripts/audit_regime_model.py` supporting Markdown tables, volatility monotonicity gate, and `--json` export. <!-- id: 17 -->
  - [x] Wire `hmm_regime_state` label telemetry into `state_snapshot.json` across orchestrators. <!-- id: 18 -->

- [x] **Area 5: Documentation & Quality Assurance** <!-- id: 19 -->
  - [x] Create comprehensive guidebook `docs/regime_model_tuning_guide.md`. <!-- id: 20 -->
  - [x] Update `docs/architecture/signal-engines.md`, `CLAUDE.md`, and `AGENTS.md`. <!-- id: 21 -->
  - [x] Regenerate settings census and liveness artifacts. <!-- id: 22 -->
  - [x] Complete multi-agent audit (6 Builder + 6 Troubleshooting & Audit agents). <!-- id: 23 -->
  - [x] Verify all 208 regression and targeted tests pass with 0 failures. <!-- id: 24 -->
