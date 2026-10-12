# Step 5.4b-i walkthrough: a cutover-neutral `make verify`, and honest warnings about `--dry-run` and keyless runs

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md` (PR 5.4b, re-split 2026-10-12). Tracker:
`.claude/shrink_step5_4b_i_verify_helper_task.md`. Stacked on 5.4a (PR #1117).

**Code, scripts and docs only.** Nothing here changes the live `.env`, `runtime_flags.json` or `~/.stockpy_local`.

## Why this is 5.4b-i and not the whole of 5.4b

5.4b was planned, and fully built, on the premise that the live daemon was already cut over to `DAEMON_AGENTIC_QUEUE_MODE=primary`
(that was the operator's answer at the start of the work). While integrating it, `main` moved: #1119 (merged 2026-10-12 10:07Z)
records that the first shadow review "wasn't clean" and that a second 5-trading-day review runs 2026-10-12..10-16, with cutover after
day 5. **The live daemon is still on `shadow`**, and the operator chose to split 5.4b:

- **5.4b-i (this PR, cutover-neutral):** the `make verify` helper, the `--dry-run` / keyless-run warnings, skill and settings text
  fixes that are true in any mode, and a plist that did not parse.
- **5.4b-ii (held until after the review and the cutover):** deleting `launch.command` and `scripts/com.investyo.daily-advisory.plist`,
  the `install_*` script and README changes, the `robinhood-execution` / `rh-execute` rewrite (stop when the mode is not `primary`),
  RUNBOOK 3.15 "DONE" + rollback + 5.1, the CLAUDE.md/AGENTS.md status lines, `.env.example`. The complete, tested change set exists on
  the local branch `step5-4b-ii-cutover-dependent-full` (commit `08f0630`); it must be rebased and every "live is primary" sentence
  re-checked against the real state when the cutover happens. Hunks that landed in 5.4b-i will drop out.

## What changed

**`make verify` / `verify.command` step 3 -> `scripts/step5_4_verify_cycle.py`** (new). The old step ran `import main;
main.run_once()`. The helper runs one `main_orchestrator.main(dry_run=True)` cycle in its own process (it never contacts the daemon)
and then proves it ran. A real pipeline cycle does things `run_once()` never did, so the helper switches them off for its own process
only; every override is restored on exit and the cycle is not run if one cannot be applied:
- `DAEMON_AGENTIC_QUEUE_MODE = "off"`: `AgenticQueueStep` ignores dry-run and, in `primary`, writes the real `execution_queue.json` /
  `watch_state.json` and sends pushes.
- `SYMBOL_RATING_ENABLED = False`, `SIZING_CAP_AUDIT_ENABLED = False`: the daemon records at most one durable rating cycle per trading
  day (possibly from mock data); a verify run must not spend it.
- `main_orchestrator.fetch_account_snapshot` is wrapped to `allow_live_fetch=False`: with `ROBINHOOD_AUTO_REFRESH_ENABLED` on and a
  cache older than 20 h the default call would prompt the operator's phone for device approval, which a headless script must never do.
- `main_orchestrator._dispatch_daily_summary` and `observability.alerts.send_alert` are replaced with logging no-ops (the daily summary
  posts to Slack/email/the alert file; risk-gate alerts can fire even in dry-run). `main_orchestrator._heartbeat` becomes a no-op: the
  daemon never refreshes `heartbeat.txt`, so a file left behind would turn the heartbeat panel from "No heartbeat" to "Stale".
- `setup_logging()` runs before `main_orchestrator` is imported (its import runs `logging.basicConfig`, after which `setup_logging()`
  returns early and the rotating log file is never attached).
- Exit codes: `0` completed and `state_snapshot.json` was refreshed by this run (mtime and the snapshot's own `timestamp`, and the
  end-of-cycle hook was reached so the file cannot be someone else's); `1` any exception / `PipelineFatalError` / `SystemExit`; `3` the
  cycle returned but did not refresh the snapshot (kill switch, stopped early, hook never reached); `4` the cycle ran but proves nothing
  about live data (synthetic `MockDataEngine` data, or a snapshot with no signals). The Makefile recipe is `&&`-chained, so `make
  verify` can no longer print "verify complete" for a cycle that did not run (it echoed unconditionally before), and `verify.command`
  now exits with the helper's status (it always exited 0 before). `verify.command` step 2 also gets `--dist loadgroup`, like the Makefile.

**Warnings that were missing or false.** `--dry-run` stops broker orders only (HOW_TO_GUIDE called it "safe to test - no orders sent";
the RUNBOOK going-live step recommended it with no caveat; `regime-model-tuning` told an agent to run a bare full cycle). The
RUNBOOK 3.15 and `robinhood-execution` claim that "a synthetic-data cycle writes nothing" now says it is only true when the price fetch
comes back empty (see below).

**Text that was wrong in any mode.** `alert-rule-authoring` said `notify()` reads `NTFY_TOPIC` from `os.environ` (it reads
`settings.ALERT_NTFY_TOPIC`) and listed the wrong push senders; `incident-triage` verified a fix by running `main.py` and did not know
`POST /run` answers 423 while the kill switch is on; `agentic-discovery` named `main._load_watchlist()` instead of the shared universe
builder. The `ORCHESTRATOR_DAEMON_ENABLED` description (it described the deleted desktop shell and Launcher tab) is corrected, mirrored
in `webapp/src/api/mock.ts`. `scripts/com.investyo.weekly-edgar.plist` did not parse (`--` inside an XML comment is illegal); the two
comment lines were reworded, no key or value changed. `scripts/daily_briefing.py`'s footer linked the retired `daily_report.html`.

## Found while verifying (documented, not fixed)

1. **`--dry-run` does not make a standalone cycle side-effect-free in `primary`.** Confirmed by running `python main_orchestrator.py
   --dry-run` with `DAEMON_AGENTIC_QUEUE_MODE=primary` in an isolated data root: it wrote `watch_state.json` and
   `queue_sources/advisory.json`. The same run through the helper wrote neither.
2. **The synthetic-data gate does not cover a keyless run** (this also corrected a sentence I wrote in the 5.4a walkthrough and PR body;
   fixed there). `AsyncDataFetchStep` sets `data_is_synthetic` only when the price fetch returns nothing
   (`pipeline/production_steps.py` ~222). With no `FRED_API_KEY`, `live_data_configured()` is false and it picks `MockDataEngine` up
   front without the flag, so `AgenticQueueStep` / `BrokerExecutionStep` run. If the key were ever cleared on a `primary` deployment
   the daemon would write the real queue from flat-$10 mock data (per-trade human confirmation and `ADVISORY_ONLY` still stand between
   that and an order). **Not fixed here**: it changes a gate in the pipeline tier with a wide test blast radius and needs its own plan.
   The helper exits 4 for it.
3. **Process note.** My first push to 5.4a's branch after a one-test run turned CI red (`bffaec7`): the change shifted line numbers
   that `docs/settings_liveness.json` records, and I had not run the freshness gates. Fixed in `9b095dd`; the targeted set I run before
   every commit now includes `test_measure_settings_census` / `test_settings_liveness`.

## Verification

Everything below was run on the final tree of this branch (before the last merge of #1117's head, a clean `main` merge; the
freshness gates were re-run after it, see the last bullet).

- **Targeted Python run** on this tree (includes the 50 helper tests and the settings census / liveness freshness tests): 697 passed,
  1 skipped. `ruff check` clean.
- **Full offline suite** (`pytest -m "not network and not slow" -n 4 --dist loadgroup`, 4-core container, 1h03m): **12,060 passed,
  37 skipped, 1 failed.** The one failure is `tests/test_validation_forecast_direction.py::TestPerfSmoke::test_tiny_window_completes_quickly`,
  a coarse 30 s wall-clock guard. It fails when four xdist workers fight over four cores (load average ~16 during the run); alone it
  passes in 17.5 s. It failed the same way in both earlier full runs on 5.4a, and nothing under `validation/` is touched here.
- **Webapp:** `npm run --prefix webapp typecheck` clean; `vitest` for `src/api`: 124 passed. The only webapp change is the
  `ORCHESTRATOR_DAEMON_ENABLED` description string in `mock.ts`, mirrored from `settings.py`.
- **Real helper run** in an isolated `LOCAL_DATA_ROOT` with `DAEMON_AGENTIC_QUEUE_MODE=primary` in the environment and no market-data
  keys: exit 4 (`MockDataEngine` data), no execution queue, watch-state, advisory-source or heartbeat file written, and
  `make _live_run` fails instead of printing "verify complete". The same cycle run directly with `python main_orchestrator.py
  --dry-run` (no helper) did write `watch_state.json` and `queue_sources/advisory.json`; that is finding 1 above.
- **Mutation checks:** the helper's safety guards were each broken on purpose and the tests had to fail; all 8 mutations I tried were
  caught (the production `AgenticQueueStep` run under the forced mode is one of the tests that catches a missing queue-mode override).
- **Generated artifacts:** `docs/settings_field_census.{json,md}` and `docs/settings_liveness.json` regenerated with the repo scripts
  (`measure_settings_census.py --write`, `settings_liveness.py --write`); `test_measure_settings_census` and `test_settings_liveness`
  pass. `CLAUDE.md` / `AGENTS.md` are still byte mirrors (`test_agent_docs_sync`).

## Not verified / disclosed limits

- The helper was run against `MockDataEngine` data in an isolated data root (no market-data or Robinhood keys here), so it exited 4 by
  design. The operator's `make verify` on a real machine is the first run on live market data; it still rewrites `state_snapshot.json`,
  the dashboards, `dead_letter.json` and `progress.json`, stamps `last_data_refresh.txt` (which can make the daemon's timer skip its next
  pull within `DATA_FRESHNESS_TTL_SECONDS`) and appends to `risk_gate_blocks.jsonl` if the pre-trade risk gate blocks an order. The old
  `run_once()` step wrote `progress.json` too but not the snapshot, dashboards or `dead_letter.json`.
- If a daemon cycle is in flight, nothing stops the two from sharing `progress.json`, and a snapshot the daemon wrote could satisfy the
  freshness check; only the "end-of-cycle hook never reached" rule (exit 3) catches the case where this run did not finish.
- If the helper runs on synthetic data (exit 4) it has already overwritten `state_snapshot.json` and the dashboards with that data
  (`StateSnapshotStep` ignores the flag, pre-existing); it says so.
- Not exercised: the macOS-only `verify.command` beyond `bash -n` and its text pins, and any real `primary` cycle.

## Pre-existing problems found, NOT fixed here (follow-ups)

- The keyless-run gate gap above (the important one), and `StateSnapshotStep` ignoring `data_is_synthetic`.
- `alerting_mcp/notifier.py:35`: an unset `ALERT_NTFY_TOPIC` falls back to the public topic `investyo-alerts` instead of sending
  nothing (it would publish operator alerts to a shared topic).
- `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` line 34 (Options Desk row) describes `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` /
  `manage_0dte_exits`, which no longer exist outside `legacy/`. README still describes the Google Sheets publisher (archived in 4e).
- `alerting.py` / `watch_engine.py` / `queue_builder.py` comments still say "`main.py`'s advisory loop" and `NTFY_TOPIC` (5.5 territory).
- The `--agent` features (`engine.advisory_agent` backlog reminders, `engine.trade_signals` alerts) are called only from `main.py`'s
  `--agent` loop, so they disappear when 5.5 archives it. The operator should decide whether they are wanted.
