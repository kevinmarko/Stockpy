# Task: fix-ast-triage-pbo-gate-tests

## Objective
Triage the AST auditor's latest findings, add a regression test pinning the
PBO-overrides-DSR deployability precedence, and — found in the course of
reviewing `validation/covariate_drift.py` — stop `compute_psi` from
fabricating a `0.0` PSI (CONSTRAINT #4) for cases where PSI cannot actually
be computed.

## Checklist
- [x] Run `scripts/auditor/stockpy_codebase_auditor.py`, triage all 8 LOW
      findings (no CRITICAL/HIGH/MEDIUM).
- [x] Fix the 2 real bugs (missing module docstrings:
      `scripts/launch_mcp_builder_agents.py`, `validation/covariate_drift.py`).
- [x] Annotate the 6 false-positive/documented-gap findings in place
      (`cli_introspect/__init__.py`, `gui/panels/__init__.py`,
      `execution/receipts_store.py`, `validation/walk_forward.py`,
      `execution/overnight_guardrails.py`, `ml/drl_market_maker_ppo.py`).
- [x] Add `test_high_dsr_high_pbo_still_not_deployable` to
      `tests/test_registry_load.py`, pinning
      `compute_deployable(dsr=1.00, pbo=0.73) is False`.
- [x] `compute_psi`'s three fallback branches (empty input, degenerate
      bucket edges, exception handler) return `NaN` instead of `0.0` —
      never fabricate "confirmed no drift."
- [x] `check_and_alert_feature_drift` fails closed on a NaN PSI (reports
      `psi=None, details="PSI computation failed"`, no alert) instead of
      falling through to "within normal range."
- [x] Tests updated/added for both `compute_psi` and
      `check_and_alert_feature_drift` NaN paths.
- [x] Rebased onto `origin/main` (15 unrelated upstream commits, no
      overlapping files).
- [x] Full targeted test run green: `tests/test_covariate_drift.py`,
      `tests/test_registry_load.py`, `tests/test_pbo.py`, `tests/test_dsr.py`,
      `tests/test_family_deployable.py`, `tests/test_receipts_store.py`,
      `tests/test_walk_forward.py` — 71/71 passed.
- [x] PR artifacts committed under this branch's slug.
