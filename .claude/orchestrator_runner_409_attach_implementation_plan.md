# Plan: `launch_orchestrator` attaches to an in-flight daemon run on 409

## Context
`shared/orchestrator_runner.py::launch_orchestrator` (backs `api/_jobs.py` `JobType.ORCHESTRATOR`) triggers the daemon via `shared.daemon_client.trigger_run()`. On HTTP 409 `already_running`, `TriggerResponse.ok` is False, so the code logs a warning and **spawns `main_orchestrator.py`**. That is a second full pipeline cycle (data, broker, snapshot) running alongside the daemon's. CLAUDE.md says "a 409 means one is already running — attach to it". `launch_daemon_cycle` (same file) already does this. Bug fix (allowed under the feature freeze); orchestrator tier → branch + PR.

## Sequencing / coordination
- #1124 is held until after **2026-10-16** (operator decision), and #1125 (`step5-6-retire-daemon-enabled-flag`) is stacked on it. Waiting would leave the duplicate-cycle bug live for 4+ days, so: **branch `orchestrator-runner-409-attach` from current `origin/main`** and keep the code change to one hunk inside the `if daemon_available():` block.
- #1125 touches the `if settings.ORCHESTRATOR_DAEMON_ENABLED and not dry_run:` line, the docstring, and renames `test_flag_on_trigger_fails_falls_back_to_subprocess` → `test_trigger_fails_falls_back_to_subprocess`. Expect a small textual conflict when #1125 rebases: keep #1125's flag removal and this PR's new test bodies. I'll note this in both PR descriptions.

## Change: `shared/orchestrator_runner.py::launch_orchestrator`
After `result = trigger_run()`, if `not result.ok and result.error == "already_running"`:
1. `run_id = result.existing_run_id or _inflight_daemon_run_id()`. Reuse the helper so a 409 with an unparseable body still attaches when `GET /run/latest` confirms a queued/running run, as `launch_daemon_cycle` does.
2. **Non-full in-flight run** (status via `_daemon_run_status(run_id)`, `mode` present and `!= "full"`, e.g. a `/pipeline/data` sub-run): falling back would spawn a parallel cycle, and attaching would report a data-only run as this job's result. Mirror `launch_daemon_cycle` and **refuse** with `RuntimeError`, which `api/control_api.py` maps to HTTP 409. **The raise must escape the fast path's `except Exception` (which otherwise turns it into the subprocess fallback)**, so the refusal is recorded inside the try and raised after it. Unverifiable mode → attach (same rule as `launch_daemon_cycle`).
3. Otherwise return `RunHandle(pid=-1, mode="orchestrator", backend="daemon", daemon_run_id=run_id, dry_run=False, log_path=RUN_LOG_PATH, ...)`, the same shape as the success branch, and log "attaching".
4. 409 with no verifiable id → keep today's subprocess fallback (your scope: "on a 409 *with* `existing_run_id`"), with the warning saying so.
- Unchanged fallback: daemon unreachable, other trigger errors (`network_error`, `unavailable`, `kill_switch_active`, …), exceptions, `dry_run=True`.
- Update the docstring "Notes" to list the 409 attach behavior.

I'll implement the frame and hand you the ~6-line non-full-run refusal decision as a `TODO(human)`.

## `api/_jobs.py` (caller): verified, no change expected
- `start_job` re-raises launcher exceptions (control_api → 409 for `RuntimeError`). The cancel-during-launch path already branches on `handle.backend`, so an attached daemon handle is not "cancelled" and the run continues. `JobRecord` treats non-subprocess handles as not cancellable (line ~100). Attaching therefore works without any caller edits.

## Tests: `tests/test_orchestrator_runner_daemon_cutover.py`
- `test_flag_on_trigger_fails_falls_back_to_subprocess` → rename `test_flag_on_409_already_running_attaches_to_existing_run`: Popen must not be called; asserts `backend=="daemon"`, `daemon_run_id=="run-xyz"`, `mode=="orchestrator"`, `pid==-1`.
- New `test_flag_on_non_409_trigger_failure_falls_back_to_subprocess` (e.g. `error="network_error"`) → subprocess.
- New: 409 without body id but `/run/latest` running → attaches; 409 with no verifiable id → subprocess fallback; 409 on a non-full run → `RuntimeError`, Popen not called.

## Docs
- `docs/architecture/webapp-and-gui.md` (the `launch_orchestrator` fast-path paragraph): replace "falls back on ANY failure" with the 409 attach / non-full refusal.
- `.claude/orchestrator_runner_409_attach_{implementation_plan,task,walkthrough}.md` committed on the branch.
- CLAUDE.md: no rule changes.

## Verification
- `pytest tests/test_orchestrator_runner_daemon_cutover.py tests/test_orchestrator_runner.py tests/test_control_api.py` (+ any `_jobs` tests): zero failures.
- `make ci`: zero failures (report any pre-existing failures honestly against `origin/main`).
- Self-review the full diff, open the PR, and merge once green per CLAUDE.md rule 5.
