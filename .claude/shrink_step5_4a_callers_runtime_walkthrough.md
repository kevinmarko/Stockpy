# Step 5.4a walkthrough: runtime callers off `main.py`

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md` (PR 5.4a section). Tracker:
`.claude/shrink_step5_4a_callers_runtime_task.md`. Branch `claude/ecstatic-maxwell-f6ruct`, cut from `origin/main` @ `2f53931`.

**Code only.** Nothing here changes the live `.env`, `runtime_flags.json` or `~/.stockpy_local`. 5.4b (shell/`make verify`/skills/
docs) is a separate PR; 5.5 archives `main.py`.

**Correction (2026-10-12).** An earlier version of this walkthrough, the plan and the PR text said the operator had confirmed the live
system was already cut over (`DAEMON_AGENTIC_QUEUE_MODE=primary`). That premise was wrong: `main` (#1119, merged 2026-10-12 10:07Z)
records that the first shadow review "wasn't clean" and that a second 5-trading-day review runs 2026-10-12..10-16, with cutover after
day 5. **The live daemon is still on `shadow`.** Nothing in 5.4a depends on the cutover (see the next section for the one behaviour
change), but the parts of step 5.4 that do (deleting the 08:45 launchd job and `launch.command`, marking RUNBOOK 3.15 done, skills that
stop when the mode is not `primary`) are held until after the review.

## Behaviour change while the live daemon is on `shadow`

The Console "Advisory Pipeline" job, the MCP `generate_html_report` tool and `POST /dead-letter/retry` used to run `main.py`. While
the queue mode is not `primary`, `main.py` is the REAL queue writer, so those three actions refreshed the live `execution_queue.json`,
ran the watch engine and sent pushes. They now trigger a daemon cycle, and in `shadow` a daemon cycle writes only the review copy under
`OUTPUT_DIR/shadow/` and sends nothing. So until cutover the only things that refresh the real queue are the 08:45 `main.py` launchd
job and a hand-run `python3 main.py`; pressing those three buttons no longer does. Manual `main.py` runs are unaffected (5.5 archives it).

## What changed

**`shared/orchestrator_runner.py` / `api/_jobs.py`**
- Deleted `launch_advisory_main`, `launch_scheduled_advisory`, `launch_symbol_retry`, their three log-path constants and
  `HIGH_STAKES_COMMANDS["main.py"]`.
- Fixed a standing bug: `launch_orchestrator` appended `--refresh-account` to `main_orchestrator.py`, whose argparse has only
  `--dry-run/--strict`, so the subprocess fallback exited 2. The parameter is gone; a truthy `refresh_account` on an `advisory` or
  `orchestrator` job now returns HTTP 400 pointing at `POST /brokerage/refresh`.
- New `launch_daemon_cycle()`: daemon-ONLY (no subprocess fallback, no dependence on `ORCHESTRATOR_DAEMON_ENABLED`). Backs
  `JobType.ADVISORY`. On HTTP 409 it attaches to the in-flight run, but refuses to attach to a run whose mode is verifiably
  `data`/`metrics` (those never run the advisory/broker/queue/snapshot steps). Every refusal (kill switch, token, daemon down) is a
  `RuntimeError` with an operator-readable message; none echoes a token. A cancel during the launch window no longer marks a
  running daemon cycle "cancelled".

**`POST /dead-letter/retry`** now triggers one full daemon cycle through the `/automation/run` handler (same error mapping) and
returns `{symbol, run_id, applies, note}`. The note says plainly that the whole universe is re-run, that the old retry was never
per-symbol, and that a cycle can place paper orders / write the queue. (Today only the daemon's `StrategyEvalStep` writes
`dead_letter.json`, so the old retry could never clear an entry.)

**MCP `generate_html_report`** triggers a daemon cycle, polls `get_run_status`, and only reports `daily_report_dashboard.html` as
fresh when its mtime is >= the run's `started_at` and the run mode is `full`. It never defaults an unknown mode, never claims a stale
file is fresh, and discloses that a synthetic-data cycle still renders the report and cannot be detected by the tool.

**Command manifest / completions / webapp**: `main.py` is no longer an operator command (manifest, completions, mock manifest,
`commandParse.ts`). Console "Advisory Pipeline" keeps its label; its description and an in-panel note say it is a daemon-hosted full
cycle (no log lines, not cancellable). Dead-letter Retry shows the run id and the whole-universe note. Mock jobs mirror the live
non-cancellable/400/409 behaviour. All "run `python3 main.py ...`" hints (Python + webapp) now point at Settings -> Brokers ->
Refresh or `POST /run`. The stale `DAEMON_AGENTIC_QUEUE_MODE` description (said primary was "reserved for 5.3") is corrected.

## Bugs found while verifying, fixed here
1. **`shared/daemon_client.trigger_run` lost the 409 run id and 423 kill-switch reason against the real server.** FastAPI serializes
   `HTTPException(detail={...})` as `{"detail": {...}}`; the client read the top level. The old tests used flat bodies. Now unwrapped
   (`_flatten_error_detail`), tolerant of non-object JSON, with real-shaped regression tests (fail without the fix).
2. **`GET /jobs/{id}/stream` blocked the Control API's event loop for daemon-hosted jobs.** It called `rec.is_running` /
   `rec.exit_code()` on the loop; for a daemon handle those are a blocking HTTP call to the same server, which cannot answer, so each
   timed out after 2 s. Measured on a real daemon: stream of a finished job took 4.02 s and ended "exit code None"; `/health`
   stalled 3.6 s. Now read in a worker thread: 0.02 s, "exit code 0", `/health` 2 ms; a stream opened mid-cycle stays open for the real
   cycle and ends with the real exit code. Pre-existing, but this PR made it reachable by default (ADVISORY is always daemon-backed).
3. **`createJob` showed any 409 as "Already running (job null)"**, including a launch refused for kill switch / daemon down. A string
   `detail` is now a plain `ApiError` carrying the real reason; an object `detail` stays a single-flight `JobConflictError`.

## Verification (run, not assumed)
- Full offline suite, 4 workers, final tree: **11,898 passed, 37 skipped, 2 failed**. Both failures have a demonstrated cause outside
  this PR and pass when run alone (21 passed):
  - `test_validation_forecast_direction.py::TestPerfSmoke::test_tiny_window_completes_quickly`: wall-clock ceiling of 30 s; 3.1 s
    alone, 34-37 s under 4-worker load. Code untouched by this PR.
  - A `TypeError: expected string or bytes-like object, got 'NoneType'` from `urllib` loopback calls (hit a different test in each of
    three full runs: `inspect_webapp_screen`, `/automation/status`). Cause: `fredapi.Fred.__init__` installs a process-global opener
    with `{'http': None, 'https': <proxy>}` when `HTTPS_PROXY` is set but `HTTP_PROXY` is not (this sandbox). Reproduced with no repo
    code (`Fred(api_key=...)` then any `urlopen("http://...")`); setting `HTTP_PROXY` makes it disappear. A normal machine with no
    proxy variables, or a CI runner, is unaffected.
- `ruff check . --select=F821,F822,F823,E9`: clean. Census/liveness regenerated and their freshness tests pass.
- Webapp: `npm run typecheck` clean; full vitest 147 files / 1,781 tests pass.
- Browser (Playwright + Chromium against the mock API): Console, Pipeline Dashboard, Commands, Trade History, Settings -> Brokers:
  zero console errors/warnings; Console launch shows the daemon-hosted note and no Cancel; Retry shows the run id and whole-universe
  note; no `main.py` anywhere; two screenshots inspected.
- Real daemon (`python -m desktop.orchestrator_daemon --dry-run`, mock data, isolated data root): job launch/poll to `success`,
  mid-cycle retry -> 409 with the real run id, second launch -> single-flight conflict, `refresh_account` -> 400, kill switch -> 409
  (string detail) / 423 (nested detail) both with the reason, idle retry -> 200 new shape, stream timing as above.
- Reviews: API-parity reviewer (one real finding, fixed) and honesty auditor (V1-V4, P1-P3 fixed or disclosed).
- Mutation checks: each new regression test fails with its fix removed (daemon_client unwrap, createJob 409, stream event loop,
  cancel-during-launch).

## Not verified / disclosed limits
- No real primary cycle (queue write, watch alerts, paper orders): no market-data/Robinhood keys here, and every smoke run used
  `DAEMON_AGENTIC_QUEUE_MODE=off`. (An earlier draft of this note said the synthetic-data gate would skip the queue + broker in
  this sandbox. That is wrong for a keyless run: see "Pre-existing problems" below.) The operator's RUNBOOK 3.15 live smoke test
  covers a real primary cycle.
- Daemon-hosted jobs: SSE log stream is empty (the daemon never writes a job log) and cancel is unsupported; the Console says so.
  `get_run_status` needs `STATE_API_TOKEN` when set, and `daemon_client` sends only `ORCHESTRATOR_DAEMON_TOKEN`: with differing tokens a
  job reads "unknown" immediately. Check both tokens before relying on the Console job.
- `cancel_job` already answered `cancelled: true` during the launch window before the backend is known; only a log line records that
  the daemon cycle kept running.
- The `/run/latest` fallback exists in both `launch_daemon_cycle` and the MCP tool (now redundant given the `daemon_client` fix, kept as
  defense in depth).
- Setup note: `./setup.sh` needs `zsh`; in this container the venv was built with `uv venv` + `uv pip install -r requirements.txt`.

## Pre-existing problems found, NOT fixed here (follow-ups)
- `StateSnapshotStep` ignores `data_is_synthetic`: during a market-data outage the daemon writes `state_snapshot.json` and the
  dashboards from fabricated MockDataEngine data and records the cycle `succeeded` (same for the dead-letter write). Needs its own
  plan (pipeline tier).
- **The synthetic-data gate does not cover a keyless run.** `AsyncDataFetchStep` sets `data_is_synthetic` only in the fallback for an
  empty price fetch (`pipeline/production_steps.py` ~222). When `FRED_API_KEY` is unset, `live_data_configured()` is false and the
  step picks `MockDataEngine` up front without the marker, so `AgenticQueueStep` and `BrokerExecutionStep` do not skip. Found by
  running `python main_orchestrator.py --dry-run` with `DAEMON_AGENTIC_QUEUE_MODE=primary` and no key in an isolated data root: it
  wrote `watch_state.json` and `queue_sources/advisory.json` from the flat-$10 mock data. If the key were ever cleared on the live
  `primary` deployment, the daemon would write the real queue from fabricated prices (orders still need per-trade human
  confirmation, and `ADVISORY_ONLY` still blocks paper orders). Needs its own plan (pipeline tier); not changed here.
- `launch_orchestrator`'s fast path (flag-gated, off by default) falls back to a subprocess on 409/423 and uses a stale-log path;
  retired with the flag in 5.6.
- Stale or untrue help text: "no orders placed" claims in `shared/help_content.py` and `webapp/src/help/helpContent.ts`, pointers to the
  deleted Streamlit Launcher tab (`pilots/gravity_audit.py`), settings descriptions that contradict their own defaults
  (`PILOTS_API_ENABLED`, `JOBS_API_ENABLED`, `DEAD_LETTER_RETRY_ENABLED` comment), `WEEKLY_DIGEST_ENABLED` text.
- The webapp's `HIGH_STAKES_COMMANDS` omits `database_setup.py`, which the server gates; the Run control always sends `confirm: true`.
- `getCronStatus` / `health` client methods have no caller.
- `fredapi`'s global-opener side effect above (consider a loopback-only opener in `daemon_client`).
