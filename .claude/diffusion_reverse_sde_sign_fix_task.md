# Task tracker: reverse-SDE sign fix in `validation/synthetic_diffusion_engine.py`

Branch: `fix-diffusion-reverse-sde-sign`. See
`.claude/diffusion_reverse_sde_sign_fix_implementation_plan.md` for the full
approved plan and `docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md`
for the root-cause write-up.

- [x] Stage 1 — Core sign fix (`validation/synthetic_diffusion_engine.py`):
      extracted `_reverse_sde_drift(x, score)` helper, fixed both call sites
      (`generate_synthetic_crash_paths`, `generate_guided_crisis_paths`) and
      their stale comments/docstring. Sanity check:
      `_reverse_sde_drift([1.0], [2.0]) == [5.0]` confirmed.
- [x] Stage 2 — Regression tests (`tests/test_synthetic_diffusion_engine.py`):
  - [x] `test_reverse_sde_drift_recovers_known_gaussian_analytic_score`
  - [x] `test_generate_synthetic_crash_paths_recovers_known_training_distribution`
  - [x] `test_generate_guided_crisis_paths_recovers_known_crash_regime_direction`
  - [x] Fixed `test_classifier_free_guidance_monotonicity`'s fabricated
        positive-mean "vol_shock" fixture (flipped to a genuine negative
        mean); verified it fails pre-fix, passes post-fix.
  - [x] Confirmed all 4 above fail against a deliberately-reintroduced sign
        bug (temporarily flipped `_reverse_sde_drift`'s return value) —
        real regression guards, not vacuous.
- [x] Stage 3 — Endpoint VaR/CVaR-vs-paths consistency
      (`api/pilots_api.py`): attempted, found to introduce a worse
      regression (VaR/CVaR going negative on a high-variance draw via
      asymmetric-clip bias), **reverted**. Kept the harmless
      `_DIFFUSION_PATH_MIN_STEP`/`_DIFFUSION_PATH_MAX_STEP` module
      constants (single-source-of-truth cleanup, no behavior change).
      Documented as a deferred follow-up, not silently dropped.
- [x] Stage 4 — Doc fix (`docs/architecture/ml-and-reports.md:29`): LaTeX
      sign corrected.
- [x] Stage 5 — Known-issue writeup + index + CLAUDE.md:
  - [x] `docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md`
        (new)
  - [x] `docs/known_issues/README.md` index row added
  - [x] CLAUDE.md bullet added (auto-mirrored to AGENTS.md by the
        `sync_agent_docs.sh` hook — confirmed identical post-edit)
- [ ] Stage 6 — Workflow: branch created; this task tracker + plan +
      walkthrough committed under `.claude/`; open PR.

## Deviations from the approved plan (both disclosed, not silent)

1. **Item 5 (optional seed parameter)** — deferred to a follow-up PR per
   the plan's own scope decision (orthogonal to the sign bug, reporter's
   own framing granted discretion). Not implemented in this PR.
2. **Stage 3 (VaR/CVaR-vs-paths consistency)** — attempted per plan,
   empirically found to introduce a new, worse regression, and reverted
   rather than shipped. This is a deviation from "implement the fix" to
   "investigate, find it unsafe, defer" — documented in full in the
   known-issues doc's "Investigated but deferred" section and in the
   CLAUDE.md bullet.

## Verification run log

- `python3 -m pytest tests/test_synthetic_diffusion_engine.py -v` → 16
  passed (13 pre-existing + 3 new).
- `python3 -m pytest tests/test_pilots_api.py -k Diffusion -v` → 18 passed.
- `python3 -m pytest tests/test_pilots_paper_broker.py -k Diffusion -v` →
  3 passed.
- `python3 -m pytest tests/test_synthetic_diffusion_engine.py tests/test_pilots_api.py tests/test_pilots_paper_broker.py -q`
  → 627 passed.
- `ruff check` on all 3 changed files (`--select F,E9`): 3 pre-existing
  findings (unrelated unused imports predating this change), zero new
  findings introduced.
