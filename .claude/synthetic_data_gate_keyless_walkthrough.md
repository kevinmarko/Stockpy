# Walkthrough: synthetic-data gate covers keyless and injected-Mock cycles

Plan: `.claude/synthetic_data_gate_keyless_implementation_plan.md` (approved 2026-11-13).
Tracker: `.claude/synthetic_data_gate_keyless_task.md`. Root-cause write-up:
`docs/known_issues/synthetic_data_gate_keyless_cycle_2026_10.md`.

## What was wrong

`ctx.context_extras["data_is_synthetic"]` was set only on `AsyncDataFetchStep`'s empty-fetch
fallback. The daemon builds its engine once (`OrchestratorDaemon._build_data_engine`) and injects it,
returning `MockDataEngine` when there is no `FRED_API_KEY` or the FRED check fails. Such a cycle was
never flagged, so in `primary` it wrote the real queue, advisory source, watch state and pushes, and in
every mode it rewrote the state snapshot, the dashboards and the freshness marker from fabricated data.

## What changed

| File | Change |
|---|---|
| `data_engine.py` | `is_synthetic_provider(engine)` = `isinstance(engine, MockDataEngine)`; the misleading `fetch_technical_raw_cached` docstring (claimed `fetch_all_data_async` falls back to Mock) corrected. |
| `pipeline/synthetic_gate.py` (new) | Stdlib-only leaf: flag/reason keys, `REASON_MOCK_ENGINE` / `REASON_EMPTY_PRICE_FETCH`, `is_cycle_synthetic`, `synthetic_reason`, `mark_cycle_synthetic` (idempotent, one CRITICAL log, one alert), `send_synthetic_cycle_alert` (`send_alert("CRITICAL", ..., dedup_key="synthetic_data_cycle")`, never raises, looked up at call time so the verify helper's suppression covers it), `synthetic_progress_message`. |
| `pipeline/production_steps.py` | `AsyncDataFetchStep` flags the cycle from the engine it actually uses, right after resolving it (standalone keyless, daemon-injected, any injected engine; all modes). The fallback flags with `empty_price_fetch` (first reason wins). `_mark_data_refreshed()` only when not synthetic. `AgenticQueueStep` reason text "ran on synthetic MockDataEngine data"; `BrokerExecutionStep` comment; `StateSnapshotStep` returns first when flagged (no plotly, snapshot, `history/`, report or payload print). |
| `main_orchestrator.py` | `_build_daily_summary` adds a `SYNTHETIC DATA:` warning; `_main_body_impl` sets the run's progress message to `synthetic_progress_message(...)` after the runner (Q4). All edits below line 118. |
| `desktop/daemon_runtime.py` | `start()` logs an ERROR when its engine is synthetic and `DAEMON_AGENTIC_QUEUE_MODE` is not `off`. |
| `scripts/step5_4_verify_cycle.py` | Synthetic checked before the snapshot (exit 4, unless the cycle stopped: the stop keeps its exit-3 explanation); disclosure no longer says the snapshot was overwritten and names what is still not gated. |
| `investyo_mcp_server.py` | `generate_html_report`: synthetic data is a listed not-refreshed cause; a run whose progress message starts `SYNTHETIC DATA` is reported as not refreshed by name; the old "cannot detect" caution is gone. |
| `conftest.py`, `pytest.ini`, `tests/_synthetic_outputs.py` (new) | `allow_synthetic_outputs()` context manager + `synthetic_outputs_allowed` marker (marker-keyed fixture, never autouse-on); `_stub_synthetic_cycle_alert_in_tests` (opt out: `real_synthetic_cycle_alert`). |
| `tests/test_synthetic_data_gate_keyless.py` (new) | T1-T8, T11, T12, alert, daemon start log (28 tests). |
| `tests/test_orchestrator_e2e.py` | Module fixture opts in (`allow_synthetic_outputs()`), with a comment. |
| `tests/test_step5_4_verify_cycle.py` | T9: synthetic exits 4 with and without a snapshot; stopped synthetic keeps exit 3. |
| `tests/test_investyo_mcp_server.py`, `tests/test_daemon_agentic_queue_primary.py` | T10: text pins flipped; new synthetic-progress tests. |
| `tests/test_production_steps_broker_gate.py` | The fallback test now uses a non-Mock engine, so it still exercises the fallback (with a keyless `None` engine the identity check would flag first). |
| Docs | CLAUDE.md + AGENTS.md (one rule bullet, byte mirrors), RUNBOOK §3.15, known-issues write-up + README row, FEATURE_TIER_HISTORY, `docs/architecture/{execution,orchestration-entrypoints,testing,webapp-and-gui}.md`, HOW_TO_GUIDE (dry-run note, mock mode, troubleshooting), robinhood-execution skill (`.claude` + `.agents`), `/verify` command. |
| `docs/settings_field_census.{json,md}`, `docs/settings_liveness.json` | Regenerated with the scripts' `--write` (line shifts in the verify script and MCP server; one new `getattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", ...)` site in the daemon). |

## Operator decisions applied

- Q1 gate the writers: the cycle still computes; only durable/outward writes are skipped.
- Q2 inner writers (forecast ledger, `dead_letter.json`, symbol rating, cap audit): follow-up PR, as the
  plan recommended (the tracker-attach change touches forecasting coverage tests).
- Q3 the deduplicated CRITICAL alert is sent; the suite-wide stub keeps tests from alerting.
- Q4 the run stays `succeeded`; the reason is in `RunRecord.progress["message"]` (existing field,
  served by `GET /run/{id}/status`), the logs and the daily summary.
- Q5 left to the operator (below).

## Deviations from the plan

- The flag/alert/progress helpers live in a new leaf module `pipeline/synthetic_gate.py` instead of a
  private `_mark_cycle_synthetic` in `production_steps.py`, so `main_orchestrator`, the MCP server and
  the tests share one set of constants without importing the heavy steps module.
- Q4 visibility uses the run's progress message (an existing field) rather than only logs, and the MCP
  report tool reads it, so it now names a synthetic run instead of just listing it as a possible cause.
- T4 lives in the new test file; the existing `TestMarkerSetByAsyncDataFetchStep` test was changed to
  a non-Mock engine instead of extended, because after the fix its keyless setup no longer reached the
  fallback at all.
- The opt-in helper is `tests/_synthetic_outputs.py` (importable) rather than a function inside
  `conftest.py`; the marker fixture in `conftest.py` uses it.

## Mutation checks (run by hand, restored from a scratch copy; never `git stash`)

| Mutation | Result |
|---|---|
| `is_synthetic_provider` always returns False (the pre-fix behaviour) | 11 failed, incl. both T7 Mock-engine cycle tests (queue written, pushes sent) |
| `mark_cycle_synthetic(ctx, REASON_MOCK_ENGINE)` removed from `AsyncDataFetchStep` | 6 failed, incl. both T7 Mock-engine tests and T1/T2 |
| `is_synthetic_provider` always returns True (over-block) | 6 failed, incl. T3 and the real-engine T7 control |
| `StateSnapshotStep` early `return` removed | 3 failed (T5 and both T7 Mock-engine tests) |
| verify helper: synthetic check disabled | 2 failed (the gated T9 cases fall to exit 3) |

All restored; the suite is green on the final code.

## Verification

All run in this session on the final code (2026-11-13):

- Targeted: 27 files (the new one, every test file that reaches `AsyncDataFetchStep` /
  `StateSnapshotStep` / `_main_body`, plus `test_measure_settings_census`, `test_settings_liveness`,
  `test_agent_docs_sync`, `test_help_content`, `test_runtime_flags`): all passed after the
  census/liveness regeneration (before it, exactly the 3 freshness checks failed, as the plan predicted).
- `make lint`: `All checks passed!`
- `OMP_THREAD_LIMIT=1 make ci`: `12048 passed, 28 skipped, 531 warnings in 698.65s`.
  A first `make ci` without `OMP_THREAD_LIMIT` crawled on this 4-CPU sandbox (LightGBM thread
  oversubscription; `tests/test_lgbm_ranker_native_cv.py` alone did not finish in 600 s) and was cut
  off by my own 50-minute timeout at 41 %. The same LightGBM tests pass with `OMP_THREAD_LIMIT=1`
  (which `scripts/local_ci.py`'s offline step also sets) and touch no file in this change.
- `make local-ci ARGS="--profile quick"`: lint, webapp-typecheck, webapp-test, webapp-build, bandit
  PASS; ml-audit / ml-lookahead SKIPPED (no watched path changed).

## Left for the operator

- **Q5:** confirm the live daemon's start log says `OrchestratorDaemon started: engines warm,
  data_engine=DataEngine`. `MockDataEngine` there means every cycle is now skipped (and was writing
  fabricated output before this fix). After setting `FRED_API_KEY`, restart the daemon.
- A deployment that was keyless will now show an aging snapshot and dashboards instead of fresh
  fabricated ones; the reason is in the logs, the alert, the daily summary and the run's progress
  message, not on the snapshot screens themselves (plan risk R3).
- Follow-up PR: gate the inner `RunPipelineStep` writers (plan Q2).
