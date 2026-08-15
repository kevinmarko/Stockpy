# Task: Background Job Status Lifecycle & Post-Completion Cancellation Fix

- [x] Investigate root cause of job status payload and cancellation race in `api/_jobs.py` <!-- id: 0 -->
- [x] Guard `JobManager.cancel_job` to return `False` and avoid overwriting `rec.cancelled = True` if `not rec.handle.is_running()` <!-- id: 1 -->
- [x] Refactor `job_status()` evaluation order to prioritize `cancelled` flag while preserving terminal exit codes <!-- id: 2 -->
- [x] Update `webapp/src/screens/Console.tsx` to render `"—"` for terminal jobs in Cancellable column <!-- id: 3 -->
- [x] Update `webapp/src/api/mock.ts` to mirror live backend cancellation semantics for completed jobs <!-- id: 4 -->
- [x] Update `Console.tsx` polling effect to poll all in-flight jobs in `jobHistory` <!-- id: 5 -->
- [x] Add informative cancellation toast feedback when job already finished prior to cancel request <!-- id: 6 -->
- [x] Update cancel button visibility condition in `Console.tsx` to hide for terminal jobs <!-- id: 7 -->
- [x] Implement double-cancel idempotency and clean-exit race handling in `api/_jobs.py` <!-- id: 8 -->
- [x] Add automated test coverage in `tests/test_control_api.py` across `pytest`, `gravity`, `preflight`, and `validation` <!-- id: 9 -->
- [x] Add comprehensive Vitest unit tests in `webapp/src/screens/Console.test.tsx` (12 tests passing) <!-- id: 10 -->
- [x] Verify webapp typecheck passes (`npm run --prefix webapp typecheck`) <!-- id: 11 -->
- [x] Perform live browser QA verification via Chrome DevTools MCP on `http://localhost:5173/console` <!-- id: 12 -->
- [x] Update documentation in `docs/architecture/webapp-and-gui.md` <!-- id: 13 -->
- [x] Complete walkthrough, implementation plan, and task docs in `.claude/` <!-- id: 14 -->
