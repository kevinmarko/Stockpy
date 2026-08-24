# Fix: Brinson-Fachler array-input missing interaction effect + duplicate-sector data loss

## Summary

An audit of the platform's Brinson-Fachler performance attribution math found two
confirmed bugs, both fixed in this PR.

### Fix 1 — missing interaction effect in `EvaluationEngine.calculate_brinson_fachler`'s array-input path (HIGH, live-dashboard impact)

`calculate_brinson_fachler` has two code paths under one name:

- **DataFrame-input branch** (`_calculate_brinson_fachler_compat`, used by the
  Pilots PWA's manual-input calculator) — already correct, verified hand-computed
  to floating-point precision including the `Allocation + Selection + Interaction
  == Active Return` identity.
- **Series/array-input branch** (called internally by `evaluate_portfolio()`,
  the real per-cycle orchestrator pipeline via
  `pipeline/production_steps.py::StrategyEvalStep`) — computed only Allocation
  and Selection effects, **silently omitting Interaction entirely**. This broke
  the identity whenever both weight AND return differed between portfolio and
  benchmark for a sector (the general, common case). The bad output already fed
  the live `BF_Allocation`/`BF_Selection` `config.COLUMN_SCHEMA` dashboard
  columns shown in every daily report.

**Fix**: the array-input branch now also computes
`BF_Interaction = (w_p - w_b) * (R_p - R_b)` (identical formula to the correct
compat path's `interaction_effect`), added as a new `BF_Interaction`
`config.COLUMN_SCHEMA` column (orchestrator-only, alongside its siblings) and
exported by `pipeline/production_steps.py`. `evaluate_portfolio()`'s two skip
branches (zero total position size; missing sector/benchmark data) default it
to `0.0`, matching the existing convention for `BF_Allocation`/`BF_Selection`.

A new regression test asserts the full identity for the array-input path,
bringing its test coverage to parity with the DataFrame path's pre-existing
identity test.

### Fix 2 — duplicate-sector data loss in `pilots/brinson.py` (MODERATE, reachable via the real endpoint)

`pilots/brinson.py` had no duplicate-sector-name validation. Two rows sharing a
sector name make `evaluation_engine.py`'s `pd.merge(..., on="sector",
how="outer")` produce a Cartesian-product row set for that sector, which a
downstream `{row["sector"]: {...}}` dict comprehension then silently collapses
via dict-key collision — real per-sector data loss in `POST
/portfolio/attribution/brinson-fachler`'s `Sector Details` response (aggregate
totals stay correct since that math is linear; only the per-sector breakdown
is corrupted).

**Fix**: `pilots/brinson.py::compute_brinson_fachler` now hard-rejects (raises
`ValueError` → HTTP 422) a request whose rows contain a duplicate sector name,
via a new `_find_duplicate_sectors` helper — checked BEFORE the shared engine
is ever called, rather than fixing the engine's own merge (out of scope: shared
engine, other callers depend on it, including the legacy/frozen
`gui/report_viewer_helpers.py` calculator, which carries the identical latent
bug). `validate_brinson_fachler_rows` also surfaces the same condition as an
informational warning for any other direct caller.
`webapp/src/api/mock.ts::mockComputeBrinsonFachler` mirrors the same reject for
mock/live parity (that mock reproduces the real math, not a canned fixture, per
its own header comment) — confirmed via a new component-level test that
exercises the real mock (not a stubbed error) through a paste-duplicate-sector
flow.

## Files changed

- `evaluation_engine.py` — `calculate_brinson_fachler` array-input branch,
  `evaluate_portfolio()`'s three BF-column-populating branches, `__main__` demo.
- `config.py` — new `BF_Interaction` `COLUMN_SCHEMA` entry.
- `pipeline/production_steps.py` — `export_keys` list.
- `pilots/brinson.py` — `_find_duplicate_sectors`, wired into
  `validate_brinson_fachler_rows` and `compute_brinson_fachler`.
- `webapp/src/api/mock.ts` — `mockDuplicateSectors`, wired into
  `mockValidateBrinsonFachlerRows` and `mockComputeBrinsonFachler`.
- Tests: `tests/test_evaluation_engine.py`, `tests/test_config.py`,
  `tests/test_evaluate_portfolio_zero_positions.py`,
  `tests/test_pilots_attribution_brinson.py`,
  `webapp/src/screens/Attribution.test.tsx`.
- Docs: `docs/architecture/simulation-eval-reporting.md`'s `evaluation_engine.py`
  entry.

## Out of scope (documented follow-ups from the audit, not required for this PR)

- `pilots/brinson.py::_coerce_float`'s NaN-float (not just non-numeric-string)
  coercion-to-zero gap — self-disclosed pre-existing tradeoff, low priority.
- `evaluation_engine.py:371`'s outer-merge `fillna(0.0)` in the DataFrame path —
  currently dormant via `pilots/brinson.py`'s entry point (matching sector sets
  by construction), reachable via other callers
  (`gui/report_viewer_helpers.py`).
- Webapp: explicitly surfacing the `Attribution Sum` vs `Active Return`
  reconciliation check to the operator (currently only logged server-side).

## Verification

- `pytest tests/test_evaluation_engine.py tests/test_config.py
  tests/test_pilots_attribution_brinson.py
  tests/test_evaluate_portfolio_zero_positions.py -q` — all green (103 passed).
- `pytest tests/ -k brinson -q` — 44 passed.
- `pytest tests/test_state_snapshot_parity.py
  tests/test_production_steps_edge_ratio_notes.py
  tests/test_quantitative_models.py -q` — 55 passed (consumers of
  `evaluate_portfolio`/`COLUMN_SCHEMA` unaffected).
- Full offline suite: `pytest -m "not network and not slow" -q` — green (see PR
  CI run / session transcript).
- `python3 -m ruff check <changed files> --select=F821,F822,F823,E9` — clean.
- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run src/screens/Attribution.test.tsx` — 20 passed, including the
  new real-mock duplicate-sector test.
- Hand-verified arithmetic: for the existing Tech/Energy fixture
  (`w_p=[0.6,0.4]`, `w_b=[0.5,0.5]`, `r_p=[0.08,0.03]`, `r_b=[0.05,0.02]`):
  Allocation=0.003, Selection=0.02, Interaction=0.002, sum=0.025 == Active
  Return (0.025).
