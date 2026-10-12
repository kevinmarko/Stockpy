# Step 5.4: switch the callers off `main.py` (two PRs)

## Context

`main.py` is being retired (shrink step 5, plan `.claude/shrink_step5_retire_main_py_implementation_plan.md`).
Steps 5.0–5.3 are merged: the daemon (`desktop/daemon_runtime.py` + `main_orchestrator._main_body`) builds the
universe, runs the advisory overlay, and in `DAEMON_AGENTIC_QUEUE_MODE=primary` writes the real
`execution_queue.json`, runs the watch engine and sends the pushes. The operator confirmed the live system **is cut
over** (primary, `com.investyo.stack` installed).

What is left is everything that still *shells out to* `main.py` or tells the operator to run it. 5.5 (archive
`main.py` to `legacy/`) cannot happen until none of these remain, otherwise they fail silently (stale queue,
Console button / `make verify` / `/dead-letter/retry` / MCP report all die with only a log line).

Branch note: work is on the harness-designated branch `claude/ecstatic-maxwell-f6ruct` (== `origin/main` @ `2f53931`).
Two sequential draft PRs from it; after 5.4a merges, restart the branch from `origin/main` for 5.4b. Tier is
"Everything else" (execution/orchestrators), so no direct commits to `main`, and PRs wait for the operator before merge.

Feature freeze (until 30 closed `main_pipeline` paper trades) allows this: it is removal/rewiring of existing
behaviour, no new capability. No new settings flags.

## Operator decisions (this session + step-5 plan)

- Live is **primary**: delete the daily-advisory launchd plist; rewrite RUNBOOK rollback text.
- `/dead-letter/retry`: **repoint to a daemon cycle** (keep endpoint + `DEAD_LETTER_RETRY_ENABLED`).
- `launch.command`: **delete**.
- **Two PRs** (5.4a runtime, 5.4b shell/docs).
- From step-5 plan: Console `ADVISORY` → daemon trigger; MCP `generate_html_report` → trigger a daemon cycle;
  `daily_report.html` retired (use `daily_report_dashboard.html`); `--agent` features archived (5.5).

## Step 0 — environment (prerequisite, no repo change)

The container has no `.venv` / `sqlalchemy` / `node_modules`, so `scripts/feature_freeze_status.py` and pytest cannot
run. Run `./setup.sh` (creates `.venv`, Python 3.12 via uv, installs `requirements.txt`) and `npm install --prefix webapp`.
All verification below depends on this. Establish a green baseline for the targeted tests before editing.

## PR 5.4a — runtime callers (Python API/runner, MCP, webapp, manifest)

1. **`shared/orchestrator_runner.py`**
   - Delete `launch_advisory_main` (389-447), `launch_scheduled_advisory` (450-544), `launch_symbol_retry`
     (1466-1532) and now-unused log-path constants (`ADVISORY_LOG_PATH`, `SCHEDULED_LOG_PATH`, `RETRY_LOG_PATH`) once
     grep shows no other reader (tests' fixtures patch them — update those).
   - Remove `HIGH_STAKES_COMMANDS["main.py"]` (1116-1118) and its comment (1106-1110).
   - Fix the `--refresh-account` bug: drop the `cmd.append("--refresh-account")` at 356-357 (`main_orchestrator.py`
     argparse has only `--dry-run/--strict`, so the subprocess fallback exits 2). Remove/reject the `refresh_account`
     job param for `JobType.ORCHESTRATOR`; point callers to `POST /brokerage/refresh`. Fix the wrong docstring (295-298).
   - Add `launch_daemon_cycle()` (daemon-only, **no subprocess fallback**): `daemon_client.trigger_run()`; on
     409 `already_running` attach to `existing_run_id`; on `kill_switch_active`/`unavailable`/`command_disabled`/
     `unauthorized` raise `RuntimeError` with an honest message. Returns the existing daemon-backed `RunHandle`
     (`backend="daemon"`, `daemon_run_id`). Reuse the poll/handle machinery at 216-248; no new job framework.
   - Leave `launch_daemon_engine` (no callers) and `launch_verify` alone.
2. **`api/_jobs.py:240-243`**: `JobType.ADVISORY` → `launch_daemon_cycle()`. Daemon handles are already
   non-cancellable (`_jobs.py:91-99`). Known, disclosed limits (same as the existing orchestrator fast path):
   SSE log stream is empty for daemon-backed jobs (daemon never writes `gui_run.log`); `get_run_status` needs
   `STATE_API_TOKEN` if set, so verify live tokens; single-flight is per job type, daemon-side 409 is attached to.
3. **`POST /dead-letter/retry`** (`api/pilots_api.py:5423-5469`): replace `launch_symbol_retry` with
   `daemon_client.trigger_run()`, reusing `_TRIGGER_ERROR_STATUS` (`pilots_api.py:3528`) like `/automation/run`.
   Response → `{symbol, run_id, applies, note}`; note states honestly that the whole universe is re-run (per-symbol
   retry was never really per-symbol — `WATCHLIST` env merged with held/discovered — and a daemon cycle is what
   rewrites `dead_letter.json`). Keep `DEAD_LETTER_RETRY_ENABLED` and its guard. Follow the `pilots-endpoint` skill
   (auth tier, "applies" contract). Webapp: `types.ts` (`DeadLetterRetryResult` 3627-3633), `client.ts`,
   `mock.ts` (`retryDeadLetter` 11538), `PipelineDashboard.tsx` (480, 530) + its test (270-345: no more PID /
   `gui_retry.log`).
4. **MCP `generate_html_report`** (`investyo_mcp_server.py:1074-1129`): remove the `main.py` subprocess and the primary
   early-return; trigger a daemon cycle via `shared.daemon_client` (attach on 409), poll `get_run_status` (15 s, same
   deadline pattern as `.claude/skills/robinhood-execution/SKILL.md:82-98`), then require
   `daily_report_dashboard.html` mtime ≥ the run's `started_at` (else report "cycle finished but report not
   refreshed" — never re-report a stale file as fresh). Docstring discloses that a cycle is a full pipeline run
   (writes snapshot/queue, paper orders if enabled). Fix hints at 2184, 2195, 4091. Tests: `test_investyo_mcp_server.py:2148-2170`,
   `test_daemon_agentic_queue_primary.py:581-596`.
5. **Command manifest / completions**: remove `main.py` from `cli_introspect/targets.py:27`; regenerate with
   `python scripts/build_command_manifest.py` then `python scripts/generate_shell_completion.py`.
   `webapp/src/commandParse.ts:59` (`main.py` high-stakes rule) and `mock.ts` (manifest entry 7296-7339, confirm branch
   11304-11313, `cancellable` expressions 11367/11377/11416 → include `advisory`). Tests pinned to the manifest:
   `test_command_manifest_freshness`, `test_shell_completion::TestCommittedArtifactIsFresh`,
   `test_pilots_commands.py:114`, `Commands.test.tsx` (14 `findByText("main.py")`, refresh-account confirm test
   454-490 → retarget to a remaining command, delete the refresh-account case), `commandParse.test.ts`.
6. **Webapp**: `Console.tsx:42` description ("Run main.py cycle" → full daemon cycle; label "Advisory Pipeline" stays,
   `Console.test.tsx` pins it); hint strings `SettingsBrokers.tsx:196`, `TradeHistory.tsx:57` (+ `TradeHistory.test.tsx:79`),
   `helpContent.ts:379` → "Settings → Brokers → Refresh". Keep `client.ts`/`mock.ts` in parity.
7. **Operator-facing hint strings (Python)**: `data/robinhood_portfolio.py:608,624`, `data/robinhood_orders.py:458`,
   `pilots/observability.py:316`, `scripts/preflight_check.py:592,893,918,1102-1105,2020-2023`, `shared/help_content.py:786`
   (delete the `--agent` mention; no daemon equivalent), `setup.sh:91`, and the stale `settings.py:2412-2435`
   ("primary reserved for 5.3"), `settings.py:2653`, `settings.py:4665`/`settings_keysets.py:408`,
   `pilots/run_status.py:93-96`. Replacement action: Settings→Brokers Refresh / `POST /brokerage/refresh` (or
   `ROBINHOOD_SCHEDULED_LOGIN_ENABLED`) for logins; `POST /run` / `python -m desktop.orchestrator_daemon` /
   `python3 main_orchestrator.py` for cycles. Mirror any `settings.py` description change into `webapp/src/api/mock.ts`
   (2870, 4329, 9543) and regenerate `docs/settings_field_census.*` / `settings_liveness.json`.
8. **Tests to update** (caller changes only; the ~25 files that `import main` are 5.5): `test_orchestrator_runner.py`
   (176-196 delete; fixtures at 45), `test_orchestrator_runner_daemon_cutover.py` (add `launch_daemon_cycle` cases:
   ok, attach on 409, kill-switch, daemon-down — no subprocess), `test_ai_control_center.py:489-556` (delete),
   `test_command_execution.py` (390-412, 594-614), `test_pilots_dead_letter.py`, `test_control_api.py` (add the
   missing `JobType.ADVISORY` through `/jobs` test), `test_investyo_mcp_server.py`, `test_robinhood_e2e.py:849-880`.
9. **`Gravity AI Review Suite.py`**: adjust/remove the checks for the deleted functions (5539-5650, 6390-6548,
   13929-14085). The `main.py`-source reads (7140… ) stay for 5.5.

## PR 5.4b — shell, schedulers, skills, docs

1. **`make verify` / `verify.command`**: replace `import main; main.run_once()` with a new task-scoped helper
   `scripts/step5_4_verify_cycle.py` (not a generic name), used by both: load `.env`, `alerting.setup_logging()`
   **before** importing `main_orchestrator` (its import runs `logging.basicConfig`; `setup_logging` returns early if
   handlers exist), then `asyncio.run(main_orchestrator._main_body(True, strict=False))`, then print a summary from
   `state_snapshot.json` + `dead_letter.json` via `scripts.daily_briefing.generate_briefing(settings.OUTPUT_DIR)`
   (its default dir is `<repo>/output`, wrong for `LOCAL_DATA_ROOT`). **Safety, found in this survey:**
   `AgenticQueueStep` ignores dry-run, so in primary a verify cycle would write the real queue, watch state and send
   pushes. The helper forces `settings.DAEMON_AGENTIC_QUEUE_MODE = "off"` in-process and runs dry-run (no broker
   orders; `force_account` is the dry-run flag in this path). Make the `Makefile` `&&`-chain so a failed cycle fails
   `verify` (today "verify complete" echoes unconditionally); add `_live_run` to `.PHONY`. Disclose: the cycle still
   rewrites `state_snapshot.json`/dashboards/`dead_letter.json` (run_once did not). Tests: helper forces off mode,
   dry-run, ordering of logging vs import, non-zero exit on `PipelineFatalError`.
2. **Delete** `launch.command`, `scripts/com.investyo.daily-advisory.plist`. `scripts/install_schedule.command`: drop the
   label (44-48) and text (8, 30-31, 45, 134, 139). Keep the unload block in `scripts/install_stack_service.command`
   (69-76; cleans old installs), fix its text (17-19, 75, 138-141). Stale comments in `investyo_stack_service.sh`,
   monthly-retrain/weekly-edgar plists. `README.md:164-167,265`, `.github/CODEOWNERS:24`, `ci.yml:7` comment.
3. **Skills**: `robinhood-execution` (`.claude` + `.agents`: always `trigger_run`/poll; non-primary mode → stop and tell
   the operator the daemon isn't the queue writer; update `test_robinhood_e2e.py:849-880` phrase list in the same PR),
   `rh-execute.md`, `agentic-discovery` (22, 150), `.agents/skills/incident-triage` (104, 113: `POST /run` returns
   423 `kill_switch_active` when the switch is on, so the verification step changes), `.agents/skills/alert-rule-authoring`
   (32-52: daemon `send_run_summary_push` now also notifies). `.claude/commands/verify.md:28-31`.
4. **Docs**: `docs/RUNBOOK.md` (§3.15 rollback 874-875, §5.1 953-987, §3.15 step list), `CLAUDE.md` + `AGENTS.md`
   (mirror; mark 5.4 done, drop `launch.command` and `main.py` commands, keep "being retired" until 5.5; run the
   `sync_agent_docs` hook / `test_agent_docs_sync`), `docs/architecture/{orchestration-entrypoints,webapp-and-gui,
   execution}.md`, `docs/HOW_TO_GUIDE.md`, `docs/FEATURE_TIER_HISTORY.md` (one history line).
5. **PR artifacts** (unique names, per CLAUDE.md rule 6): `.claude/shrink_step5_4a_callers_runtime_{implementation_plan,task,walkthrough}.md`
   and `..._5_4b_callers_shell_docs_...`.

## Execution: 6 parallel agents per PR (requested by the operator)

I do Step 0 (environment + green baseline) and the final integration myself; the six agents run **concurrently in the
one working tree with strictly disjoint file ownership** (two agents editing the same file can silently lose edits, so
no file has two owners). Rules given to every agent: edit only owned files and report (don't edit) anything else it
finds; never run git commands that change state (no commit/stash/checkout — I commit; `git stash` is banned here);
never touch `.env`; run its owned tests with `pytest tests/test_<x>.py` and paste the pass/fail output; report back the
diff summary, test results and anything uncertain. Agents are `general-purpose` (inherit the session model — CLAUDE.md:
don't default multi-file refactors to a cheap tier), except tests that are purely mechanical may use `test-writer`.

**Shared contracts fixed up front** (so agents don't have to wait on each other):
- `shared/orchestrator_runner.launch_daemon_cycle() -> RunHandle` (daemon-only; raises `RuntimeError(<honest message>)`
  on kill-switch / daemon down / token problems; attaches to `existing_run_id` on 409).
- Dead-letter retry response: `{symbol: str, run_id: str | None, applies: "immediately", note: str}` (no `pid`,
  no `log_path`); error mapping = `_TRIGGER_ERROR_STATUS`.
- One canonical wording for replaced hints — login: *"Open Settings → Brokers and press Refresh (approve the device
  prompt on your phone), or enable ROBINHOOD_SCHEDULED_LOGIN_ENABLED for the daemon's weekday-morning login."*;
  cycle: *"Trigger a cycle with POST /run or the Pipeline Dashboard's 'Run full advisory pipeline' button (daemon:
  python -m desktop.orchestrator_daemon)."*

**5.4a — six agents**
| # | Owns (exclusive) | Task |
|---|---|---|
| 1 | `shared/orchestrator_runner.py`, `api/_jobs.py`, `Gravity AI Review Suite.py` (5539-5650, 6390-6548, 13929-14085 only), `tests/test_orchestrator_runner*.py`, `test_ai_control_center.py`, `test_command_execution.py`, `test_control_api.py` | items 1, 2, 9 and their tests, incl. new `launch_daemon_cycle` + `JobType.ADVISORY` tests |
| 2 | `api/pilots_api.py` (dead-letter endpoint only), `tests/test_pilots_dead_letter.py` | item 3 backend |
| 3 | `investyo_mcp_server.py`, `tests/test_investyo_mcp_server.py`, `tests/test_daemon_agentic_queue_primary.py` (581-596 only) | item 4 |
| 4 | everything under `webapp/src/**` (Console, PipelineDashboard, types, client, mock, commandParse, SettingsBrokers, TradeHistory, helpContent + vitest) | items 3 (webapp half), 5 (webapp half), 6; `npm run typecheck` + `npm test`; later mirrors agent 6's settings descriptions into `mock.ts` |
| 5 | `cli_introspect/targets.py`, `cli_introspect/command_manifest.json`, `completions/*`, `tests/test_pilots_commands.py`, `test_command_manifest_freshness.py`, `test_shell_completion.py` | item 5 (Python half): remove target, regenerate manifest + completions via the scripts |
| 6 | `data/robinhood_portfolio.py`, `data/robinhood_orders.py`, `pilots/observability.py`, `pilots/run_status.py`, `scripts/preflight_check.py`, `shared/help_content.py`, `setup.sh`, `settings.py`, `settings_keysets.py`, `docs/settings_field_census.*`, `docs/settings_liveness.json`, tests pinned to those strings | item 7; reports the final description strings to me for agent 4's `mock.ts` sync |

After they report: I run `make ci`, ruff, typecheck, the browser pass and the daemon smoke, resolve any cross-file
fallout, then run `api-parity-reviewer` and `honesty-auditor` (read-only) on the diff before opening the draft PR.

**5.4b — six agents**
| # | Owns (exclusive) | Task |
|---|---|---|
| 1 | `scripts/step5_4_verify_cycle.py` (new), `Makefile`, `verify.command`, its new test file | item 1 |
| 2 | `launch.command` (delete), `scripts/com.investyo.daily-advisory.plist` (delete), `scripts/install_schedule.command`, `scripts/install_stack_service.command`, `scripts/investyo_stack_service.sh`, the retrain/edgar plists, `README.md`, `.github/CODEOWNERS`, `.github/workflows/ci.yml` (comment) | item 2 |
| 3 | `.claude/skills/**`, `.agents/skills/**`, `.claude/commands/**`, `tests/test_robinhood_e2e.py` | item 3 (keep `.claude`/`.agents` copies identical) |
| 4 | `docs/RUNBOOK.md`, `docs/HOW_TO_GUIDE.md`, `docs/architecture/*.md` | item 4 (RUNBOOK rollback + §3.15/§5.1) |
| 5 | `docs/FEATURE_TIER_HISTORY.md`, `.claude/shrink_step5_4{a,b}_*` artifacts, `docs/known_issues/*` touched by 5.4 | item 5; drafts history line + walkthroughs from the other agents' reports |
| 6 | `CLAUDE.md` + `AGENTS.md` (mirror via the sync hook; `test_agent_docs_sync`) | last, after 1-5 report; facts only (5.4 done, `launch.command` gone, commands list) |

Sequencing: 5.4a runs first and merges (operator go-ahead) → branch restarted from `origin/main` → 5.4b. 5.4b agent 1
needs a working `.venv` (Step 0) because it executes a real dry-run cycle against `MockDataEngine`.

## Out of scope (stays for 5.5/5.6)

Archiving `main.py`, `pipeline/steps.py`, `html_publisher`, `engine/trade_signals.py`; the ~25 tests that `import main`
or read its source; `("main.py", 68)` timeout allowlist; `is_automatic_run_gated` move; `launch_daemon_engine`
cleanup; `ORCHESTRATOR_DAEMON_ENABLED` retirement (note: `launch_webapp.command:497-512` starts the daemon only when
that flag is on, and `POST /jobs` is only daemon-hosted then — 5.4a's `launch_daemon_cycle` returns a clear
"daemon not running" error instead of falling back).

## Verification (every claim is run, not assumed)

- Baseline then after: `pytest` targeted files above, then `make ci`; `ruff check . --select=F821,F822,F823,E9`.
- Regenerated artifacts re-checked: `test_command_manifest_freshness`, `test_shell_completion`, settings-census tests,
  `test_agent_docs_sync`, `test_state_snapshot_parity`.
- Webapp: `npm run --prefix webapp typecheck`, `npm --prefix webapp test`; `/verify-webapp` browser pass on Console
  (Advisory Pipeline job: starts, shows daemon-backed status, not cancellable), Pipeline Dashboard (dead-letter retry),
  Commands, Settings→Brokers, Trade History — console clean, visually confirmed.
- Live-ish smoke: start `python -m desktop.orchestrator_daemon` in mock mode (no FRED key ⇒ MockDataEngine); create the
  Console `advisory` job and `POST /dead-letter/retry`; confirm run ids/status flow and the 409-attach / daemon-down
  messages. A real primary cycle (queue write, paper orders) cannot be exercised here (no keys, synthetic-data gate
  skips queue + broker) — the operator's §3.15 live smoke test covers it; I will say so rather than claim it.
- `grep -rn "main\.py"` outside `legacy/`, `docs/`, `.claude/`, `tests/`: only intended hits (5.5-scoped tests,
  the file itself, comments about the retirement).
- Draft PR per sub-step; `subscribe_pr_activity` after creating each.

## Risks flagged for review

- Console "Advisory Pipeline", MCP report and dead-letter retry now run a **full daemon cycle** (broker step places paper
  orders if `ADVISORY_ONLY=false`; in primary also writes the queue/pushes). Previously `main.py` was advisory-only.
  Button/tool descriptions say so.
- Daemon-backed jobs: empty SSE log, no cancel, possible `STATE_API_TOKEN` vs `ORCHESTRATOR_DAEMON_TOKEN` mismatch
  (would show an immediately-terminal `unknown` job). Checked against the live tokens by the operator before relying on it.
- Deleting the plist removes the documented rollback path; RUNBOOK is rewritten to "set mode `off`, run `main.py` by hand
  until 5.5 archives it".
