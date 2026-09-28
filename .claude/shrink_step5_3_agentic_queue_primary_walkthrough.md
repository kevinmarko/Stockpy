# Step 5.3 walkthrough: the daemon as the primary agentic-queue writer

Plan: `.claude/shrink_step5_retire_main_py_implementation_plan.md` (section 4 "5.3", section 5 "Traps", decisions 3 and 5).
Branch: `step5-agentic-queue-primary`, cut from `origin/main` @ `49bd42de` (5.0-5.2 plus the Robinhood scheduled-login PRs #1082/#1083).

**Code only.** The live system keeps `DAEMON_AGENTIC_QUEUE_MODE=shadow` for the 5-trading-day comparison. Nothing here changes the live `.env`, `runtime_flags.json` or anything under `~/.stockpy_local`. With the mode at `off` or `shadow`, main.py, the queue files, the pushes and `state_snapshot.json` behave exactly as before (every new kwarg defaults to the old behaviour; the new snapshot keys are written only in primary).

## What primary does

`pipeline/production_steps.py::AgenticQueueStep` with `DAEMON_AGENTIC_QUEUE_MODE=primary`, in main.py's `_run_cycle` order:

1. **Summary push** (`pipeline.agentic_queue.send_run_summary_push`): logs `alerting.summarize_run(...)` over the cycle's recommendations and per-symbol errors, then the high-priority "Errors Detected" push when there are errors, else "Refresh Complete". main.py sent the clean push once per *launch*, and its launchd job launched once per weekday; the daemon launches rarely and cycles hourly, so the clean push goes out at most once per US/Eastern day (kept in memory; a same-day daemon restart can send a second). The error push goes out every cycle with errors, as main.py's `--interval` mode did.
2. **Watch engine** (`pipeline.agentic_queue.run_watch_engine`): the same `load_watch_rules(settings.WATCH_RULES_FILE)` → `load_watch_state(OUTPUT_DIR/watch_state.json)` → `evaluate_watch_rules` → `dispatch_watch_alerts(dashboard_url=NTFY_DASHBOARD_URL)` → always `save_watch_state` sequence. `WATCH_RULES_FILE` is relative (`watch_rules.yaml`); the stack service `cd`s to the repo root, so it resolves as it did for main.py.
3. **Real queue**: `write_advisory_source(ctx.recommendations, output_dir=OUTPUT_DIR, now=…)` then `compose_and_emit(ctx.snapshot, output_dir=OUTPUT_DIR, mode=<ROBINHOOD_EXECUTION_MODE captured at step start>, now=…, macro_dto=ctx.macro_dto)` with side effects ON: the new-intent ntfy push and `execution_queue_notified.json`, risk-gate alerts, `risk_gate_blocks.jsonl`. One `now` for both files.

Skip rules:
- stopped cycle / synthetic data / advisory overlay unfinished → the whole step is skipped (no queue, no push, no watch), logged. The previous queue stays.
- no recommendations → summary push (it then carries the per-symbol errors) and watch engine run, the queue write is skipped (logged). **Disclosed difference:** main.py would write an *empty* `advisory.json` here; the daemon leaves the old one. Either way `compose_and_emit` would not have replaced the queue.
- `compose_and_emit`'s "nothing composable / stale or corrupt source / no positive equity → leave the old `execution_queue.json`" is unchanged (`test_nothing_composable_leaves_the_old_queue`). The skill's ~30-minute `generated_at` rule is what stops an old queue being placed.
- **Kill-switch pause, disclosed:** main.py still ran its summary push and watch engine on a paused cycle (with zero recommendations, which also resets watch edge state). The daemon's `AsyncPipelineRunner` skips every step after the kill-switch stop, so a paused daemon cycle pushes nothing and leaves `watch_state.json` alone.

The mode actually used is stored in `ctx.context_extras["agentic_queue_mode"]`; `StateSnapshotStep` reads that, not the setting, so one cycle cannot mix modes if runtime flags hot-reload mid-cycle.

## Double-writer prevention

`main.py::_run_cycle` calls `pipeline.agentic_queue.daemon_owns_agentic_side_effects(settings.DAEMON_AGENTIC_QUEUE_MODE)` right after `run_once()` + the summary log line. In primary it logs a WARNING (count of recommendations it computed, "Trigger a daemon cycle (POST /run)") and returns, skipping the summary push, watch engine, queue write and `_write_html_report` (so no `daily_report.html` and no advisory `state_snapshot.json` competing with the daemon's). `pipeline/agentic_queue.py` is stdlib-only at import, so main.py pays nothing for it. The edit is below line 68, so `tests/test_no_missing_call_timeouts.py`'s `("main.py", 68)` allowlist entry is unchanged.

Consequence to note: in primary, if the daemon is not running, nobody writes the queue. The runbook cutover installs `com.investyo.stack` (KeepAlive) first.

## Run ownership (the cycle-time trap)

`AsyncPipelineRunner` bounds a sync step with `asyncio.wait_for(asyncio.to_thread(...))`. A timeout cancels the await, not the thread.

- `AgenticQueueStep.run` claims a token with `claim_queue_writer(ctx.context_extras)` (stored under `agentic_queue_owner_token`). A newer claim supersedes older tokens.
- `main_orchestrator._main_body_impl` wraps `runner.run(...)` in `try/finally: close_cycle_queue_writer(ctx.context_extras)`, which, under the ownership lock, marks the cycle closed and releases its token. A claim for an already-closed cycle is refused (the case of a worker thread that only starts after its step timed out).
- Every real file commit goes through `commit_guard(token)`: the lock is held while it checks ownership and does the final `os.replace`. That covers `queue_sources/advisory.json` (`write_source`), `execution_queue.json` (`emit_execution_queue`, which also checks before building so a dead run fires no risk-gate alert) and `watch_state.json` (`run_watch_engine`). Refused commits delete their temp file and return `None`.
- Pushes (summary, watch alerts) check `is_current_queue_writer` first. **Residual, disclosed:** that check is not atomic with the push, and a risk-gate alert fired inside a build that was still owned when the build started can land after the cycle ended. A push cannot be un-sent; the queue file can no longer be written late.
- The step also releases its token in its own `finally` on a normal return.

`commit_guard` is a new optional kwarg on `write_source`, `write_advisory_source`, `compose_and_emit` and `emit_execution_queue`; `None` (all existing callers, main.py, shadow) is byte-identical to before.

## State snapshot

`main_orchestrator._write_state_snapshot(..., recommendations=None)`. `StateSnapshotStep` passes the cycle's recommendations only when `AgenticQueueStep` ran in primary. Then each signal also gets:
- `garch_vol` = `_safe_float_or_none(rec.key_indicators["garch_vol"])` (the advisory writer's source);
- `suggested_exit_pct` = `rec.suggested_exit_pct`;
- both `null` (never 0.0) for a symbol with no recommendation this cycle.

In off/shadow neither key is written, so the file is unchanged. Tests: `tests/test_state_snapshot_parity.py::TestPrimaryModeAdvisoryFields` (values equal the advisory writer's, null when absent, absent without recommendations) and `tests/test_daemon_agentic_queue_primary.py::TestStateSnapshotStepPassesRecommendationsOnlyInPrimary`.

## `daily_report.html` (decision 5: retired, not ported)

- `StateSnapshotStep` keeps writing `daily_report_dashboard.html`, `volatility_bands_dashboard.html` and `state_snapshot.json` every cycle, so the webapp's Report Library and every snapshot reader keep working with only the daemon running.
- `pilots/reports.py` lists `daily_report.html` only if the file exists; an old one on disk stays listed with its old timestamp.
- MCP `generate_html_report` shelled out to main.py and reported `daily_report.html`. In primary it would have reported a stale file as fresh, so it now returns a pointer to `daily_report_dashboard.html` / `POST /run` without running main.py (`TestMcpReportUnderPrimary`). The existing off-mode test is unchanged. 5.4 reworks this tool.
- Main.py's `_write_html_report` also wrote the advisory `state_snapshot.json`; skipping it in primary removes the two-writer alternation in `state_snapshot.json`/`history/` the plan's traps section describes.

## Skill

`.claude/skills/robinhood-execution/SKILL.md` and `.agents/skills/robinhood-execution/SKILL.md` (identical apart from the `.agents` port comment), plus `.claude/commands/rh-execute.md`:
- Prerequisites step 3 reads `DAEMON_AGENTIC_QUEUE_MODE`. In `primary`: run the embedded snippet (`shared.daemon_client.trigger_run()`, attach to the in-flight run on 409 `already_running`, poll `get_run_status` every 15 s until `succeeded`/`failed`, 45-minute cap), with plain handling for `kill_switch_active`, `network_error`/`unavailable` (daemon down), `unauthorized`/`command_disabled` (token), and a failed run. A succeeded run does not guarantee a new queue, so the freshness rule still applies. In `off`/`shadow`: the old `python3 main.py` guidance.
- The stale-queue hard stop keeps the ~30-minute rule and names both refresh paths.
- Pinned by `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned::test_skill_md_refreshes_the_queue_through_the_daemon_in_primary`. The snippet was extracted and `py_compile`d.

## Gate

`tests/test_daemon_agentic_queue_primary.py::TestGatePrimaryEqualsMainPy`, on the run_once golden's frozen inputs (`_install_frozen_inputs`), time frozen at `_NOW` in `execution.compose`, `execution.queue_builder` and `watch_engine`, a real watch-rules file (conviction ≥ 0.80 and action_change) and a seeded prior `watch_state.json` (INTC was BUY, JNJ HOLD):

1. `test_primary_real_files_and_side_effects_equal_main_py` — the REAL `main.main()` single-run with mode `off` into OUTPUT_DIR A (not a copy of its queue block), then the daemon's `AsyncDataFetchStep` → `AdvisoryOverlayStep` → `AgenticQueueStep` with mode `primary` into OUTPUT_DIR B. **PASS:** `queue_sources/advisory.json`, `execution_queue.json`, `execution_queue_notified.json` and `watch_state.json` are byte-identical (A's source and queue also equal the committed goldens); per-symbol recommendation bytes equal; the pushes are equal (summary push compared minus its first line, which carries wall-clock start and duration): Refresh Complete, conviction alerts for AAPL/KO/NVDA/XOM, action-change alerts INTC BUY→SELL and JNJ HOLD→BUY, and the new-intent push. No `shadow/` directory in primary.
2. `test_side_effects_fire_once_when_both_writers_run` — both writers with mode `primary` against one OUTPUT_DIR C: main.py pushes nothing, changes none of the four files and calls no report; the daemon then writes all four identical to step 1's and sends exactly one summary push and one new-intent push.
3. `test_watch_state_diff_is_empty` — the plan's `watch_state.json` diff on its own: equal state dicts and equal watch alerts (≥ 3).

Other coverage in the same file: primary writes with side effects; clean push once per ET day; error push every cycle and no queue with no recommendations; the three whole-cycle skip reasons write and push nothing; nothing composable leaves the old queue; the mode is captured and recorded and the token released; ownership (late claim refused, newer claim supersedes, a source commit refused without ownership, `_main_body_impl` closes the cycle when the runner raises); **the timed-out step**: a real `AsyncPipelineRunner` with `PIPELINE_STEP_TIMEOUT_SECONDS=0.3`, the step blocked inside the queue build, the cycle closed in a `finally` as `_main_body_impl` does, the thread resumed at 1.0 s → the old queue is untouched and no new-intent push was sent; main.py's skip parametrized over off/shadow/primary.

**Mutation checks** (temporary edits, reverted):

| Temporary edit | Result |
|---|---|
| `emit_execution_queue` ignores `commit_guard` at the final rename | the timed-out-step test fails |
| main.py's primary check disabled | the "fire once" gate and the main.py skip test fail |

5.2's tests: `test_primary_behaves_like_shadow_and_warns` replaced by `test_shadow_does_not_touch_the_real_queue`; the symlink-refusal test now runs shadow only (primary writes the real queue by design); the `write_queue` kwargs assertion includes `owner_token=None`.

## Verification

- `tests/test_daemon_agentic_queue_primary.py`: 24 passed. With `test_daemon_advisory_shadow_equivalence.py`, `test_state_snapshot_parity.py`, `test_investyo_mcp_server.py`, `test_main_report_real_macro.py`, `test_compose_advisory_only_golden.py`, `test_watch_alerts.py`, `test_robinhood_e2e.py`: all pass.
- Full offline suite, `LOCAL_DATA_ROOT=<scratch> NO_VENV_REEXEC=1 pytest -m "not network and not slow" -n auto --dist loadgroup -q -p no:randomly`: **11743 passed, 18 skipped, 0 failed** (run twice, before and after the doc edits).
- `ruff check . --select=F821,F822,F823,E9`: clean.
- Regenerated `docs/settings_field_census.{json,md}` and `docs/settings_liveness.json` (`DAEMON_AGENTIC_QUEUE_MODE` read-site count and `investyo_mcp_server.py` line numbers moved; no classification change).
- No webapp change.
- Not run: a live daemon cycle in primary (the live mode stays shadow; the runbook's §3.15 step 5 is the operator's live smoke test).

## Uncertain / follow-ups

- The clean "Refresh Complete" push cadence (once per ET day) is a judgement call; main.py's was "once per launch".
- The daemon's macro DTO is still its own `MacroStep`'s (plan section 1 "Macro"); the gate holds macro equal by construction, as 5.2 did. Gate (iii) is where a live macro difference shows.
- Daemon cycles run hourly, so in primary the watch engine and error push run hourly instead of once each weekday morning. Edge-triggered watch rules don't repeat, but an `action_change` that flips back and forth intraday now alerts each time.
- 5.4 still has to repoint the callers that shell out to main.py (Console Advisory job, `launch.command`, `make verify`, MCP).
