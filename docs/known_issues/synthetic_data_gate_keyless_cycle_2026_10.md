# Synthetic-data gate missed keyless and injected-Mock cycles (2026-10)

**Status: fixed in code (2026-11-13, PR `synthetic-data-gate-keyless`); not confirmed against the
live daemon.** Found 2026-10-12 during step 5.4 (noted in `docs/FEATURE_TIER_HISTORY.md`, "Callers
switched off `main.py`"), planned 2026-10-29
(`.claude/synthetic_data_gate_keyless_implementation_plan.md`), approved 2026-11-13.

## Symptom

`settings.py` (the `DAEMON_AGENTIC_QUEUE_MODE` description) already promised: "In every mode a cycle
on synthetic (MockDataEngine) data ... writes nothing". The code did not keep that promise for the
most likely way a cycle ends up on synthetic data: a daemon with no `FRED_API_KEY`. In `primary` such
a cycle would write the real `execution_queue.json`, `queue_sources/advisory.json` and
`watch_state.json`, send the summary, watch and new-intent pushes, rewrite `state_snapshot.json`
(+ `history/`) and both HTML dashboards, and stamp `last_data_refresh.txt`, all on fabricated inputs.
`docs/RUNBOOK.md` §3.15 listed it as a known, unfixed gap ("keep the key set").

## Root cause

1. **The flag was set in one place only.** `ctx.context_extras["data_is_synthetic"]` was set only
   inside `AsyncDataFetchStep`'s empty-fetch fallback (the price fetch came back empty, so the step
   swapped in a `MockDataEngine`). `MockDataEngine` never returns empty bars, so a cycle that was on
   Mock from the start never reached that branch.
2. **The daemon picks its engine elsewhere.** `OrchestratorDaemon.start()` calls
   `_build_data_engine()` once and injects the engine through
   `main_orchestrator._main_body(..., data_engine=...)`. With `ctx.market` already set,
   `AsyncDataFetchStep` skipped its own (keyless) Mock branch entirely, so a fix there alone would have
   left the daemon exposed.
3. **A second Mock site with a key set.** `_build_data_engine()` also returns `MockDataEngine` when
   `ensure_fred_configured()` / `DataEngine(...)` raises, so "keyless" was not the only trigger.
4. **Only two consumers read the flag.** `AgenticQueueStep` and `BrokerExecutionStep` honored it;
   `StateSnapshotStep` and the freshness marker did not.
5. **Mixed provenance.** The advisory overlay reads bars from `data.market_data.get_provider()`, not
   `ctx.market`, so a keyless queue could carry real-looking prices on Mock macro presets and (with
   `ADVISORY_REUSE_PIPELINE_COMPUTE`) Mock-derived GARCH/forecasts. Real-looking numbers on partly
   fabricated inputs are worse than a uniform $10.
6. **The freshness marker lied.** The keyless branch fell into the real-data `else` and stamped
   `last_data_refresh.txt`, so the next interval cycle could be skipped as "fresh".

A partial mitigation existed by accident: the Mock macro preset has no `VIXCLS`, so the macro kill
switch is forced on; whether that blocks queue intents depends on `MACRO_REGIME_GATE_ENABLED`. It
never covered `advisory.json`, `watch_state.json`, pushes or the snapshot.

## Fix

- `data_engine.is_synthetic_provider(engine)`: the one decision, `isinstance(engine,
  MockDataEngine)` (positive identification, so test doubles stay "real").
- `AsyncDataFetchStep` calls it on the engine the cycle actually uses, right after the engine is
  resolved, so one call covers the standalone keyless branch, the daemon-injected engine (keyless or
  after a failed FRED check) and any injected engine, in all three modes. The empty-fetch fallback
  keeps setting the same flag (reason `empty_price_fetch`; the engine case is `mock_engine`).
- `pipeline/synthetic_gate.py` holds the flag/reason keys and `mark_cycle_synthetic()`: one CRITICAL
  log line and one deduplicated CRITICAL alert (`send_alert("CRITICAL", ...,
  dedup_key="synthetic_data_cycle")`) per cycle.
- Consumers: `AgenticQueueStep` (all modes: no summary push, watch engine, `advisory.json`, queue or
  shadow copy), `BrokerExecutionStep` (no paper order), `StateSnapshotStep` (new: no snapshot,
  `history/`, dashboards or payload print) and the freshness marker (new: not stamped).
- The run stays `succeeded` (the `GET /status` contract is frozen); `_main_body_impl` puts a message
  starting `SYNTHETIC DATA` into the run's progress, which reaches `RunRecord.progress["message"]` and
  `GET /run/{id}/status`. `_build_daily_summary` adds a warning line. The daemon logs an ERROR at start
  when its engine is synthetic and the queue mode is not `off`.
- `scripts/step5_4_verify_cycle.py` checks for synthetic data before the snapshot (exit 4, not 3) and
  no longer claims the snapshot was overwritten. The MCP `generate_html_report` tool reports a
  synthetic run as "report not refreshed" and names it from the progress message.
- Tests: `tests/test_synthetic_data_gate_keyless.py`; Mock-cycle tests that need outputs opt in via
  `tests/_synthetic_outputs.py`. The alert is stubbed suite-wide by
  `conftest.py::_stub_synthetic_cycle_alert_in_tests`.

## Not fixed here (follow-up)

The inner durable writers that run inside `RunPipelineStep` are not gated yet: the always-attached
`ForecastTracker` ledger (`main_orchestrator.run_pipeline`), the `dead_letter.json` rewrite including
its "cleared" branch (`ForecastingStep`), and the `SYMBOL_RATING_ENABLED` / `SIZING_CAP_AUDIT_ENABLED`
writers. A keyless cycle against the live DB can still add $10 forecast rows (cleanup:
`scripts/clean_forecast_ledger.py`, rule (d)) and clear a real `dead_letter.json`. The plan
recommended a separate PR because the tracker-attach change touches forecasting coverage tests.

## Still unverified

- Whether the live daemon has `FRED_API_KEY` set (so whether anything was ever exposed). The operator
  check: the daemon start log line `OrchestratorDaemon started: engines warm, data_engine=...` should
  say `DataEngine`, and no `SYNTHETIC DATA` lines should appear.
- That every snapshot reader (Pilots API, retrospective, webapp) renders a stale-but-real snapshot
  sensibly during a long keyless period; none of them gets a new shape.
- A future engine that fabricates data but is not a `MockDataEngine` would not be caught;
  `tests/test_synthetic_data_gate_keyless.py` classifies every data provider by AST so a new one
  fails the suite until it is classified.
