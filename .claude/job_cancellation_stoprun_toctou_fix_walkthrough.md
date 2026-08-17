# Walkthrough: Job-Cancellation TOCTOU Gap & `stop_run()` Root-Cause Fix

## Summary of Changes

- **`gui/orchestrator_runner.py`**: added `StopOutcome(stopped, already_stopped)`
  and `stop_run_detailed()`. `stop_run()` is now a thin, byte-for-byte
  backward-compatible wrapper (`return stop_run_detailed(handle,
  timeout=timeout).stopped`) — its signature, return type, and behavior for
  every existing caller (`gui/panels/ai_control_center.py`,
  `gui/panels/launcher.py`, `desktop/engine_supervisor.py`) are unchanged.
- **`api/_jobs.py::JobManager.cancel_job()`**: replaced the `is_running()`
  pre-check + `stop_run()` + `returncode() == 0` heuristic (from PR #754)
  with a single call to `stop_run_detailed()`. `already_stopped=True` now
  returns `rec.cancelled` honestly (never mutates it) regardless of what
  exit code the job raced to; `already_stopped=False` and `stopped=True`
  sets `rec.cancelled = True` and returns `True` — this really was the call
  that killed it.
- **`tests/test_control_api.py`**: updated the four existing tests that
  monkeypatched the now-internal `stop_run` to monkeypatch
  `stop_run_detailed` (returning `StopOutcome`) instead; added a new
  parametrized regression test,
  `test_cancel_race_with_natural_completion_returns_false_and_preserves_status[rc=0,1]`,
  proving the nonzero-exit race is now closed (the old test only covered
  `rc=0`).
- **`docs/architecture/webapp-and-gui.md`**: "Background job execution"
  bullet updated to describe `stop_run_detailed()`/`StopOutcome` and why the
  prior `returncode() == 0` heuristic was insufficient.

## What was investigated but NOT fixed (retracted)

A prior review pass flagged "9 failing tests in
`test_control_api.py::TestJobsApi` on current `main`, caused by an unrelated
`conftest.py` settings-reset commit (`54501c6c`)." Re-investigating to build
this fix, that turned out to be **wrong** — a `git worktree add` used during
the original bisection ran tests under a different Python interpreter
(system Python 3.14 instead of the project's `.venv` Python 3.12), and rapid
edit/restore cycles on `conftest.py`/`api/auth.py` while diagnosing it left
stale `__pycache__`/`.pytest_cache` state in the main checkout. After
`rm -rf` on both and a clean rerun, all 103 pre-existing tests on `main`
pass. **No `conftest.py` change is included in this fix** — there was
nothing to fix.

## Verification Results

### 1. Reproduced the bug before fixing it

Standalone script against the pre-fix merged code (`api/_jobs.py`'s
`returncode() == 0` guard):

```
CANCEL RESPONSE: 200 {'job_id': 'job-88b56d48', 'cancelled': True}
```

for a `_FakeHandle(running=True, rc=1)` (a job racing to a **nonzero**,
"failed" exit right as cancel was requested) — confirming the gap the new
parametrized test now covers.

### 2. Targeted pytest

```bash
uv run pytest tests/test_control_api.py tests/test_ai_control_center.py \
  tests/test_orchestrator_runner_daemon_cutover.py tests/test_engine_supervisor.py \
  tests/test_command_execution.py tests/test_pipeline_stage_status.py \
  tests/test_run_progress.py tests/test_security_audit_fixes.py \
  -q -k "not TestCapabilityRowToggleDedup"
```

**Result**: `286 passed, 3 deselected` (the deselected tests are a
pre-existing, unrelated Streamlit `AppTest` 15s-timeout flake in
`test_ai_control_center.py`, reproduced as failing identically with and
without this change — not touched by this fix).

`tests/test_control_api.py` alone: `104 passed` (up from 103 — the old
single-scenario race test was replaced by a 2-variant parametrized test).

### 3. New regression test confirms the fix

```bash
uv run pytest "tests/test_control_api.py::TestJobsApi::test_cancel_race_with_natural_completion_returns_false_and_preserves_status" -v
```

`2 passed` — both `rc=0` ("success") and `rc=1` ("failed") natural
completions racing a cancel request now correctly report `cancelled: false`
and preserve the true terminal status, instead of only the `rc=0` case.

### 4. Lint

```bash
ruff check api/_jobs.py gui/orchestrator_runner.py tests/test_control_api.py
```

Zero new findings in the three changed files (81 pre-existing findings
elsewhere in `test_control_api.py`, none in the ranges touched by this fix).

### 5. Webapp

No webapp files touched by this fix — the two findings addressed here
(`api/_jobs.py`, `gui/orchestrator_runner.py`) are backend-only. Webapp
typecheck/vitest were separately re-verified clean against `main` during the
review that produced this fix (typecheck clean, 1721/1721 vitest passing).
