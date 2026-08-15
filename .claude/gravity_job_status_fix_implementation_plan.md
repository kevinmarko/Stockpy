# Background Job Status Lifecycle & Post-Completion Cancellation Fix

## Overview
When background jobs (such as the Gravity AI Review Suite `gravity`, `pytest`, `validation`, or `advisory`) finish executing, their status is evaluated based on the process exit code (`success` or `failed`). Several subtle lifecycle issues were identified and resolved across the backend, frontend, mock data, and test harness:

1. **Post-Completion Cancellation Race & Status Overwrite**: If a cancellation request (`POST /jobs/{job_id}/cancel`) arrived after a job had already completed (or while it was exiting), `stop_run()` returned `True` (since `poll()` was not None), setting `rec.cancelled = True` and permanently overwriting the job's terminal status from `"success"` or `"failed"` to `"cancelled"`.
2. **`job_status()` Lifecycle Resolution Order**: `job_status()` evaluated `is_running()` before `cancelled`, which could cause inconsistencies if flags were read concurrently during state transitions.
3. **Ambiguous `cancellable` Table Display**: In `Console.tsx`, the Recent Jobs table rendered `Cancellable: Yes` for completed/dead jobs because `rec.cancellable` reflected the backend capability (subprocess vs. daemon) rather than active lifecycle state.
4. **Mock API Parity**: `mockApi.cancelJob` unconditionally set `cancelled = true` without checking if the mock job had already finished.
5. **In-Flight Polling for Superseded Jobs**: `Console.tsx` only polled the single `activeJob`, meaning background jobs launched prior to the latest job would stay frozen in their initial status in the Job History table.
6. **Cancellation Feedback**: Cancel response did not distinguish between a job that was already completed versus one that failed to cancel while running.
7. **Cancel Button Visibility**: Cancel button in the header remained interactive even if `activeJob` transitioned to a terminal state.
8. **Double-Cancellation Idempotency**: Calling cancel multiple times on an already cancelled job did not cleanly return `True`.
9. **Clean-Exit Race Condition**: If a process completed with `returncode == 0` during the cancellation handshake, it was improperly marked cancelled.
10. **Multi-Job Launcher Test Coverage**: Tests only checked `pytest` and `gravity` rather than the full set of launchers (`preflight`, `validation`, etc.).

---

## Architectural Changes

### 1. Backend (`api/_jobs.py`)
- In `JobManager.cancel_job(job_id)`:
  - Guard check 1: `if rec.cancelled: return True` (idempotent double-cancel).
  - Guard check 2: `if not rec.handle.is_running(): return False` (rejects cancellation of terminated jobs).
  - Guard check 3: If `rec.handle.returncode() == 0` after `stop_run`, return `False` to preserve clean exit status.
  - Only when process was genuinely stopped while running and non-zero rc, mark `rec.cancelled = True` and return `True`.
- In `job_status(handle, *, cancelled)`:
  - Evaluate `cancelled` before checking `is_running()`.
  - Fall through to process exit code (`rc == 0 -> "success"`, `rc != 0 -> "failed"`) when `cancelled` is false.

### 2. Frontend (`webapp/src/screens/Console.tsx`)
- Updated `JOB_COLUMNS` Cancellable column:
  `render: (row) => (!TERMINAL_STATUSES.has(row.status) ? (row.cancellable ? "Yes" : "No") : "—")`
- Polling effect polls all in-flight jobs in `jobHistory` (`jobHistory.filter(j => !TERMINAL_STATUSES.has(j.status))`) via `Promise.allSettled()`.
- Cancel button conditionally hidden when `TERMINAL_STATUSES.has(activeJob.status)`.
- Handled `{ cancelled: false }` response by fetching fresh status and displaying informative toast when job was already finished.

### 3. Mock API (`webapp/src/api/mock.ts`)
- In `mockApi.cancelJob`: checked `Date.now() - job.startedAt < 2000` to simulate running state; returns `{ cancelled: false }` for completed jobs.

### 4. Automated Tests (`tests/test_control_api.py`, `webapp/src/screens/Console.test.tsx`)
- Parameterized backend tests across `pytest`, `gravity`, `preflight`, and `validation`.
- Added tests for double-cancel idempotency and race condition preservation.
- Added 12 Vitest unit tests in `Console.test.tsx` verifying UI rendering, polling, toast handling, and table display.

### 5. Documentation (`docs/architecture/webapp-and-gui.md`)
- Documented job status lifecycle guarantees, non-cancellable daemon runs, and terminal status preservation.
