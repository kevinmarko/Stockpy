# Implementation Plan: fix-ast-triage-pbo-gate-tests

## Scope
1. **AST auditor triage** — run the auditor, fix genuine bugs, document
   false positives in place, never silently dismiss a finding.
2. **PBO/DSR gate regression** — `ml/registry_io.py::compute_deployable` had
   no test exercising it directly; add one that pins the real observed
   cross-sectional-momentum precedence (high DSR does not override high PBO).
3. **`compute_psi` fabricated-metric fix** (CONSTRAINT #4) — found while
   reviewing the covariate-drift module for its auditor docstring finding:
   three fallback branches returned `0.0`, indistinguishable from a real
   "confirmed no drift" measurement:
   - empty `reference`/`current` input
   - `len(bins) < 2` after quantile binning
   - the `except Exception` handler
   These now return `float('nan')`. The single-value/identical-distributions
   "no drift" case (a real measurement) and the `float('inf')` "maximum
   drift" case are unchanged.
4. **Fail-closed follow-up** (CONSTRAINT #6) — `check_and_alert_feature_drift`
   is the one production caller of `compute_psi`, and its own
   `np.isinf(psi) or psi >= PSI_ALERT_THRESHOLD` check does not catch NaN,
   so a NaN PSI would have fallen through to the "within normal range"
   branch — reporting a computation failure as a clean bill of health.
   Added an explicit NaN branch, mirroring the existing "Insufficient data"
   pattern: `PSIResult(psi=None, drift_detected=False, details="PSI
   computation failed")`, no alert fired.

## Files touched
- `validation/covariate_drift.py` — module docstring (auditor finding),
  `compute_psi` NaN fallbacks, `check_and_alert_feature_drift` fail-closed
  NaN handling.
- `tests/test_covariate_drift.py` — updated empty-series assertion to
  `np.isnan`; added a mocked-NaN test for `check_and_alert_feature_drift`.
- `tests/test_registry_load.py` — new PBO-overrides-DSR regression test.
- `cli_introspect/__init__.py`, `gui/panels/__init__.py`,
  `execution/receipts_store.py`, `validation/walk_forward.py` — explanatory
  comments only, no behavior change.
- `scripts/launch_mcp_builder_agents.py` — module docstring only.

## Documentation-update step
No `CLAUDE.md`/`AGENTS.md`/`docs/architecture/*.md` changes needed — this is
a bugfix + comment/docstring pass within an existing, already-documented
module (`validation/covariate_drift.py`'s own docstring is the
documentation of record for its NaN-vs-fabricated-value contract, and it
was updated in place).

## Verification
- `git fetch origin && git rebase origin/main` — clean, no conflicts (15
  upstream commits, none touching this branch's files).
- `python3 -m pytest tests/test_covariate_drift.py tests/test_registry_load.py
  tests/test_pbo.py tests/test_dsr.py tests/test_family_deployable.py
  tests/test_receipts_store.py tests/test_walk_forward.py -v` — 71 passed,
  0 failed.
