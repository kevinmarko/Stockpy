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
