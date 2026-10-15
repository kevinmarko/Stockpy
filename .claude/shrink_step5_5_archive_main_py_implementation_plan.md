# Step 5.5: archive `main.py` to `legacy/`

## Context
Shrink step 5 retires `main.py`. Since the 2026-10-12 cutover the orchestrator daemon
(`DAEMON_AGENTIC_QUEUE_MODE=primary`) writes the execution queue, watch state and pushes, and
5.4a/5.4b-i/5.4b-ii (#1117, #1121, #1123) moved every runtime caller off `main.py`. 5.5 moves `main.py`
and the modules only it uses into `legacy/`, leaving one runtime. Parent plan:
`.claude/shrink_step5_retire_main_py_implementation_plan.md` §4 (5.5). Operator decisions that apply:
4 (archive the `--agent` features; `is_automatic_run_gated` → `shared/`), 5 (retire `daily_report.html`).

**Merge timing (operator, 2026-10-12):** build and verify now, open the PR, **don't merge before the
daemon has run `primary` cleanly through 2026-10-16**. 5.5 removes the RUNBOOK §3.15 rollback (the
`com.investyo.daily-advisory` plist runs `main.py`); after it, rollback = `git revert` of this PR.

No runtime behavior of the daemon changes: nothing on its path imports `main` (verified). The only
daemon-path edits are import paths for the three market-hours helpers.

## What moves to `legacy/` (exclusive to `main.py`, verified by import graph)
- `main.py` → `legacy/main.py`
- `pipeline/steps.py` (Account/Universe/KillSwitchGate/Macro/Precompute/AdvisoryEval steps)
- `reporting/html_publisher.py` (`daily_report.html`, decision 5)
- `engine/trade_signals.py` (conviction momentum / price triggers, decision 4)
- The agent half of `engine/advisory_agent.py` (AgentState, load/save_agent_state, backlog/reminders,
  process_run_result, compute_next_run_delay, CONFIG) → `legacy/engine/advisory_agent.py`
- `write_state_snapshot` / `load_snapshot_diff` from `reporting/state_snapshot.py` (only caller is
  `html_publisher`) → `legacy/reporting/state_snapshot.py`
- `scripts/com.investyo.daily-advisory.plist` → `legacy/scripts/` (rollback retired with `main.py`)

## What stays (daemon uses it)
- **New `shared/market_hours.py`** (decision 4): `is_automatic_run_gated`, `is_us_market_open_now`,
  `is_extended_hours`, moved verbatim with their constants. Importers updated:
  `desktop/daemon_runtime.py:56`, `main_orchestrator.py:591`, `pipeline/production_steps.py:782`,
  `shared/engine_status.py:101`, `scripts/preflight_check.py:860`. `engine/advisory_agent.py` is
  deleted from the active tree (no shim: nothing else imports it).
- `reporting/state_snapshot.py` trimmed to the helpers `main_orchestrator.py:96-100` imports
  (`_safe_float_or_none`, `_rating_consecutive_cycles`, `_rating_is_excluded`); drop its
  `TYPE_CHECKING` import of `main.RunResult`.
- `pipeline/advisory_inputs.py`, `pipeline/agentic_queue.py`, `execution/compose.py`, `watch_engine`,
  `engine/advisory.py`: unchanged.

## Callers and references to fix
- `scripts/install_schedule.command`: remove `--with-daily-advisory` and its guard (the job it installed
  is gone); unknown-arg handling stays.
- `Gravity AI Review Suite.py`: step 28 (~3384, imports `main.run_once`/`_build_universe`/…) retargeted to
  `pipeline.advisory_inputs` + `AdvisoryOverlayStep`; steps 78/79 (~12121/12392) retargeted to
  `shared.market_hours` / retired for `trade_signals`; source-read checks at ~4065, 7146-7155, 7670,
  8493, 9246, 12366, 12572, 13054 pointed at the daemon files or removed with a comment.
- `.github/CODEOWNERS:24`; `scripts/compare_shadow_queue.py` docstring (only meaningful in shadow);
  comments in `scripts/preflight_check.py`, `scripts/_bootstrap.py`, `scripts/generate_shell_completion.py`.
- `settings.py` `DAEMON_AGENTIC_QUEUE_MODE` description (2435-2444) + `.env.example:150-157`: `off` now
  means **no queue writer at all**. Default stays `off` (default-value policy: no silent behavior change
  on `git pull`; the live value is `primary` in the runtime-flags store). Regenerate census/liveness.
- `.agents/skills/robinhood-execution/SKILL.md` (stale copy still says "re-run `python3 main.py`") and
  `.agents/skills/alert-rule-authoring/SKILL.md`: brought in line with the `.claude/` versions. _(If
  #1123 hasn't merged when implementation starts, this goes onto #1123 instead.)_
- `pilots/agentic.py` reads `agent_state.json`, which nothing writes any more: it already shows
  "no state" live; leave the reader, note it in the walkthrough.

## Tests (39 files touch the targets)
- **Move to `legacy/tests/`** (main.py-only behavior): `test_main.py`, `test_run_once.py` (except the
  two `advisory_inputs` classes, retargeted), `test_main_report_real_macro.py`,
  `test_run_once_advisory_golden.py` (after extracting `_install_frozen_inputs` into a shared fixture the
  three daemon tests import, patching `pipeline.advisory_inputs` seams), `test_reporting_package.py`,
  `test_trade_signals.py`, `test_advisory_inputs_patch_seams.py`, `test_state_snapshot_advisory.py`;
  the main-only classes inside `test_pipeline_package.py`, `test_alert_dispatch_llm.py`,
  `test_advisory_agent.py` (agent classes), `test_daemon_agentic_queue_primary.py`
  (`TestGatePrimaryEqualsMainPy`, `TestMainPyPrimarySkip`), `test_daemon_advisory_shadow_equivalence.py`
  (the one `_run_main` test).
- **Retarget to the daemon path**: `test_universe_retention.py`, `test_main_multifactor_precompute.py`,
  `test_xsec_momentum_advisory_parity.py` (3-way → 2-way), `test_production_steps_universe.py`
  (`TestDaemonMatchesMainUniverse` → vs `advisory_inputs.build_universe`), `test_symbol_rating_wiring.py`,
  `test_progress_emission.py`, `test_pipeline_smoke.py`, `test_advisory_pause_gate.py`,
  `test_watch_alerts.py` (source-greps → `pipeline/agentic_queue.py::run_watch_engine`),
  `test_daemon_advisory_reuse_diff.py`; market-hours tests from `test_advisory_agent.py` →
  `tests/test_shared_market_hours.py`; patch targets `engine.advisory_agent.*` → `shared.market_hours.*`
  in `test_preflight.py`, `test_engine_status.py`, `test_main_orchestrator.py`,
  `test_execute_broker_orders.py`, `test_symbol_rating_wiring.py`, `test_daemon_runtime.py`.
- **Lists**: drop `main.py` from `test_env_loading.py:42`, `test_cnn_lstm_import_order.py:29-33`,
  `test_no_missing_call_timeouts.py:53`, `test_garch_extraction_equivalence.py:100`,
  `test_options_archive_import_smoke.py:95`. `test_state_snapshot_parity.py` becomes a single-writer
  schema pin (orchestrator writer only). Drop moved files from `.test_durations.json`.
- **New gate** `tests/test_main_py_archive_import_smoke.py`, cloned from
  `tests/test_options_archive_import_smoke.py`: block `main`, `pipeline.steps`,
  `reporting.html_publisher`, `engine.trade_signals`, `engine.advisory_agent` in `sys.modules` and
  import every entry point (`main_orchestrator`, `desktop.orchestrator_daemon`, `api.*`,
  `investyo_mcp_server`, `scripts.*` with `NO_VENV_REEXEC=1`); assert the archived paths are not
  importable.

## Docs (same PR)
`legacy/README.md` (every move + why); CLAUDE.md/AGENTS.md (status bullet → 5.5 done, Commands block
loses `python3 main.py`, the descriptive mentions at ~143/150/208/243/265, Archived subsystems bullet);
RUNBOOK (§3.15 rollback = revert this PR; §3.1 `--refresh-account` → Settings → Brokers → Refresh /
`POST /brokerage/refresh`; ~1160/1196/1306-1498 run instructions → `POST /run`; §5.1 drops
daily-advisory); HOW_TO_GUIDE (317, 356, 784, 1478, universe section 166-193); `docs/architecture/`
`orchestration-entrypoints.md`, `execution-boundary.md`, `testing.md`, `simulation-eval-reporting.md`
and the other files' passing mentions; `docs/FEATURE_TIER_HISTORY.md`; step 5 plan status.

## How the work is split
I do the core moves (`git mv`, `shared/market_hours.py`, trimming `reporting/state_snapshot.py`,
importers, settings text, the new import gate). Then in parallel: a `test-writer` subagent for the test
moves/retargets, a general agent for Gravity, a general agent for docs. Branch `step5-5-archive-main-py`.
Plan/task/walkthrough saved as `.claude/shrink_step5_5_archive_main_py_*.md`.

## Verification
1. `git grep -nE "\bimport main\b|from main import|pipeline\.steps|html_publisher|trade_signals|engine\.advisory_agent"`
   outside `legacy/` and history docs → empty; the new import-smoke gate passes.
2. Targeted: every touched test file, plus `test_daemon_runtime.py`, `test_main_orchestrator.py`,
   `test_production_steps*.py`, `test_state_snapshot_parity.py`, `test_agent_docs_sync.py`,
   census/liveness.
3. `make ci` zero failures; `make lint` (ruff) clean.
4. `make verify` step 3 (`scripts/step5_4_verify_cycle.py`, one isolated dry-run daemon cycle) still exits
   as before (4 on mock data in the sandbox).
5. Gravity run (`python "Gravity AI Review Suite.py"`) no new failures vs `main`.
6. `npm run --prefix webapp typecheck` only if a webapp file changes (none planned).
7. After 10-16 and before merge: confirm the live daemon is healthy (`GET /status`, last runs succeeded,
   queue fresh) — the soak condition.
