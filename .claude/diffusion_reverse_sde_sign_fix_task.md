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
      (`api/pilots_api.py`): first attempt (clip once, share the array)
      found to introduce a worse regression (VaR/CVaR going negative on a
      high-variance draw via asymmetric-clip bias), reverted. **Follow-up
      (same day, per operator request): real fix implemented and
      verified.** VaR/CVaR now derived directly from the compounded
      `paths` array's own total simple returns
      (`final_price/spot - 1`) — trivially consistent by construction.
      150-combination seed/regime/confidence-level sweep: zero violations.
      New regression test added:
      `test_var_cvar_computed_from_the_same_paths_returned_to_the_client`.
      Kept the `_DIFFUSION_PATH_MIN_STEP`/`_DIFFUSION_PATH_MAX_STEP` module
      constants from the reverted first attempt (harmless cleanup).
- [x] Stage 3b — Endpoint calibration gap (follow-up, per operator
      request): **partially mitigated, not resolved.** Epoch sweep
      (15→10000) confirmed diminishing returns well short of the true
      training scale — a network-capacity/architecture limit, not an
      epoch-count problem. Shipped a verified, low-risk partial
      improvement: training epochs raised `15→1000` (negligible latency,
      ~0.002s→~0.1-0.2s), measured ~35% std reduction on the underlying
      generated-return distribution, and consistently-improved (though
      still saturated) dollar VaR/CVaR at the endpoint's real
      `num_paths=500`. A genuine fix (architecture/training redesign) is
      explicitly out of scope and left as a disclosed follow-up.
- [x] Stage 4 — Doc fix (`docs/architecture/ml-and-reports.md:29`): LaTeX
      sign corrected.
- [x] Stage 5 — Known-issue writeup + index + CLAUDE.md:
  - [x] `docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md`
        (new, updated same-day with the Stage 3/3b follow-up results)
  - [x] `docs/known_issues/README.md` index row added/updated
  - [x] CLAUDE.md bullet added/updated (auto-mirrored to AGENTS.md by the
        `sync_agent_docs.sh` hook — confirmed identical post-edit)
- [x] Stage 6 — Workflow: branch created; task tracker + plan + walkthrough
      committed under `.claude/`; PR opened (#882); CI offline-suite
      failure (unrelated settings-census staleness) diagnosed and fixed;
      Stage 3/3b follow-up work committed and documented.

## Deviations from the approved plan (all disclosed, not silent)

1. **Item 5 (optional seed parameter)** — still deferred to a follow-up PR
   per the plan's own scope decision (orthogonal to the sign bug,
   reporter's own framing granted discretion). Not implemented in this PR.
2. **Stage 3 (VaR/CVaR-vs-paths consistency)** — the plan's first attempt
   was reverted as unsafe (as planned), but the operator then explicitly
   asked to pursue a real fix; implemented, verified (150-combination
   sweep), and shipped in a same-day follow-up. Net effect: fully resolved,
   not deferred as the original plan anticipated.
3. **Stage 3b (calibration gap)** — the original plan scoped this out
   entirely as "needs its own calibration study." The operator asked to
   pursue it; a full architecture/training redesign remains out of scope
   (confirmed via an epoch sweep to be a capacity limit, not tunable away),
   but a verified, low-risk partial mitigation (epoch bump) was shipped
   instead of leaving the endpoint untouched.

## Verification run log

- `python3 -m pytest tests/test_synthetic_diffusion_engine.py -v` → 16
  passed (13 pre-existing + 3 new).
- `python3 -m pytest tests/test_pilots_api.py -k Diffusion -v` → 19 passed
  (18 pre-existing/updated + 1 new consistency test).
- `python3 -m pytest tests/test_pilots_paper_broker.py -k Diffusion -v` →
  3 passed.
- `python3 -m pytest tests/test_synthetic_diffusion_engine.py tests/test_pilots_api.py tests/test_pilots_paper_broker.py -q`
  → 628 passed.
- `TestDiffusionStressTest` (the invariant-guarding class) re-run 8
  consecutive times standalone: 8/8 clean, no flakiness.
- 150-combination (15 seeds × 5 regimes × 2 confidence levels) manual sweep
  of the price-path-derived VaR/CVaR approach: 0 violations of
  `0 <= VaR/CVaR < spot`.
- Epoch sweep (15/100/300/600/1000/2000/5000/10000) on two representative
  training-data shapes, confirming diminishing returns and informing the
  `epochs=1000` choice.
- `ruff check` on all changed files (`--select F,E9`): 3 pre-existing
  findings (unrelated unused imports predating this change), zero new
  findings introduced.
- CI: `test (offline suite)` initially failed on an unrelated
  `docs/settings_field_census.md` staleness gate (the Autofix bot partially
  regenerated the `.json` companion but not the `.md`); fixed by re-running
  `scripts/measure_settings_census.py --write` at current HEAD.
