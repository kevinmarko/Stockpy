# Walkthrough: launch_orchestrator attaches on a daemon 409

## Bug
`shared/orchestrator_runner.py::launch_orchestrator` (Console `JobType.ORCHESTRATOR`) treated the daemon's
HTTP 409 `already_running` like any other trigger failure and spawned `main_orchestrator.py`, so a
second full pipeline cycle (data fetch, paper broker, snapshot) ran in parallel with the daemon's.

## Fix
On `already_running`, `launch_orchestrator` now follows `launch_daemon_cycle`:
1. The run id comes from the 409 body; failing that, from `_inflight_daemon_run_id()` (`GET /run/latest`,
   accepted only while queued/running).
2. When `_daemon_run_status(id)` reports a `mode` other than `full` (a data/metrics sub-run), it refuses with an
   operator-readable `RuntimeError` (HTTP 409 via `api/control_api.py`). The message is stored inside the
   fast path's `try` and raised after it; raised inside, the `except Exception` catch-all would
   turn it into the parallel subprocess this fix exists to prevent. An unknown mode still attaches.
3. Otherwise it returns a `backend="daemon"`, `mode="orchestrator"` `RunHandle` for the existing run.

Unchanged: subprocess fallback when the flag is off, `dry_run=True`, the daemon is unreachable, any other
trigger error, a 409 with no verifiable id, or an exception.

## Caller
`api/_jobs.py`: needs no change. `start_job` already re-raises launcher errors (mapped to 409), and the
cancel-in-launch-window path branches on `handle.backend`, so an attached daemon handle is not marked as cancelled.

## Tests
`tests/test_orchestrator_runner_daemon_cutover.py::TestLaunchOrchestratorDaemonFastPath`: 409 attaches
(no Popen), attaches to a full run, attaches via /run/latest, falls back when no id can be verified, refuses data/metrics
runs without spawning, and falls back on 6 non-409 errors.

## Coordination with #1125
#1125 removes the `ORCHESTRATOR_DAEMON_ENABLED` gate and renames the `test_flag_on_*` tests. When it
rebases, keep its flag removal and this PR's test bodies (drop the `ORCHESTRATOR_DAEMON_ENABLED`
monkeypatch in `_patch_fast_path`).
