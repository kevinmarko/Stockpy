# Fix reverse-SDE sign error in `validation/synthetic_diffusion_engine.py`

## Context

`validation/synthetic_diffusion_engine.py` implements a score-based generative
diffusion model (OU forward SDE `dx = -x dτ + √2 dW`) used by the live
`POST /pilots/options/ai/diffusion-stress-test` endpoint (`api/pilots_api.py`)
to generate synthetic crisis price paths and report VaR95/CVaR95/VaR99/CVaR99
tail-risk figures to operators. Training is correct (verified: marginal
`p(x_τ|x_0)=N(x0·e^-τ, 1-e^-2τ)`, score target `-z/std` — textbook
denoising score matching).

Generation is not. Both sampling entry points integrate the reverse-time SDE
with the wrong sign:

```python
# Reverse SDE for OU: dx = [-x - 2 * score] dt + sqrt(2) dW
drift = -x - 2.0 * score
```

Anderson (1982)'s reverse-time SDE for forward `dx = f(x,τ)dτ + g(τ)dW` is
`dx = [f - g²·score]dτ̄ + g·dW̄`, where `dτ̄` is a **negative** infinitesimal
(time runs backward). The code's loop decreases `tau` each iteration but
takes a **positive** step (`x = x + drift*dt + ...`), i.e. it discretizes
with `dτ̄ = -dt`. Substituting flips the bracketed term's sign: for this
process (`f=-x, g²=2`), the correct drift is `x + 2·score`, not `-x - 2·score`.

This was independently verified three ways (by the reporter, and reproduced
independently by a validation pass in this planning session with matching
numbers): a hand-derivation, an analytic-score isolation test (known
`N(5,1)` target recovers `mean≈5.0, var≈1.0` under the fix vs. `mean≈-25 to
-29, var≈770-800` under the bug), and a live-pipeline repro training on real
daily-return-scale data. The bug is real, reproducible, and shipped
undetected because every existing generation test only checks shape/NaN/Inf,
never distributional correctness — this is the actual gap being closed.

**Important, disclosed finding from the validation pass**: fixing the sign
does *not* fully resolve the endpoint's downstream "VaR/CVaR saturates
near 100% of spot" symptom at the endpoint's literal production
hyperparameters (`epochs=15, steps=100, dt=1/252` → `tau_max≈0.4`). At those
exact settings, generated return std drops from ~2.38 (238%, buggy) to
~1.41 (141%, fixed) — mathematically necessary and a real improvement, but
still ~120x the true ~1.2% training scale. Root cause is separate and
pre-existing: `tau_max≈0.4` is far short of true OU stationarity (true
`var≈0.55` there, not 1) and 15 epochs is not enough for this small MLP to
correct for the injected Wiener noise at that budget — confirmed this isn't
simply "more integration steps" (`dt=0.01, steps=100` → `tau_max=1.0` made
it *worse*, since more raw noise gets injected than the undertrained score
network can reel back in). This is a genuine, separate calibration/undertraining
gap, not something this PR should silently paper over or claim to fix.
**Scope decision: fix the sign bug (the acute, three-ways-verified,
release-blocking issue), disclose the calibration gap explicitly and
honestly in the known-issues writeup and PR, and leave it as a named
follow-up** — attempting to also fix it here would require its own
calibration study (epochs/tau_max/steps/network-capacity tradeoffs) that
is out of scope for a focused sign-error fix.

**Scope decision on the optional seed parameter (item 5 of the original
report)**: deferring to a separate follow-up PR. It's an orthogonal
concurrency/reproducibility improvement with no correctness relationship to
the sign bug, and the reporter's own framing ("lower priority, worth doing
if convenient") grants discretion here. Bundling it would touch
`webapp/src/api/types.ts`/`mock.ts` for zero benefit to the actual fix and
adds surface area to what should be a small, fast-reviewable, severity-driven
PR.

## Approach

### Stage 1 — Core sign fix (`validation/synthetic_diffusion_engine.py`)

Extract the one-line drift formula into a single new module-level helper so
both call sites share one source of truth (this is the structural fix that
prevents the two-sites-drift-apart failure mode that let the bug ship):

```python
def _reverse_sde_drift(x: np.ndarray, score: np.ndarray) -> np.ndarray:
    """Drift term for Euler-Maruyama discretization of the reverse-time OU
    SDE. Forward process: dx = -x dτ + sqrt(2) dW. Anderson (1982)'s
    reverse-time SDE is dx = [f - g^2*score] dτ̄ + g dW̄, where dτ̄ is a
    NEGATIVE infinitesimal (time runs backward). This loop discretizes with
    a POSITIVE step dt while tau itself decreases each iteration, i.e.
    dτ̄ = -dt; substituting flips the bracketed term's sign:
        x_{i+1} = x_i + [-f(x_i) + g^2*score(x_i)] * dt + g*sqrt(dt)*z
    For f(x)=-x, g^2=2: -f + g^2*score = x + 2*score.
    """
    return x + 2.0 * score
```

- Replace `drift = -x - 2.0 * score` at line ~362 (`generate_synthetic_crash_paths`)
  and line ~461 (`generate_guided_crisis_paths`) with `drift = _reverse_sde_drift(x, score)`.
- Fix the now-wrong comments/docstring describing the formula: line ~361
  comment, the `generate_guided_crisis_paths` docstring's "Reverse SDE
  integration:" block (line ~385-386 — **not** line 383's CFG
  score-combination formula, which is correct and unrelated), and the
  line ~460 comment.

### Stage 2 — Regression tests (`tests/test_synthetic_diffusion_engine.py`)

Add the missing distributional/sign-correctness test class (confirmed
entirely absent today — existing generation tests check shape/NaN/Inf/
monotonicity-direction only):

1. **Analytic-score recovery test** — bypass the trained network. Use the
   conjugate case `x_0 ~ N(5,1)` (so `p_τ(x) = N(5e^-τ, 1)` for all τ,
   analytic score `-(x - 5e^-τ)`), call the real `_reverse_sde_drift` helper
   directly inside a hand-rolled loop mirroring the production
   discretization (`tau = max(tau_max - i*dt, 1e-3)`, `steps=300, dt=0.01`
   → `tau_max=3.0`, starting `x ~ N(0,I)`). Assert recovered empirical
   mean/std land close to (5, 1) — tight tolerance is legitimate here since
   there's no training noise (validated in the planning pass: recovers
   ≈4.97/1.005 with a fixed seed).
2. **Unconditional full-pipeline test** — real `train_diffusion_model` +
   `generate_synthetic_crash_paths` on data drawn from a known Gaussian.
   Use parameters in the *proven-reliable* range confirmed during
   validation (`data scale ~0.01-0.05`, `epochs≈300`, `steps=50, dt=0.01`
   → `tau_max=0.5`) — **not** the endpoint's literal `epochs=15, dt=1/252`
   combination, which the validation pass confirmed doesn't converge within
   a reasonable tolerance for reasons unrelated to the sign bug (see the
   disclosed calibration gap above). Assert generated mean/std land within
   a tolerance that clearly separates correct from buggy (the bug is off by
   orders of magnitude, so even a generous tolerance works and avoids
   flakiness).
3. **Conditional/guided full-pipeline test** — real
   `train_conditional_diffusion_model` + `generate_guided_crisis_paths` for
   a genuinely negative-mean "crash" class (this is the actual code path
   the live endpoint calls). Same parameter-range guidance as #2.
4. **Fix `test_classifier_free_guidance_monotonicity`** (pre-existing test,
   confirmed to fail post-fix as currently written): its `paths_c1`
   ("vol_shock") fixture is defined with a **positive** mean
   (`randn(N,L)*0.05 + 0.10`) despite being commented as a crash regime —
   the old buggy sign accidentally passed the assertion via pathological
   blow-up, not real signal. Flip to `randn(N,L)*0.05 - 0.10` (a genuine
   negative-mean crash) and correct the stale comment to match; verified in
   the planning pass this restores strict CVaR/VaR monotonicity across
   `w ∈ {0,1,2,3}` under the corrected sign.

Verify each new test would have failed against the pre-fix code (stash the
Stage 1 change, confirm red, unstash, confirm green) — these need to be real
regression guards, not vacuous.

### Stage 3 — Endpoint VaR/CVaR-vs-paths consistency fix (`api/pilots_api.py`)

Separate, independent inconsistency (not caused by the sign bug): in
`post_diffusion_stress_test` (~line 7194), `paths` (returned to the client,
~line 7282) are built via `_clip_and_compound_diffusion_path`, which clips
each step to `[-0.5, 2.0]` before compounding — but VaR/CVaR (~line 7291)
are computed via `compute_diffusion_var(synthetic_returns, ...)` on the
**raw, unclipped** array. Fix:

- Add module-level constants `_DIFFUSION_PATH_MIN_STEP = -0.5` /
  `_DIFFUSION_PATH_MAX_STEP = 2.0` near `_clip_and_compound_diffusion_path`
  (~line 6969); have its `min_step`/`max_step` defaults reference them
  instead of repeating the literals.
- In the endpoint, clip `synthetic_returns` once
  (`clipped_returns = np.clip(synthetic_returns, _DIFFUSION_PATH_MIN_STEP, _DIFFUSION_PATH_MAX_STEP)`)
  before both building `paths` and calling `compute_diffusion_var` twice —
  both consumers now read the same data. Re-clipping inside
  `_clip_and_compound_diffusion_path` is idempotent, so no behavior change
  there.

Confirmed safe against `tests/test_pilots_api.py::TestDiffusionPriceBoundAndVarUnitFix`
(tests the pure helpers directly, contracts unchanged) and
`test_var_cvar_never_reach_or_exceed_spot_price_end_to_end` (bounds only
tighten).

After Stages 1+3, manually re-run the endpoint's real code path (e.g. via a
small script instantiating real training + generation at the endpoint's
actual hyperparameters against real historical bars, or via the FastAPI
endpoint itself) and record the actual post-fix VaR/CVaR figures honestly —
do not claim the downstream saturation symptom is "fixed" without checking;
report what's actually observed (expected: much less saturated but likely
still elevated, per the disclosed calibration gap above).

### Stage 4 — Doc fix (`docs/architecture/ml-and-reports.md`)

Line 29's LaTeX formula in the `synthetic_diffusion_engine.py` bullet
restates the same wrong sign
(`\left[-X_t - 2 \tilde{s}_\theta(...)\right]`) — flip to
`\left[X_t + 2 \tilde{s}_\theta(...)\right]`.

### Stage 5 — Known-issue writeup + index + CLAUDE.md

- New `docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md`
  following this repo's established convention (matched against
  `docs/known_issues/hmm_regime_state_mislabeling_spherical_tied.md`): H1
  title, bold `**Status: Fixed and verified (2026-08-24).**` line,
  `## Summary`, `## Root cause` (the Anderson-1982 / `dτ̄=-dt` derivation,
  with a `### Reachability` subsection naming the live endpoint as the sole
  production caller), `### Also found while root-causing` (the VaR/CVaR
  clip-consistency issue, Stage 3), a clearly separated section honestly
  disclosing the calibration/undertraining gap found during verification
  (NOT fixed by this PR — named as a follow-up), `## Fix` bullets, `##
  Verification` (exact pytest commands + what each new/changed test
  proves), `## Related` cross-links.
- Add a row to `docs/known_issues/README.md`'s index table.
- Add a CLAUDE.md bullet near the existing Phase 31-36/diffusion-engine
  bullets summarizing the fix and the disclosed follow-up (the
  `sync_agent_docs.sh` hook mirrors this to `AGENTS.md` automatically on
  edit).
- Consider flagging the calibration/undertraining gap as a spawned
  follow-up task (`spawn_task`) so it isn't lost after this PR merges.

### Stage 6 — Workflow (per CLAUDE.md)

- `git checkout -b fix-diffusion-reverse-sde-sign` (current branch
  `claude/lucid-albattani-cdc97b` is presently identical to `main`'s tip —
  confirmed 0 commits ahead — so branching now is clean).
- Commit implementation-plan/task-tracker/walkthrough artifacts under
  `.claude/diffusion_reverse_sde_sign_fix_*` (unique task-scoped prefix,
  not bare `plan.md`/`task.md`).
- Run the full verification below, then open a PR whose description states
  plainly: what was fixed (sign bug + VaR/CVaR consistency), what was
  verified (the three-part methodology), and the disclosed-but-not-fixed
  calibration gap as a named follow-up.

## Critical files

- `validation/synthetic_diffusion_engine.py` — the core fix (Stage 1)
- `tests/test_synthetic_diffusion_engine.py` — new regression tests +
  monotonicity-test fixture fix (Stage 2)
- `api/pilots_api.py` — VaR/CVaR-vs-paths consistency fix (Stage 3);
  `DiffusionStressTestRequest`/`post_diffusion_stress_test` at lines
  ~6836-6844 / ~7194-7305, helpers `_clip_and_compound_diffusion_path`/
  `_diffusion_logret_loss_to_dollars` at ~6969-7027
- `docs/architecture/ml-and-reports.md` — doc fix (Stage 4)
- `docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md` (new),
  `docs/known_issues/README.md`, `CLAUDE.md` — writeups (Stage 5)

## Verification

```bash
# Stage 1 sanity check
python3 -c "from validation.synthetic_diffusion_engine import _reverse_sde_drift; import numpy as np; print(_reverse_sde_drift(np.array([1.0]), np.array([2.0])))"  # expect [5.0]

# Stage 2 — full engine test file, including new distributional tests
python3 -m pytest tests/test_synthetic_diffusion_engine.py -v

# Confirm the new tests are real regression guards (fail pre-fix, pass post-fix)
git stash   # stash Stage 1's drift-sign change only, or diff-apply selectively
python3 -m pytest tests/test_synthetic_diffusion_engine.py -v   # expect new tests RED
git stash pop

# Stage 3 — endpoint consistency + existing diffusion-endpoint coverage
python3 -m pytest tests/test_pilots_api.py -k Diffusion -v
python3 -m pytest tests/test_pilots_paper_broker.py -k Diffusion -v

# Full targeted regression sweep before PR
python3 -m pytest tests/test_synthetic_diffusion_engine.py tests/test_pilots_api.py tests/test_pilots_paper_broker.py -v

# Repo-wide offline gate (per CLAUDE.md / verify skill)
# (run via the `verify` skill / make verify-equivalent offline gate)
```

Also manually exercise the live endpoint's real code path post-fix (real
historical bars, the endpoint's actual `epochs=15/steps=100/dt=1/252`
hyperparameters) and record the actual VaR/CVaR figures observed, to report
honestly on the downstream symptom rather than assuming Stage 1 alone
resolves it.
