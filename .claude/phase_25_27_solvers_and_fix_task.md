# Task Tracker: Phase 25-27 Solvers & FIX Sequence Recovery

- [x] **Agent 1 (Math & Risk Verification)**:
  - [x] Audit `test_hrp_cvar_optimizer.py` (tree clustering, singular covariance guards, zero-variance clipping, CVaR boundary constraints).
  * [x] Audit `test_almgren_chriss_router.py` (temporary $\eta$, permanent $\gamma$, volatility $\sigma$, $\kappa = \sqrt{\lambda \sigma^2 / \eta}$ decay rate, front-loading verification).
- [x] **Agent 2 (Execution Infrastructure)**:
  - [x] Implement `execution/fix_recovery.py` (`FixSessionRecovery`, `ResendRequest`, `SequenceReset`, gap fill mode).
  - [x] Implement `tests/test_fix_recovery.py` (logging, resend handling, sequence increment success, sequence decrement rejection).
  - [x] Verify `tests/test_fix_gateway.py` (state machine, PossDup deduplication, OrderCancelReject restoration).
- [x] **Agent 3 (Review & Commit)**:
  - [x] Compile all test results.
  - [x] Ensure 0 TypeScript compilation errors (`tsc --noEmit`).
  - [x] Ensure 100% Python Pytest pass rate.
  - [x] Synchronize unique artifacts to `.claude/`.
