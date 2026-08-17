# Task: Job-Cancellation TOCTOU Gap & `stop_run()` Root-Cause Fix

- [x] Reproduce the residual TOCTOU race on merged `main` (nonzero-exit-code race still mislabeled "cancelled") with a standalone script <!-- id: 0 -->
- [x] Confirm `gui/orchestrator_runner.py` has zero diff in PR #754 despite the PR's own claim of fixing "stop_run Caller Semantics" <!-- id: 1 -->
- [x] Investigate the apparent 9-test `conftest.py` regression on `main`; determine it was a local `__pycache__`/`.pytest_cache` artifact, not a real bug — retract, no fix needed <!-- id: 2 -->
- [x] Add `StopOutcome` + `stop_run_detailed()` to `gui/orchestrator_runner.py`; keep `stop_run()` as a backward-compatible bool wrapper <!-- id: 3 -->
- [x] Rewrite `api/_jobs.py::cancel_job()` to use `stop_run_detailed()`, removing the `returncode() == 0` heuristic <!-- id: 4 -->
- [x] Update the 4 existing `tests/test_control_api.py` tests that monkeypatch `stop_run` to monkeypatch `stop_run_detailed` instead <!-- id: 5 -->
- [x] Add a new parametrized (`rc=0`, `rc=1`) regression test proving the nonzero-exit race is closed <!-- id: 6 -->
- [x] Run `tests/test_control_api.py` (104 passed) <!-- id: 7 -->
- [x] Run every other test file importing `api._jobs`/`gui.orchestrator_runner` <!-- id: 8 -->
- [x] `ruff check` on the changed Python files (clean) <!-- id: 9 -->
- [x] Update `docs/architecture/webapp-and-gui.md`'s "Background job execution" bullet <!-- id: 10 -->
- [x] Write implementation plan, task tracker, and walkthrough under `.claude/` with a unique project-scoped name <!-- id: 11 -->
