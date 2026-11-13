# Task: synthetic-data gate for the inner writers

Branch `synthetic-data-gate-inner-writers`. Approved by the operator on 2026-11-13 as the follow-up
(plan Q2) of `.claude/synthetic_data_gate_keyless_implementation_plan.md`. Bug fix, allowed under the
feature freeze. Walkthrough: `.claude/synthetic_data_gate_inner_writers_walkthrough.md`.

## Steps

- [x] Audit every durable/outward write reachable from `RunPipelineStep` (Macro, TrendVolatility,
      Processing, Forecasting, StrategyEval) and `AdvisoryOverlayStep`; classify each as gate or keep
      (inventory table in the walkthrough)
- [x] Hand the flag to the inner context: `run_pipeline(data_is_synthetic=, synthetic_reason=)` +
      `synthetic_gate.propagate_synthetic_flag` (no second log or alert)
- [x] Forecast ledger + model cache: `generate_forecast(persist=)`,
      `run_prophet_forecast(persist=)`, `run_cnn_lstm_forecast(persist=)`; `ForecastingStep` passes it
- [x] Macro: `compute_hmm_risk_on_probability(persist=)` (no `macro_history` top-up, HMM fitted on a
      deep copy); `MacroStep` passes it
- [x] `StrategyEvalStep`: PIT snapshot, `dead_letter.json`, cap audit, symbol rating (+ cadence
      check), sizing-cap alert
- [x] Advisory: `evaluate(persist_forecast=)`; `AdvisoryOverlayStep` passes it
- [x] `pipeline/synthetic_gate.py` docstring + `SKIPPED_OUTPUTS` (log/alert text) updated
- [x] Test isolation: `_isolate_cap_audit_db_in_tests`, `_isolate_pit_feature_store_in_tests`
      (both stores leaked into the shared `LOCAL_DATA_ROOT` from the suite)
- [x] Tests: `tests/test_synthetic_data_gate_inner_writers.py` (full Mock `_main_body` cycle, real
      engine control, dashboard equality, unit tests per seam)
- [x] Mutation check: 17/17 killed (CNN-LSTM save gate not reachable without TensorFlow; disclosed)
- [x] Widen test fakes to accept the new keywords; keep the symbol-rating AST guard's `if` shape
- [x] Regenerate the settings census artifacts
- [x] Docs: known-issue doc (follow-up marked fixed), CLAUDE.md + AGENTS.md bullet and isolation
      list, `docs/architecture/{orchestration-entrypoints,signal-engines,testing}.md`
- [x] Verify: targeted tests, `make lint`, `OMP_THREAD_LIMIT=1 make ci` (results in the PR)
- [x] Commit, push, open a DRAFT PR (not merged)
