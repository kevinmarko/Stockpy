# Implementation plan: make the synthetic-data gate cover keyless and injected-Mock cycles

Status: DRAFT for operator review. No code written. Date 2026-10-29, base `origin/main` @ `6e79c997`.
Tier: "Everything else" (pipeline / safety gate), so branch `synthetic-data-gate-keyless` + PR after this plan is approved.
Feature freeze: allowed (bug fix, safety, tests, docs). No new settings flag (section 2.6).
Everything below was established by reading the code unless it says "ran". I ran no pipeline and no test.

## 1. Verified current behaviour

### 1.1 Corrections to the problem statement
- **C1. In the daemon the Mock engine is chosen in `OrchestratorDaemon.start()`, not in `AsyncDataFetchStep`.**
  `desktop/daemon_runtime.py:458` calls `_build_data_engine()` (`:555-571`) once; `_run_one_cycle` passes it to
  `main_orchestrator._main_body(..., data_engine=...)` (`:636-645`), which sets `ctx.market = data_engine`
  (`main_orchestrator.py:1429`). `AsyncDataFetchStep` then sees `ctx.market` already set and takes the `else`
  at `pipeline/production_steps.py:194-195` (full `base_symbols` universe). The Mock-picking branch
  `:184-192` is dead code for the daemon; it runs only for a standalone `python3 main_orchestrator.py` / `make verify`
  (`data_engine=None`), where it also shrinks the universe to `["AAPL"] + held` (`:191-192`).
  Any fix that only touches `:184-192` would leave the daemon exposed.
- **C2. There is a second Mock-without-flag site inside the daemon:** `daemon_runtime.py:563-568` returns
  `MockDataEngine()` when `ensure_fred_configured()` / `DataEngine(...)` raises even though a key IS set.
  So "keyless" is not the only trigger.
- **C3. Provenance of a keyless queue is mixed, not purely flat-$10.** Mock bars (flat 10.0, `data_engine.py:530,569-582`)
  feed `dashboard_df`, the broker step and the snapshot. The advisory overlay that feeds the queue reads bars from
  `data.market_data.get_provider()` (`production_steps.py:2900`), not `ctx.market`, so it uses whatever FMP/yfinance
  returns. It still takes `ctx.macro_dto` (built on Mock macro presets, `:309-338`) and, when
  `ADVISORY_REUSE_PIPELINE_COMPUTE` is on (RUNBOOK 3.15 implies it is on live; value not verifiable here), the
  Mock-derived GARCH/forecast (`:2919`). Real-looking numbers on partly fabricated inputs is arguably worse than a
  uniform $10.
- **C4. Incidental partial mitigation:** the Mock macro preset has no `VIXCLS` (`data_engine.py:531-535`), so
  `macro_killswitch_data_unavailable` (`macro_engine.py:75,112`) is True and `MacroEconomicDTO.killSwitch` is forced
  (`dto_models.py:394`). Whether that blocks queue intents depends on `MACRO_REGIME_GATE_ENABLED`
  (`execution/risk_gate.py:455-465`), not verified. It does not touch `advisory.json`, `watch_state.json`, pushes or the snapshot.
- **C5. A second harm the brief did not list:** the keyless branch falls into the real-data `else` at
  `production_steps.py:224-230` and calls `_mark_data_refreshed()`, stamping `last_data_refresh.txt` with Mock data.
  The comment at `:226-229` claims Mock never stamps it. The next interval cycle may then be skipped as "fresh"
  (`main_orchestrator.py:141-156,1396`).

### 1.2 Hop-by-hop path, daemon (the `primary` runtime)
1. `data_engine.py:99-110` `live_data_configured()` = `bool(settings.FRED_API_KEY)`.
2. `daemon_runtime.py:570-571` (or `:563-568`) builds `MockDataEngine`; built once, so a key added later needs a restart.
3. `main_orchestrator.py:1429` -> `production_steps.py:194-195`; `:203` fetch returns non-empty Mock bars for every ticker,
   so the only place the flag is ever set (`:209-223`) is skipped.
4. `MacroStep` / `RunPipelineStep` / `AdvisoryOverlayStep` run on the inputs in C3.
5. `AgenticQueueStep.cycle_skip_reason` (`:3166-3175`) reads the flag (unset), so `_write_primary` (`:3266-3342`)
   does the summary push (`:3283`), watch engine (`:3291`), `advisory.json` (`:3317`) and `compose_and_emit` (`:3325`)
   with side effects. Shadow: `_write_shadow` (`:3225-3264`).
   Whether `execution_queue.json` itself is written also needs `ROBINHOOD_EXECUTION_MODE != off`, a positive-equity
   account snapshot and an intent clearing the 0.85 floor (`:3104-3109`); not verified for this scenario.
6. `BrokerExecutionStep` gate (`:3374`) reads the flag (unset), so it proceeds to `ADVISORY_ONLY` / `_execute_broker_orders` (`:3383-3392`).
7. `StateSnapshotStep` (`:3395-3480`) never reads the flag: plotly chart (`:3411`), `_write_state_snapshot` (`:3427`,
   which also rotates `history/`), `daily_report_dashboard.html` (`:3453`), payload print (`:3478`).
8. `main_orchestrator.py:1473` `_dispatch_daily_summary` runs every cycle (realized P&L + warnings only; no Mock numbers).

Standalone path: same, except hop 2-3 is `production_steps.py:183-193`. The 5.4b-i walkthrough observed `watch_state.json` and
`queue_sources/advisory.json` written this way (`.claude/shrink_step5_4b_i_verify_helper_walkthrough.md:72-76`); I did not re-run it.

Other durable writers a Mock cycle reaches (not gated today, see Q2): the always-attached `ForecastTracker`
(`main_orchestrator.py:359-372`, `forecasting_engine.py:1736`), `dead_letter.json` incl. the "cleared" branch
(`production_steps.py:2469-2490`), `SYMBOL_RATING_ENABLED` / `SIZING_CAP_AUDIT_ENABLED` writers (`:798-892,2675`).

### 1.3 What the flag does today
Set once: `production_steps.py:223`, inside the empty-fetch fallback. Read by `AgenticQueueStep` (`:3171`) and
`BrokerExecutionStep` (`:3374`) only. `settings.py:2358-2359` already documents the intended contract: "In every mode a
cycle on synthetic (MockDataEngine) data ... writes nothing". This plan makes the code match that sentence.

### 1.4 How `scripts/step5_4_verify_cycle.py` decides (reusable predicate)
`_used_synthetic_data(ctx)` (`:344-357`): None -> False; `ctx.context_extras["data_is_synthetic"]` truthy -> True;
else True if any class in `type(ctx.market).__mro__` is named `MockDataEngine`. It reads the `RunContext` captured by the
suppressed `_dispatch_daily_summary` hook (`:279-282`). In `main()` it runs only AFTER `check_snapshot` passes (`:431-459`).
Gap after this plan: with `StateSnapshotStep` skipping, `check_snapshot` fails first, so a synthetic cycle would exit 3
("did not refresh the snapshot") instead of 4, and the disclosure text (`:387-391`, "has OVERWRITTEN state_snapshot.json")
becomes false. The predicate itself is right; the plan reuses its logic (engine identity OR flag) and fixes the ordering.

### 1.5 Live deployment
CLAUDE.md says the live daemon has run `primary` since 2026-10-12; RUNBOOK 3.15 (`docs/RUNBOOK.md:850-857`) says "keep the key set"
and calls the gap known and unfixed. Repo evidence the key is set: `scripts/preflight_check.py:286-302` (`fred_key_configured`) is a
go-live item, but it is a standalone script and I did not verify the daemon runs it at start. This sandbox has no `.env`, so I
cannot and should not inspect the live key. **Not verified: that the live `.env` has `FRED_API_KEY`.** The operator should confirm
with one look at the daemon start log (`OrchestratorDaemon started: engines warm, data_engine=<DataEngine|MockDataEngine>`,
`daemon_runtime.py:462-465`).
Realistic keyless sources besides a cleared live key: any worktree without a `.env` (the `.env` is per-repo via `settings.ENV_PATH`)
running `make verify` or `main_orchestrator.py` while `LOCAL_DATA_ROOT` is machine-global (CLAUDE.md), i.e. it can overwrite the
operator's real `state_snapshot.json` and dashboards. This is inference from those two facts.

## 2. Design

### 2.1 One source of truth, set where the engine is resolved
- Add `data_engine.is_synthetic_provider(engine) -> bool` next to `live_data_configured()`: `isinstance(engine, MockDataEngine)`.
  Positive identification, so test doubles (`MagicMock`, `_FakeDataEngine`) stay "real" exactly as today; a `MagicMock(spec=MockDataEngine)` would count as synthetic.
- In `AsyncDataFetchStep.run`, immediately after `ctx.market` is resolved (after `:196`), call it through the module attribute
  (`data_engine_mod.is_synthetic_provider(de)`, same patch seam style as `live_data_configured`). If true, set
  `ctx.context_extras["data_is_synthetic"] = True` and `["data_is_synthetic_reason"] = "mock_engine"`. One call covers
  daemon-injected, standalone-built and test-injected engines, and all three modes (`data`/`metrics`/`full`).
- The empty-fetch fallback (`:208-223`) keeps setting the same key, reason `"empty_price_fetch"`, via one small helper
  `_mark_cycle_synthetic(ctx, reason)` that also logs. Both paths then log one CRITICAL line per cycle.
- `_mark_data_refreshed()` (`:230`) runs only when the flag is unset.
- Not done in `daemon_runtime.py`: two construction sites plus the exception branch would each need the flag, which is how this gap arose.

### 2.2 Consumers
| Consumer | Today | With the flag set |
|---|---|---|
| `AgenticQueueStep` off | no-op | no-op |
| `AgenticQueueStep` shadow | skips only on fallback | skips (`:3226`): nothing under `shadow/`, no `archive_shadow_run` |
| `AgenticQueueStep` primary | skips only on fallback | skips (`:3269`): no summary push, no watch engine, no `advisory.json`, no queue; previous queue stays (the skill's ~30-minute freshness rule applies) |
| `BrokerExecutionStep` | gate at `:3374` | unchanged; only its comment ("fail-safe branch") is updated |
| `StateSnapshotStep` | ignores flag | **new**: log + return at the top. No plotly file, `state_snapshot.json`, `history/` rotation, `daily_report_dashboard.html` or payload print. Gate lives in the step, NOT in `_write_state_snapshot`, so `tests/test_state_snapshot_parity.py` keeps testing the writer unchanged |
| freshness marker | stamped | **new**: not stamped |
| `_build_daily_summary` (`main_orchestrator.py:1252`) | no mention | **new**: one warning line through the existing `send_daily_summary` call (no new dispatch) |
| MCP `generate_html_report` | says it "cannot detect" synthetic | a synthetic cycle now leaves the dashboard stale, which the tool's existing mtime check reports as "not refreshed"; text updated |
| `step5_4_verify_cycle.py` | see 1.4 | check synthetic first, exit 4, new honest disclosure |
| `AdvisoryOverlayStep` | runs | still runs (network cost only; its output is not consumed). Not gated, to keep the diff small |

### 2.3 Fail-closed semantics
- Missing numbers are not replaced by anything: the snapshot is not rewritten, so readers keep the last REAL snapshot with its own
  `timestamp` aging. No placeholder file, no "synthetic" snapshot (CONSTRAINT #4). Not verified: that every snapshot reader
  (Pilots API, retrospective, webapp) renders a stale-but-real file sensibly; none of them gets a new shape.
- The helper cannot raise (an `isinstance`). If the step cannot decide it behaves as today (cycle never crashes, CONSTRAINT #6).
- The cycle itself still computes (see Q1 for the stop-early alternative).

### 2.4 What the operator sees
- Daemon start: existing WARNING (`daemon_runtime.py:570`); proposal: log at ERROR when `DAEMON_AGENTIC_QUEUE_MODE != off`
  ("queue mode is on but the daemon is on synthetic data; every cycle will skip"). Two lines in `daemon_runtime.py`.
- Every cycle: one CRITICAL line from `_mark_cycle_synthetic`: reason, what is skipped, and "set FRED_API_KEY and restart the daemon".
- Daily-summary warning line (above). No snapshot field: the file is deliberately not written, and a field in a file nobody writes is meaningless.
- Optional (Q3): a deduped `send_alert("CRITICAL", ..., dedup_key="synthetic_data_cycle")`. Not in the base plan because it adds a
  test-isolation hazard (3.3).
- Run record stays `succeeded` (the `GET /status` contract is frozen, `daemon_runtime.py:1745`); see Q4.

### 2.5 Queue-mode interplay
`off`: step is a no-op, the gate still protects snapshot/dashboards/marker. `shadow` and `primary`: skip as in 2.2, before any token side effect
that matters (`claim_queue_writer` at `:3147` is released in the `finally`). The skip reason text keeps the substring "synthetic" (pinned by
`test_cycle_skip_reasons_write_and_push_nothing`) and is reworded to "this cycle ran on synthetic MockDataEngine data".

### 2.6 No new settings flag
The behaviour is already promised by `settings.py:2358-2359`; this is a bug fix. A flag that disables the gate would be a runtime switch
that re-opens the hole, and one defaulting to "today's behaviour" (per the default-value policy) would leave it open by default. The policy
targets new capabilities; here today's behaviour is the defect. Consequence: no `settings.py` edit, no census churn from it, and no runtime rollback lever (section 4).

### 2.7 Out of scope
Partial fabrication inside the real `DataEngine` (the macro layer already has `fabricated_keys`); `AgenticQueueStep` ignoring `dry_run`
(`ctx.force_account` is read only at `:3363`; documented at `HOW_TO_GUIDE.md:304`); a daemon that re-evaluates `live_data_configured()` per cycle.

## 3. Test impact

### 3.1 Why a naive gate trips many tests
`conftest.py:924-941` (`_force_mock_data_engine_in_tests`, autouse) makes `live_data_configured()` False in every test not marked
`live_data_engine` (`pytest.ini:28`), so every test that reaches `AsyncDataFetchStep` with `ctx.market is None` becomes a synthetic cycle.
By reading, only tests asserting on files that `StateSnapshotStep` writes are affected:
- `tests/test_orchestrator_e2e.py`: module fixture `orchestrator_run` (`:106`) runs a real `_main_body` on Mock. 13 of its 16 tests read
  snapshot/history (`TestStateSnapshot` x9), report/chart (`TestReportGeneration` x2) or the printed payload (`TestJsonPayloadExport` x2).
  `TestDeadLetterReport` x2 and `TestBrokerQuarantine` x1 are unaffected in the base plan.
- Expected unaffected (to confirm by running the list in 3.4, not run now): `test_production_steps_universe.py` (presets
  `ctx.market = MagicMock`, `:64`), `test_main_body_engine_injection.py`, `test_pipeline_defatalize.py` and `test_progress_emission.py`
  (crash before the gate), `test_advisory_pause_gate.py` (kill switch), `test_execution_alerts.py` (patches `StateSnapshotStep`),
  `test_state_snapshot_parity.py` (calls `_write_state_snapshot` directly), `test_daemon_runtime.py` / `test_orchestrator_daemon.py` (fakes).

### 3.2 Keeping them honest
- Explicit opt-in only: a conftest context manager `allow_synthetic_outputs()` that patches `data_engine.is_synthetic_provider` to False, plus a
  registered marker `synthetic_outputs_allowed` (pytest.ini, mirroring `live_data_engine`) applied by a NON-autouse-by-default
  fixture keyed on the marker. The module-scoped e2e fixture uses the context manager inside its existing `with (...)` block (a marker cannot
  reach a module-scoped fixture). Each use carries a comment saying why the test needs Mock outputs.
- No autouse that disables the gate (that recreates today's hole inside the suite). Guard test (T12) asserts no autouse fixture patches it.

### 3.3 Isolation hazard to avoid
`conftest.py` has no alert isolation, and `observability/alerts._active_channels` includes webhooks/SMTP when a developer's `.env` sets them
(`alerts.py:164-173`). A new `send_alert` in `AsyncDataFetchStep` would fire for every Mock cycle in the suite. That is why the base plan has no new send;
if Q3 adds one it needs its own seam plus an autouse stub (pattern: `_stub_paper_marking_network_in_tests`).

### 3.4 New tests (file `tests/test_synthetic_data_gate_keyless.py`; each guard has a mutation check, run by hand and reverted via a WIP commit, never `git stash`)
| # | Test | Mutation that must turn it red |
|---|---|---|
| T1 | `ctx.market=None`, keyless -> flag True, reason `mock_engine`, symbols `["AAPL"]+held` unchanged | remove the derivation |
| T2 | `ctx.market=MockDataEngine()` preset (the daemon case) and the engine from `OrchestratorDaemon._build_data_engine` keyless (pattern `test_live_data_gate.py:49-57`) -> flag True | derive only inside the `ctx.market is None` branch |
| T3 | keyed: non-Mock sentinel engine (`_SentinelDataEngine` pattern) -> no flag, `_mark_data_refreshed` called | make the helper return True always (over-block guard) |
| T4 | empty-fetch fallback still sets flag, reason `empty_price_fetch` (extend `TestMarkerSetByAsyncDataFetchStep`) | drop the line |
| T5 | `StateSnapshotStep` with flag: `_write_state_snapshot`, `generate_html_report`, plotly not called; pre-existing files byte-identical; CRITICAL logged. Control: same ctx without flag writes | remove the early return |
| T6 | freshness marker not stamped when flagged; stamped when not (patch `mo._DATA_REFRESH_MARKER`, since it is fixed at import, `main_orchestrator.py:118`) | stamp unconditionally |
| T7 | real steps end to end in `primary` and `shadow` with a Mock engine: `OUTPUT_DIR` stays empty, no push (pattern `_queue_ctx` / `penv` in `test_daemon_agentic_queue_primary.py`) | set the flag False in the step |
| T8 | `_build_daily_summary` carries the warning when flagged, none otherwise | remove the line |
| T9 | verify helper: synthetic cycle with NO fresh snapshot -> exit 4 and "nothing was overwritten"; non-synthetic missing snapshot still exit 3 | check the snapshot before synthetic |
| T10 | MCP `generate_html_report`: synthetic cycle -> "report not refreshed" text names synthetic data as a possible cause | revert the text |
| T11 | AST scan: every `IDataProvider` subclass under `data_engine.py` / pipeline is classified real or synthetic (third implementer `GravityTestEngine`, `Gravity AI Review Suite.py:189`, listed as audit-only; I saw no pipeline import of it) | add an unclassified subclass |
| T12 | conftest guard: no autouse fixture patches `is_synthetic_provider` | add one |

### 3.5 Existing tests to edit (names verified to exist)
- `tests/test_step5_4_verify_cycle.py`: `TestUnverifiedCycles::test_synthetic_data_cycle_exits_4_and_says_it_overwrote_the_snapshot` (text changes),
  `::test_helper_detects_the_flagless_mock_engine_case` (keep), `TestExitCodes::test_no_snapshot_written_exits_3_with_explicit_message` (keep, non-synthetic).
- `tests/test_production_steps_broker_gate.py::TestMarkerSetByAsyncDataFetchStep` (extend, T4).
- `tests/test_daemon_agentic_queue_primary.py`: `TestPrimaryWrites::test_cycle_skip_reasons_write_and_push_nothing` (reworded needle still matches);
  `TestMcpReportUnderPrimary` (`:439-510`, asserts "Synthetic (MockDataEngine) data detected" and `"synthetic" not in text`; both flip).
- `tests/test_investyo_mcp_server.py` `TestGenerateHtmlReport` (`:2255-2310`, `:2680`; pins the "still renders this report ... cannot detect" text).
- `tests/test_live_data_gate.py` (T2 lives next to it), `tests/test_orchestrator_e2e.py` (opt-in), `tests/test_state_snapshot_parity.py` (unchanged by design).
Verification run before PR (targeted, no full suite): the files above plus `test_measure_settings_census`, `test_settings_liveness`,
`test_agent_docs_sync`, `test_help_content`, then `make ci` once.

## 4. Blast radius and rollout
- **Deployment WITH a key:** `live_data_configured()` true -> `DataEngine(...)` (`daemon_runtime.py:559-562`, `production_steps.py:184-187`);
  `DataEngine` and `MockDataEngine` are siblings (`data_engine.py:116,523`), so `is_synthetic_provider` is False, the flag stays unset exactly as today,
  and every new branch (snapshot gate, marker, summary line, verify text) is a no-op. Proven in T3.
  Two honest exceptions: (a) a total market-data outage (the existing fallback) now also stops `StateSnapshotStep` and the marker, so the snapshot
  stays at the last real one instead of being overwritten with $10 data (intended); (b) the `:563-568` exception branch is now gated.
- **Keyless deployment / worktree:** cycles still run and compute, but write no snapshot, dashboards, queue, watch state, pushes or freshness marker. Dev
  "Mock mode" (`HOW_TO_GUIDE.md:306-308`) no longer refreshes local outputs; `make verify` keyless still exits 4 and no longer clobbers real files.
- **Line-number artefacts:** `docs/settings_liveness.json` records `main_orchestrator.py:118`, and the census/liveness files record sites in
  `scripts/step5_4_verify_cycle.py` (census md `:163`, `:271`). Edits to that script change line numbers; regenerate with
  `scripts/measure_settings_census.py --write` and `scripts/settings_liveness.py --write` (never by hand) as the last step (WP5).
  Keep `main_orchestrator.py` edits below line 118. This already turned CI red once (5.4b-i walkthrough, process note).
- **Rollback:** revert the PR. No data migration, no runtime-flags change, no new setting. Operational mitigation, independent of code: keep
  `FRED_API_KEY` set; after setting it, restart the daemon (the engine is built once at `start()`).

## 5. Documentation updates
- `CLAUDE.md` + `AGENTS.md` (byte mirrors, `sync_agent_docs.sh`, `tests/test_agent_docs_sync.py`): add ONE rule bullet under "Honesty and
  resilience": any step that writes durable or outward output must honor `ctx.context_extras["data_is_synthetic"]`, set once in `AsyncDataFetchStep`
  from the engine identity. Nothing else in them changes.
- `docs/RUNBOOK.md` 3.15 (`:850-857`): delete the "NOT covered by that skip ... known gap" caveat; state the new rule and "restart after setting the key".
- `docs/known_issues/synthetic_data_gate_keyless_cycle_2026_10.md` (new, root cause C1-C5, fix, what stayed unverified) + a row in `docs/known_issues/README.md`.
- `docs/FEATURE_TIER_HISTORY.md`: dated entry near the 2026-10 callers entry (`:2229`).
- `docs/architecture/execution.md` (`:53`, primary-writer paragraph: skip semantics), `docs/architecture/orchestration-entrypoints.md` (`:17`, step list),
  `docs/architecture/testing.md` (`:56` verify-cycle description + the new test file), `docs/architecture/webapp-and-gui.md` (`:67`, daemon engine choice).
- `docs/HOW_TO_GUIDE.md` `:304-308` and `:1135-1139` ("the pipeline still runs normally on synthetic data ... fine" -> outputs are not written).
- `.claude/skills/robinhood-execution/SKILL.md:110`, `.agents/skills/robinhood-execution/SKILL.md:118`, `.claude/commands/verify.md:37`.
- Code comments/docstrings to correct: `production_steps.py` (`:216-223`, `:226-229`, `:3098-3099`, `:3365-3373`), `investyo_mcp_server.py` (`:1088-1111`, `:1276-1279`),
  `scripts/step5_4_verify_cycle.py` (`:85-92`, `:347-350`, `:387-391`), `data_engine.py:593` docstring (claims `fetch_all_data_async` falls back to Mock; I found no such code).
- Not edited: `settings.py:2358` (already correct), older dated walkthroughs under `.claude/` (historical records).
- PR artifacts (CLAUDE.md rule 6): `.claude/synthetic_data_gate_keyless_task.md` and `.claude/synthetic_data_gate_keyless_walkthrough.md`.

## 6. Work breakdown (disjoint file ownership; one branch, one PR)
| WP | Owner scope (files) | Depends on | Effort (my estimate, not measured) |
|---|---|---|---|
| WP1 core | `data_engine.py`, `pipeline/production_steps.py`, `main_orchestrator.py` (below line 118), `desktop/daemon_runtime.py` (start-up log), `tests/test_synthetic_data_gate_keyless.py` (T1-T8, T11), `tests/test_production_steps_broker_gate.py` | WP3 helper name | 0.5 day |
| WP2 verify + MCP | `scripts/step5_4_verify_cycle.py`, `investyo_mcp_server.py`, `tests/test_step5_4_verify_cycle.py`, `tests/test_investyo_mcp_server.py`, `tests/test_daemon_agentic_queue_primary.py` (T9, T10) | WP1 reason keys | 0.5 day |
| WP3 test infra | `conftest.py`, `pytest.ini`, `tests/test_orchestrator_e2e.py` (+ any file the targeted run shows red), T12 | none (agree the seam name first) | 0.25 day |
| WP4 docs | everything in section 5 except code comments owned by WP1/WP2 | WP1/WP2 final wording | 0.5 day |
| WP5 regen + gate | census/liveness regeneration, targeted run, `make ci` | WP1-WP4 merged | 0.25 day |
Total about 2 engineer-days. WP1, WP2, WP3 and WP4 can run in parallel once the three names are fixed up front: `is_synthetic_provider`, `data_is_synthetic_reason`
values (`mock_engine`, `empty_price_fetch`), and the `allow_synthetic_outputs` / `synthetic_outputs_allowed` names.

## 7. Risks and open questions for the operator
- **Q1. Run the cycle but gate the writers (recommended) or stop the cycle after the data step?** Stop-early (`ctx.stopped=True`, as the kill switch does at
  `production_steps.py:248`) is one gate instead of N and also covers the inner writers for free, so a future writer cannot forget it. Cost: every Mock cycle in the
  suite computes nothing (`test_main_body_engine_injection`, the e2e and more all need opt-in), keyless `make verify` stops proving the wiring beyond the data step, and
  dev Mock mode becomes a no-op. I recommend gating the writers plus the CLAUDE.md rule, because the stop-early blast radius on tests is much larger.
- **Q2. Inner durable writers** (forecast ledger via the always-attached tracker, `dead_letter.json`, symbol rating, cap audit): include now or in a follow-up PR? A keyless
  cycle on the live DB would write $10 forecast rows (cleanup tool exists: `scripts/clean_forecast_ledger.py`, rule (d)) and could clear a real `dead_letter.json`.
  I recommend a follow-up PR (own tests, tracker-attach change touches forecasting coverage tests I did not enumerate).
- **Q3. Add a deduped `send_alert` when a cycle is skipped for this reason?** Louder for a live deployment that loses its key, but needs the isolation seam in 3.3
  and may spam every cycle until the dedup window passes (`ALERT_DEDUP_WINDOW_SECONDS`). Base plan: log + daily-summary line only.
- **Q4. Should such a run be recorded as something other than `succeeded`?** It would be the loudest signal in the Pipeline screen but changes meaning of the frozen
  status contract and of `make verify`/MCP logic keyed on `succeeded`. Base plan: unchanged.
- **Q5. Please confirm** the live daemon's start log shows `data_engine=DataEngine`; that settles whether anything is live-exposed today.
- **Risk R1.** Positive identification (`isinstance MockDataEngine`) will not cover a future third engine that serves fabricated data; T11 is the guard.
- **Risk R2.** Hidden test coupling: the 13 e2e tests are derived by reading, not by running; the first implementation step is to run the targeted list in 3.5 and extend 3.1.
- **Risk R3.** Skipping the snapshot means a long keyless period shows an aging real snapshot with no on-screen reason (only logs and the summary line); Q3/Q4 are the levers.
- **Risk R4.** Census/liveness regeneration is mandatory after WP2 and easy to forget; it is its own work package for that reason.
