# Job-Cancellation TOCTOU Gap & `stop_run()` Root-Cause Fix

## Overview

Follow-up to PR #747 ("guard `cancel_job` against completed runs") and PR #754
("resolve all 10 background job lifecycle & status review findings"). An
independent re-review of PR #754 (deep-dived against the actual merged diff,
not its self-reported test counts) found that two of its ten claimed fixes
were incomplete:

1. **Residual TOCTOU race** — PR #754's `cancel_job()` added a `returncode()
   == 0` guard to avoid mislabeling a job that raced to a clean exit as
   "cancelled", but a job racing to a **nonzero** exit code (a genuine
   failure) in the same window was still mislabeled `"cancelled"`.
   Reproduced with a standalone script against the merged code before this
   fix: a `_FakeHandle(running=True, rc=1)` racing a cancel request got
   `{"cancelled": true}` and a final status of `"cancelled"` instead of
   `"failed"`.
2. **Root cause left unfixed** — `gui/orchestrator_runner.py::stop_run()`
   conflates "the process was already dead when we looked" with "we just
   killed it" into the same bare `True` return. PR #754's own walkthrough
   claims a fix ("Finding 4: stop_run Caller Semantics — Standardized
   process termination contracts"), but `git diff` shows **zero** changes to
   that file — the claim is unsubstantiated. `gui/panels/ai_control_center.py`
   and `desktop/engine_supervisor.py` still inherit the same ambiguity.

A third item from the same re-review — "9 failing tests in
`test_control_api.py::TestJobsApi` on current `main`, caused by an unrelated
`conftest.py` change" — was **retracted after further investigation**: those
failures were reproduced to be an artifact of stale `__pycache__`/
`.pytest_cache` state left over from this reviewer's own bisection work (a
temporary worktree built against a different Python interpreter), not a real
bug in `main`. A clean cache + rerun shows all 103 pre-existing tests passing.
No conftest.py change is included in this fix.

## Root cause

`stop_run()`'s own "already exited" branch
(`if popen.poll() is not None: return True  # already exited`) returns the
identical `True` a genuine SIGTERM/SIGKILL-confirmed kill returns. Every
caller that infers "I just stopped it" from a bare `True` inherits this
ambiguity. `api/_jobs.py::cancel_job()` is the one caller where that
ambiguity has a user-visible consequence: a job that finishes on its own
(any exit code) gets its honest terminal status silently overwritten with
`"cancelled"` if a cancel request lands in the same window.

## Fix

- **`gui/orchestrator_runner.py`**: add `StopOutcome(stopped, already_stopped)`
  and `stop_run_detailed()`, which do the same signal/wait/escalate work as
  `stop_run()` but report `already_stopped=True` for every branch that
  returns `True` without ever having sent a signal (the "already exited"
  Popen branch, the "not pid_alive" PID-fallback branch, and the
  `handle is None` case). `stop_run()` becomes a thin bool-returning wrapper
  over `stop_run_detailed()` — every existing caller (`gui/panels/
  ai_control_center.py`, `gui/panels/launcher.py`,
  `desktop/engine_supervisor.py`) is unaffected, same signature, same
  return type, same tests pass unchanged.
- **`api/_jobs.py::cancel_job()`**: replaced the `is_running()` pre-check +
  `stop_run()` + `returncode() == 0` heuristic with a single call to
  `stop_run_detailed()`. `already_stopped=True` → return `rec.cancelled`
  (honest either way: `False` if it finished on its own, `True` if an
  earlier call already cancelled it — never flips `rec.cancelled` in this
  branch). `already_stopped=False` and `stopped=True` → this call really did
  kill it: set `rec.cancelled = True`, return `True`. This closes the TOCTOU
  gap for every exit code, not just `0`, and removes the fragile
  `returncode()`-based heuristic entirely.

## Documentation-update step

- `docs/architecture/webapp-and-gui.md`'s "Background job execution" bullet
  updated to describe `stop_run_detailed()`/`StopOutcome` and why the prior
  `returncode() == 0` heuristic was insufficient.
- This implementation plan, task tracker, and walkthrough committed under
  `.claude/` with a project-scoped unique name distinct from the
  `gravity_job_status_fix_*` artifacts PR #747/#754 already used.

## Verification plan

- `tests/test_control_api.py::TestJobsApi` — update the four existing tests
  that monkeypatch `stop_run` to monkeypatch `stop_run_detailed` (returning
  `StopOutcome`) instead; add a new parametrized regression test
  (`rc=0` and `rc=1`) proving the previously-open nonzero-exit race is now
  closed.
- Full `tests/test_control_api.py`, plus every other test file importing
  `api._jobs`/`gui.orchestrator_runner` (`test_ai_control_center.py`,
  `test_orchestrator_runner_daemon_cutover.py`, `test_engine_supervisor.py`,
  `test_command_execution.py`, `test_pipeline_stage_status.py`,
  `test_run_progress.py`, `test_security_audit_fixes.py`).
- `ruff check` on the three changed Python files.
