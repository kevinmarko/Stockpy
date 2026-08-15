# Implementation Plan: Audit Phase 25-27 Solvers & FIX Sequence Recovery

## Executive Summary
This document establishes the audit and verification plan for **Phases 25–27** solvers, convex optimization boundaries, Almgren-Chriss liquidation trajectories, and FIX 4.4 state machine sequence recovery.

## 3-Agent Workstream Partition
1. **Agent 1 (Math & Risk Verification)**:
   - Audit portfolio optimization models in `test_hrp_cvar_optimizer.py`.
   - Ensure Hierarchical Risk Parity (HRP) tree clustering does not produce singular matrix errors under zero/degenerate variance or collinear assets.
   - Validate $CVaR_\alpha$ boundary constraints.
   - Verify Almgren-Chriss router equations in `test_almgren_chriss_router.py`: optimal trajectory balancing temporary ($\eta$) and permanent ($\gamma$) market impact against volatility ($\sigma$) timing risk.
   - Verify that increasing risk aversion $\lambda$ raises decay rate $\kappa = \sqrt{\lambda \sigma^2 / \eta}$ and front-loads trades.

2. **Agent 2 (Execution Infrastructure)**:
   - Implement `execution/fix_recovery.py` with `FixSessionRecovery`, `ResendRequest`, and `SequenceReset`.
   - Ensure Resend Request (Tag 35=2) triggers Sequence Reset (Tag 35=4) with GapFillFlag (Tag 123="Y").
   - Ensure Sequence Reset can only increase sequence numbers, strictly rejecting decreases.
   - Implement tests in `tests/test_fix_recovery.py` and verify `tests/test_fix_gateway.py`.

3. **Agent 3 (Review & Commit)**:
   - Verify zero TypeScript compiler errors (`npm run --prefix webapp typecheck`).
   - Verify 100% Pytest pass rate across all solver, gateway, and recovery test suites.
   - Synchronize unique branch-scoped artifacts to `.claude/`.
