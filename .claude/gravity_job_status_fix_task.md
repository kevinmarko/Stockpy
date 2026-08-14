# Task: Background Job Status Lifecycle & Post-Completion Cancellation Fix

- [x] Investigate root cause of job status payload and cancellation race in `api/_jobs.py` <!-- id: 0 -->
- [x] Guard `JobManager.cancel_job` to return `False` and avoid overwriting `rec.cancelled = True` if `not rec.handle.is_running()` <!-- id: 1 -->
- [x] Update `webapp/src/screens/Console.tsx` to render `"—"` for non-running jobs in Cancellable column <!-- id: 2 -->
- [x] Add automated test coverage in `tests/test_control_api.py` <!-- id: 3 -->
- [x] Verify webapp typecheck and tests pass (`npm run test`, `npm run typecheck`) <!-- id: 4 -->
- [x] Update documentation in `docs/architecture/webapp-and-gui.md` <!-- id: 5 -->
