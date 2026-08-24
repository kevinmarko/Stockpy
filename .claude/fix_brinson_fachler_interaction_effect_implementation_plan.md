# Fix Brinson-Fachler array-input missing interaction effect + duplicate-sector data loss

## Context

An audit of the platform's Brinson-Fachler performance attribution math found the
DataFrame/compat path (`EvaluationEngine._calculate_brinson_fachler_compat`, used by
the Pilots PWA's manual-input calculator) is correct — verified to floating-point
precision, including the sum-to-active-return identity. But the same class's
same-named `calculate_brinson_fachler` has a **second, genuinely broken code path**:
the Series/array-input branch (used internally by `evaluate_portfolio()`, the real
per-cycle orchestrator pipeline) computes only Allocation and Selection effects —
**it silently drops the Interaction effect entirely**. This breaks the fundamental
Brinson-Fachler identity (`Allocation + Selection + Interaction == Active Return`)
whenever both weight AND return differ between portfolio and benchmark for a sector
(the general, common case). The bad output already lands in two live
`config.COLUMN_SCHEMA` dashboard columns (`BF_Allocation`/`BF_Selection`) shown in
every daily report and the operator dashboard — this is not a dead code path.

A second, independently-confirmed bug: the manual-input calculator's
`pilots/brinson.py` has no duplicate-sector-name validation. Two rows sharing a
sector name make `evaluation_engine.py`'s merge-on-"sector" produce a Cartesian
product for that sector, which a downstream dict comprehension then silently
collapses via key collision — real per-sector data loss in the API response's
`Sector Details`, reachable through the real `POST
/portfolio/attribution/brinson-fachler` endpoint (aggregate totals stay correct;
only the per-sector breakdown is corrupted).

This plan fixes both (item 1 required, item 2 included since it's small and
contained) and leaves the audit's lower-priority items (NaN literal-float
coercion tightening, the engine's own separate `fillna(0.0)` in a currently-dormant
call path, and a webapp reconciliation-display nicety) as documented follow-ups,
per the audit's own stated priority.

## Fix 1 — add the missing interaction effect to the array-input branch

**`evaluation_engine.py`**

- `calculate_brinson_fachler` (array-input branch, ~line 285-299): add
  `df['BF_Interaction'] = (df['w_p'] - df['w_b']) * (df['R_p'] - df['R_b'])`
  (identical formula to the correct DataFrame/compat path's `interaction_effect` at
  line 381) and include it in the returned `df[[...]]` selection.
- `evaluate_portfolio` (~line 695-726): the three branches that populate
  `df['BF_Allocation']`/`df['BF_Selection']` each need a parallel
  `df['BF_Interaction']` line — the real compute branch maps
  `bf_df['BF_Interaction']` the same way as the other two; the zero-position-skip
  branch and the missing-sector/benchmark branch both default it to `0.0`,
  matching the existing convention for the other two columns exactly.
- `__main__` demo print block (~line 1304): add `'BF_Interaction'` to the printed
  column list for consistency (cosmetic, not required).

**`config.py`** — add a new `COLUMN_SCHEMA` entry immediately after the existing
`BF_Selection` entry (~line 154):
`{"header": "BF Interaction Effect", "key": "BF_Interaction", "format": "number"}`

**`pipeline/production_steps.py`** (~line 2604) — add `'BF_Interaction'` to the
`export_keys` list alongside `BF_Allocation`/`BF_Selection` so it gets the same
`fillna(0.0)`-when-present / `0.0`-when-absent treatment as its siblings.

### Tests

- `tests/test_evaluation_engine.py::TestBrinsonFachler`:
  - Update `test_series_path_returns_bf_dataframe`'s column-list assertion to
    include `"BF_Interaction"`.
  - Extend `test_series_path_known_arithmetic` with the hand-computed
    `BF_Interaction` value for the existing fixture.
  - **New test**: assert the full identity
    `(BF_Allocation + BF_Selection + BF_Interaction).sum() == (R_p_total - R_b_total)`
    for the array-input path — this is the load-bearing check the audit calls out
    as missing; the DataFrame path already has its own equivalent
    (`test_dataframe_path_attribution_sum_matches_active_return`) so this brings
    parity between the two paths' test coverage.
- Extend `tests/test_evaluate_portfolio_zero_positions.py` (and/or
  `Gravity AI Review Suite.py`'s step 45 zero-position-skip check) to also assert
  `BF_Interaction == 0.0` in the skip branches, mirroring the existing
  `BF_Allocation`/`BF_Selection` assertions — only if touching that file is cheap;
  otherwise a dedicated assertion in the new evaluation_engine test suffices.

### Schema-count regression tests to update in the same commit

`tests/test_config.py::TestColumnSchemaIntegrity`/`TestAdvisoryColumnCoverage` pin
exact counts that a new `COLUMN_SCHEMA` entry breaks by design (the test file's own
docstring says to update these deliberately in the same commit, not loosen them):
- `EXPECTED_COLUMN_COUNT`: `114` → `115`
- `KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS`: add `"BF_Interaction"` right next to
  the existing `"BF_Allocation", "BF_Selection"` entry (orchestrator-only, same as
  its siblings — the advisory path never populates any BF column)
- `len(unmapped) == 79` → `80`
- `len(mapped) + len(unmapped) == len(config.COLUMN_SCHEMA) == 114` → `115`

(`database_setup.py`'s `DailySignals` table auto-migrates additive `COLUMN_SCHEMA`
columns via `ALTER TABLE` — confirmed via `tests/test_database_setup.py` — so no
migration code is needed.)

## Fix 2 — reject duplicate sector names in the manual-input calculator

**`pilots/brinson.py`**

- Add a small helper, e.g. `_find_duplicate_sectors(rows) -> List[str]`, returning
  the sorted list of sector names (post-strip, non-blank) that appear more than
  once among the input rows.
- In `compute_brinson_fachler`, after the existing blank-sector-name hard-check and
  before `build_brinson_fachler_frames` is called, check for duplicates and
  `raise ValueError(...)` with a clear message naming the offending sector(s) —
  this is a **hard reject** (→ HTTP 422 via the existing
  `except ValueError as exc: raise HTTPException(422, ...)` in
  `api/pilots_api.py::post_brinson_fachler_attribution`), not merely an
  informational warning, since letting it through would still hit the underlying
  Cartesian-product/data-loss bug in `evaluation_engine.py`'s merge. Fixing that
  merge itself is out of scope (shared engine, same "do not touch, other callers
  depend on it" boundary this module's docstring already documents for the sibling
  known-limitation).
- Add the same duplicate check to `validate_brinson_fachler_rows`'s returned
  warnings list too, for informational/defense-in-depth visibility for any other
  caller of that function directly.

**`webapp/src/api/mock.ts`** — `mockComputeBrinsonFachler` mirrors the real Python
math per its own header comment ("keep in sync with... if either changes"), and
reproduces the identical dict-key-collision bug via
`sectorDetails[s.sector] = {...}`. Add the matching duplicate-sector check,
throwing `ApiError(..., 422)` with a comparable message, so mock/live parity holds
for this new behavior. Also mirror the check in the informational
`mockValidateBrinsonFachlerRows` for consistency with `validate_brinson_fachler_rows`.

No webapp component changes are needed beyond the mock: `Attribution.tsx` already
renders any `mutation.error` message inline (`data-testid="brinson-error"`), so the
new 422 detail message surfaces to the operator automatically.

### Tests

- `tests/test_pilots_attribution_brinson.py`:
  - New `TestValidateRows` case: duplicate sector names produce a warning.
  - New `TestComputeBrinsonFachler`/`TestBrinsonFachlerEndpoint` cases: a rows list
    with a repeated sector name raises `ValueError` from `compute_brinson_fachler`
    directly, and the `POST /portfolio/attribution/brinson-fachler` endpoint
    returns 422 with a message naming the sector.
- `webapp/src/screens/Attribution.test.tsx` (or wherever the mock-backed component
  tests live) — a matching case if a duplicate-sector fixture is easy to drive
  through the existing test harness; otherwise a focused unit test directly against
  `mockComputeBrinsonFachler`/`mockValidateBrinsonFachlerRows` if those are
  exported/testable in isolation. (Confirm actual test file location during
  implementation.)

## Documentation

- `docs/architecture/simulation-eval-reporting.md`'s `evaluation_engine.py` entry —
  add a short note describing the fix: the array-input `calculate_brinson_fachler`
  path was missing the interaction effect (confirmed live via `BF_Allocation`/
  `BF_Selection` dashboard columns), now includes `BF_Interaction`; and that
  `pilots/brinson.py` now rejects duplicate sector names rather than silently
  losing per-sector data via a merge Cartesian product.
- No `docs/signals/<name>.md` entry applies (not a `SignalModule`/`STRATEGY_REGISTRY`
  strategy) and no `VALIDATION_STRATEGY_FIX_LOG.md` entry applies (not a
  deployability-gate strategy fix) — this is a dashboard-analytics correctness fix,
  not a strategy validation change.

## Branch / PR workflow (per CLAUDE.md)

- Rename/create branch off current (clean, at `main` tip):
  `git checkout -b fix-brinson-fachler-interaction-effect`
- Implement fixes 1 and 2, tests, docs above.
- Run targeted tests: `pytest tests/test_evaluation_engine.py
  tests/test_config.py tests/test_pilots_attribution_brinson.py
  tests/test_evaluate_portfolio_zero_positions.py -q`, then the fuller
  `/verify` gate.
- `npm run --prefix webapp typecheck` after the `mock.ts` change (webapp source
  edit triggers the repo's own `webapp_typecheck.sh` PostToolUse hook
  automatically, but run it explicitly too as a final check).
- Copy the implementation plan/walkthrough into `.claude/` under a branch-scoped
  name (e.g. `.claude/fix_brinson_fachler_interaction_effect_walkthrough.md`) per
  CLAUDE.md's PR-artifact-naming rule, and open a PR.

## Verification

- `pytest tests/test_evaluation_engine.py tests/test_config.py
  tests/test_pilots_attribution_brinson.py
  tests/test_evaluate_portfolio_zero_positions.py -q` — all green, including the
  new identity test for the array path and the new duplicate-sector tests.
- Manually hand-verify the new `test_series_path_...` arithmetic against the
  worked example in the plan (already cross-checked by hand above: Alloc=0.003,
  Select=0.02, Interaction=0.002, sum=0.025=Active Return for the existing
  Tech/Energy fixture).
- `python3 -m pytest tests/ -k brinson -q` as a final sweep for anything else
  touching this surface.
- `npm run --prefix webapp typecheck` clean.
