# Task tracker: synthetic-data gate covers keyless and injected-Mock cycles

Plan: `.claude/synthetic_data_gate_keyless_implementation_plan.md` (approved 2026-11-13).
Branch: `synthetic-data-gate-keyless`. Walkthrough: `.claude/synthetic_data_gate_keyless_walkthrough.md`.

Operator answers (2026-11-13): Q1 gate the writers, keep the cycle computing; Q2 the plan's
recommendation (inner writers as a follow-up PR); Q3 add the deduplicated CRITICAL alert with test
isolation; Q4 keep the run `succeeded`, surface the skip through existing fields and logs; Q5 operator
checks the live daemon log (mentioned in the PR, not blocking).

## Work packages

- [x] Re-verify every plan citation against `origin/main` @ `d8e53dfc`; fix the stale ones in the plan.
- [x] WP1 core
  - [x] `data_engine.is_synthetic_provider(engine)`; MockDataEngine docstring corrected.
  - [x] `pipeline/synthetic_gate.py` (flag/reason keys, `mark_cycle_synthetic`, deduped alert, progress message).
  - [x] `AsyncDataFetchStep`: flag from the engine identity; fallback keeps reason `empty_price_fetch`;
        freshness marker only when not synthetic.
  - [x] `AgenticQueueStep` reason reworded; `BrokerExecutionStep` comment; `StateSnapshotStep` early return.
  - [x] `main_orchestrator`: daily-summary warning; synthetic progress message (Q4).
  - [x] `desktop/daemon_runtime.py`: start-up ERROR when the engine is synthetic and the queue mode is on.
- [x] WP2 verify helper + MCP
  - [x] `scripts/step5_4_verify_cycle.py`: synthetic checked before the snapshot (exit 4), honest disclosure.
  - [x] `investyo_mcp_server.py::generate_html_report`: synthetic named as a not-refreshed cause and
        detected from the run's progress message; old "cannot detect" caution removed.
- [x] WP3 test infra
  - [x] `tests/_synthetic_outputs.py` (`allow_synthetic_outputs`) + `synthetic_outputs_allowed` marker fixture.
  - [x] `conftest.py::_stub_synthetic_cycle_alert_in_tests` (+ `real_synthetic_cycle_alert` opt-out).
  - [x] `pytest.ini` markers; `tests/test_orchestrator_e2e.py` opts in.
- [x] Tests: `tests/test_synthetic_data_gate_keyless.py` (T1-T8, T11, T12, alert, daemon start log);
      T9 in `tests/test_step5_4_verify_cycle.py`; T10 in `tests/test_investyo_mcp_server.py` and
      `tests/test_daemon_agentic_queue_primary.py`; fallback test in
      `tests/test_production_steps_broker_gate.py` now uses a non-Mock engine.
- [x] Mutation checks (by hand, restored from a scratch copy, never `git stash`).
- [x] WP4 docs: CLAUDE.md/AGENTS.md rule, RUNBOOK §3.15, known-issues write-up + README row,
      FEATURE_TIER_HISTORY, architecture (execution, orchestration-entrypoints, testing,
      webapp-and-gui), HOW_TO_GUIDE, robinhood-execution skill (both mirrors), `/verify` command.
- [x] WP5 regenerate `docs/settings_field_census.{json,md}` and `docs/settings_liveness.json`.
- [x] Gates: targeted files, `make lint`, `make ci`, `make local-ci ARGS="--profile quick"`.
- [ ] Draft PR opened (not merged).

## Follow-ups (not in this PR)

- Gate the inner `RunPipelineStep` writers: always-attached `ForecastTracker` ledger,
  `dead_letter.json` rewrite, symbol-rating and cap-audit rows (plan Q2).
- Operator: confirm the live daemon start log says `data_engine=DataEngine` (plan Q5).
