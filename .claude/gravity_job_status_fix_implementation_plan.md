# Background Job Status Lifecycle & Post-Completion Cancellation Fix

## Overview
When background jobs (such as the Gravity AI Review Suite `gravity`, `pytest`, `validation`, or `advisory`) finish executing, their status is evaluated based on the process exit code (`success` or `failed`). However, two subtle lifecycle bugs existed:
1. **Post-Completion Cancellation Race & Status Overwrite**: If a cancellation request (`POST /jobs/{job_id}/cancel`) arrived after a job had already completed (or while it was exiting), `stop_run()` returned `True` (since `poll()` was not None), setting `rec.cancelled = True` and permanently overwriting the job's terminal status from `"success"` or `"failed"` to `"cancelled"`.
2. **Ambiguous `cancellable` Table Display**: In `Console.tsx`, the Recent Jobs table rendered `Cancellable: Yes` for completed/dead jobs because `rec.cancellable` reflected the backend capability (subprocess vs. daemon) rather than active lifecycle state.

---

## Changes

### 1. Backend (`api/_jobs.py`)
- In `JobManager.cancel_job(job_id)`:
  - Guard check: `if not rec.handle.is_running(): return False`.
  - Only when `rec.handle.is_running()` is `True` and `stop_run(rec.handle)` confirms stopped, mark `rec.cancelled = True`.
- Preserves honest terminal status (`success`/`failed`) and exit codes across late or duplicate cancel requests.

### 2. Frontend (`webapp/src/screens/Console.tsx`)
- In Recent Jobs table, `cancellable` column renders `row.status === "running" || row.is_running ? (row.cancellable ? "Yes" : "No") : "—"`.

### 3. Tests (`tests/test_control_api.py`)
- Added `test_cancel_completed_job_returns_false_and_preserves_status` and `test_cancel_completed_gravity_job_preserves_success`.

### 4. Docs (`docs/architecture/webapp-and-gui.md`)
- Documented cancellation preservation contract on completed jobs.
