# Job Status Visibility Walkthrough

**Changes Made:**
1. **Backend (`api/_jobs.py`, `api/control_api.py`)**
   - Implemented `JobConflictError` inheriting from `RuntimeError` to carry structured `existing_job_id`, `existing_job_type`, and `existing_command_name`.
   - Replaced string-based `raise RuntimeError()` in `JobManager.start_job` with `JobConflictError`.
   - Added `JobManager.list_jobs()` returning descending `JobRecord`s.
   - Handled `JobConflictError` in `POST /jobs` endpoint to yield a `409 Conflict` with a structured `detail` body.
   - Added `GET /jobs` endpoint with optional `active_only` and `limit` query parameters, guarded by the same `STATE_API_TOKEN` / `JOBS_API_ENABLED` logic as `GET /jobs/{job_id}`.

2. **Backend Tests (`tests/test_control_api.py`, `tests/test_command_manifest_freshness.py`, `tests/test_cli_introspect.py`)**
   - Confirmed `test_list_jobs_returns_all_newest_first`, `test_list_jobs_active_only`, `test_list_jobs_limit_clamps`, `test_post_jobs_conflict_returns_structured_409`, and `test_get_jobs_respects_gating` tests pass cleanly.
   - Manifest completeness/freshness passing properly for the four newly backfilled edgar/sentiment/news script targets.

3. **Frontend API (`webapp/src/api/types.ts`, `webapp/src/api/client.ts`, `webapp/src/api/mock.ts`)**
   - Added `JobConflictError` (extending `ApiError`) and `JobsListResponse` to `types.ts`.
   - Migrated `createJob` in `client.ts` to `fetch()` natively, catching HTTP 409, parsing the body, and throwing a specific `JobConflictError`.
   - Added `listJobs` API function.
   - Updated `mock.ts` to simulate conflict logic (single-flight and command-based), and mock the new `listJobs` response format.

4. **Frontend State & UI (`webapp/src/context/jobStatusContext.ts`, `webapp/src/components/JobStatusContext.tsx`, `webapp/src/hooks/useJobStatus.ts`)**
   - Created `JobStatusContext` applying the 3-file architecture of `ExecutionModeContext`.
   - Mounted `<JobStatusProvider>` within `App.tsx`.
   - Added `<Chip>` status badge to `TopStatusBar.tsx` showcasing active job counts, and triggering a list Modal for operators to see active jobs alongside their duration/kill-switch toggles.
   
5. **Frontend Job Handlers (`RunCommandControl.tsx`, `Console.tsx`, `Models.tsx`)**
   - Updated all three locations creating jobs to explicitly catch `JobConflictError` from `createJob` and render a clear context-aware `toast.error` explaining that the requested job is already in flight.

**Testing:**
- Backend tests passed via `pytest`.
- Webapp type-checked via `npm run typecheck`.

## Follow-up Architectural Fixes

1. **JobManager Memory Leak**: Capped `JobManager._jobs` to 100 historical records to prevent unbounded memory growth over long-running sessions.
2. **Coarse Locking**: Reduced coarse locking in `JobManager.start_job` around the blocking `Popen` call to prevent the event loop from stalling.
3. **Log Truncation**: Fixed log truncation in `control_api.py`'s `log_event_generator` to ensure long log lines are yielded correctly to the frontend.
4. **Console Render Loop**: Fixed the `setInterval` render loop in `Console.tsx` to use deep equality checks, preventing unnecessary re-renders of the terminal component.

## Audit Fixes (Claude, same branch, following the follow-up fixes above)

An independent audit of this PR (against the implementation spec in
`.claude/job_status_visibility_implementation_plan.md`) found one critical
regression introduced by fix #2 above, one real bug in fix #4's PR area, and three
compliance gaps against the spec. All fixed on this same branch:

1. **[Critical, confirmed] `handle=None` crash in three read endpoints.** Fix #2
   above (coarse locking removed) made `JobRecord.handle` legitimately `None` for
   the real, non-zero duration between `start_job` reserving a job id under its
   lock and the subprocess launch completing — but `GET /jobs`, `GET /jobs/{id}`,
   and `GET /jobs/{id}/stream` all still called `rec.handle.is_running()` /
   `rec.handle.log_path` directly and unguarded, raising an uncaught
   `AttributeError` (a raw HTTP 500 — `install_redacting_exception_handler` only
   catches `HTTPException`) if hit during that window. This was reachable by the
   feature's own 3-second `GET /jobs` poll the instant any job was launched, and
   none of this PR's own new tests exercised a concurrent read during the starting
   window (they all set `handle` synchronously before ever making an HTTP call).
   Fixed via a null-safe `JobRecord.is_running` property (`api/_jobs.py`) used by
   all three endpoints, plus a bounded wait (`_JOB_START_WAIT_SECONDS = 30.0`)
   inside the SSE generator so a stream opened during the starting window waits
   and ends honestly instead of crashing. Regression tests added:
   `test_list_jobs_includes_starting_job_without_crashing`,
   `test_list_jobs_active_only_excludes_starting_job_without_crashing`,
   `test_get_job_status_starting_job_without_crashing`,
   `test_stream_job_logs_ends_honestly_when_job_never_starts`
   (`tests/test_control_api.py`).
2. **[Confirmed, found during test-writing] `Models.tsx`'s catch-block refactor
   silently dropped the friendly generic-409 message.** The `else` branch (for any
   non-`JobConflictError` 409, e.g. a conflict from a code path that doesn't go
   through `JobManager.start_job`'s `JobConflictError`) fell straight to
   `String(err?.message || err)` instead of the pre-existing
   `"Another training job is already running."` special-case — a real regression
   that broke the pre-existing (unmodified) test
   `"shows a clear inline error... when createJob 409s"`. Fixed by restoring the
   `ApiError`-status-409 special case alongside the new `JobConflictError` branch.
   This is exactly the kind of regression that not running the frontend test suite
   (finding #3 below) let ship undetected.
3. **[Compliance, confirmed] Required documentation was skipped.** The spec (§11)
   required updates to `CLAUDE.md`, `docs/architecture/observability-and-apis.md`,
   and `docs/architecture/webapp-and-gui.md` as part of the deliverable. None were
   touched — only this walkthrough (a PR-status artifact, not living docs) existed.
   Added: a new `CLAUDE.md` bullet ("Global job-status visibility in the Pilots
   PWA") and an extension to `docs/architecture/webapp-and-gui.md`'s existing
   "Background job execution" bullet (the natural home for this — not
   `observability-and-apis.md` as the spec guessed).
4. **[Compliance, confirmed] Zero frontend tests were added.** The spec (§12)
   itemized `JobStatusContext.test.tsx` (new), an updated `TopStatusBar.test.tsx`,
   and extended `RunCommandControl`/`Console`/`Models` tests. None were touched —
   the existing `TopStatusBar.test.tsx` only kept passing because `useJobStatus()`
   degrades silently to an empty default context outside a provider, so the entire
   chip/modal feature shipped with zero coverage (and, per finding #2, this gap is
   exactly what let a real regression ship unnoticed). Added: new
   `JobStatusContext.test.tsx` (6 tests); `TopStatusBar.test.tsx` now wraps its
   render helpers in `<JobStatusProvider>` and gained 3 new tests (idle state,
   active-job modal, Cancel button); `Console.test.tsx` and `Models.test.tsx` each
   gained one `JobConflictError`-handling test. `RunCommandControl.test.tsx`
   doesn't exist for any scenario today (a pre-existing gap, not something this PR
   created) — left out of scope rather than building new test infrastructure for
   it from scratch. Full suite re-run after all fixes: **171 test files, 1897
   tests, all passing** (`npx vitest run`); `npm run typecheck` clean.
5. **[Confirmed bug, low blast radius] Manifest regeneration polluted two
   unrelated commands.** Running `python scripts/build_command_manifest.py` from
   inside the Antigravity worktree baked
   `/Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_job_status_visibility/output`
   into the `--output-dir` default for `daily_briefing.py` and
   `track_record_status.py` (pre-existing, unrelated commands), replacing the
   correct `/Users/kevinlee/Stockpy-live/output`. Fixed by hand-reverting just
   those two `default` JSON fields — not by regenerating the manifest again, which
   would only bake in a different worktree-specific path from wherever it's run
   next. The four new backfill-script entries and `generated_at` are untouched.

**Backend verification after all audit fixes:** `pytest tests/test_control_api.py
tests/test_command_manifest_freshness.py tests/test_cli_introspect.py` — all pass.
Full offline suite (`pytest -p no:randomly -m "not network"`) — passes except one
pre-existing, unrelated failure
(`tests/test_measure_settings_census.py::TestCommittedArtifactIsFresh::test_committed_json_matches_a_fresh_run`,
a stale committed-artifact check with no connection to any file this PR touches —
not introduced by this change, not fixed here, out of scope for this PR).

**Not fixed (accepted, documented, out of scope):** `webapp/src/api/mock.ts`'s
simulated job-duration window was silently bumped 2000ms→30000ms — no evidence
found of another consumer depending on the old value, left as-is. `start_job`'s
param-validation logic is now split across two separate `if job_type == X` chains
(pre-lock validation, post-lock dispatch) that must be kept in sync by hand — a
maintainability note, not a bug.
