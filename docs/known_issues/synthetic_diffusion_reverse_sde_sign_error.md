# Known issue (2026-08-24): Reverse-time SDE integrated with a sign error in the generative diffusion stress engine

**Status: sign error fixed and verified.** One related item investigated
during the fix and explicitly deferred (not fixed); one pre-existing
calibration gap disclosed but not fixed. Branch
`fix-diffusion-reverse-sde-sign`.

## What happened

`validation/synthetic_diffusion_engine.py` implements score-based
generative diffusion (Song et al. 2021-style) on an Ornstein-Uhlenbeck (OU)
forward process, `dx = -x dτ + √2 dW`. Training
(`train_diffusion_model`/`train_conditional_diffusion_model`) is correct —
the marginal `p(x_τ|x_0) = N(x0·e^-τ, 1-e^-2τ)` and the denoising
score-matching target `-z/std` are textbook-correct and were not touched by
this fix.

Both generation entry points — `generate_synthetic_crash_paths` and
`generate_guided_crisis_paths` (the latter is the only one the live
endpoint calls) — integrated the reverse-time SDE with the wrong sign:

```python
# Reverse SDE for OU: dx = [-x - 2 * score] dt + sqrt(2) dW
drift = -x - 2.0 * score
```

Anderson (1982)'s reverse-time SDE for a forward process
`dx = f(x,τ)dτ + g(τ)dW` is `dx = [f - g²·score]dτ̄ + g·dW̄`, where `dτ̄` is a
**negative** infinitesimal — time runs backward. Both loops decrease `tau`
each iteration (`tau = max(tau_max - i*dt, 1e-3)`) but take a **positive**
step each time (`x = x + drift*dt + ...`), i.e. they discretize with
`dτ̄ = -dt`. Substituting flips the bracketed term's sign: for this
process (`f(x) = -x`, `g² = 2`), the correct drift is `x + 2·score`, **not**
`-x - 2·score` — the code implemented the literal negation of the correct
formula, at both call sites, since the formula was inlined separately at
each one rather than shared.

### Reachability

`api/pilots_api.py::post_diffusion_stress_test`
(`POST /pilots/options/ai/diffusion-stress-test`) is the sole production
caller (confirmed via a repo-wide grep — no other `.py`/`.ts`/`.tsx` file
outside `tests/` and the engine module itself references these
functions). It trains a real conditional model on real historical
log-returns each request and calls `generate_guided_crisis_paths` to
produce the price paths and VaR95/CVaR95/VaR99/CVaR99 figures shown on the
Pilots PWA's Generative Diffusion Stress screen
(`GenerativeDiffusionStressView.tsx`).

## How this was verified

Three independent methods, cross-checked against each other:

1. **Hand-derivation** — the `dτ̄ = -dt` substitution above.
2. **Analytic-score isolation** — for the conjugate case `x_0 ~ N(5,1)`
   (chosen so the marginal stays `p_τ(x) = N(5·e^-τ, 1)` for *all* τ, since
   `x_0`'s own unit variance exactly cancels the marginal-variance formula),
   the analytic score is `score(x,τ) = -(x - 5·e^-τ)`. Integrating the
   reverse SDE from `τ=3` (≈ the stationary N(0,1) prior) down to `τ≈0`:
   - buggy drift (`-x - 2·score`): `mean≈-25 to -29`, `var≈770-800`
   - fixed drift (`x + 2·score`): `mean≈4.97-5.0`, `var≈1.0-1.03`

   reproduced independently twice (once by the reporter, once during this
   fix's planning pass) with matching orders of magnitude.
3. **Live-pipeline repro** — trained `train_conditional_diffusion_model` on
   realistic daily log-returns (std ≈ 1.2%) and ran
   `generate_guided_crisis_paths` at the live endpoint's actual parameters
   (`steps=100, dt=1/252`): buggy generated-return std ≈ 2.38 (238%), fixed
   ≈ 1.41 (141%) — see "Disclosed, not fixed" below for why 1.41 is still
   far from the true 1.2% training scale.

The bug shipped undetected because every pre-existing generation test
(`test_diffusion_engine_e2e`, `test_generate_guided_crisis_paths_across_regimes`,
`test_classifier_free_guidance_monotonicity`,
`test_backwards_compatibility_generate_synthetic_crash_paths`) only checked
shape/NaN/Inf or a directional monotonicity comparison — none checked that
the generated distribution was actually the *right* one. That is the actual
gap this fix closes (see Tests below).

## The fix

- Extracted the drift formula into a single module-level helper,
  `_reverse_sde_drift(x, score) -> x + 2.0 * score`, called from both
  `generate_synthetic_crash_paths` and `generate_guided_crisis_paths`
  instead of each inlining its own copy. This is deliberate, not just
  tidiness: two independently-inlined copies of the same formula is exactly
  how this bug shipped at one site without the other catching it in review
  or testing.
- Corrected the three stale comments/docstring describing the old (wrong)
  formula: the comment above each call site, and the "Reverse SDE
  integration:" line in `generate_guided_crisis_paths`'s docstring (its
  separate CFG score-combination formula a few lines above is correct and
  was left untouched).
- Corrected the same LaTeX formula in
  `docs/architecture/ml-and-reports.md`'s `synthetic_diffusion_engine.py`
  architecture bullet.
- Fixed a pre-existing test-fixture defect surfaced by the sign fix:
  `test_classifier_free_guidance_monotonicity`'s `"vol_shock"` class
  (`paths_c1`) was fabricated with a **positive** mean (`+0.10`) despite
  being commented as a crash regime — under the old buggy sign, the test
  passed via pathological divergence, not real signal (verified: with the
  fix applied and the fixture unchanged, the test fails outright). Flipped
  to a genuine negative-mean crash (`-0.10`); verified this restores strict
  CVaR/VaR monotonicity across `w ∈ {0,1,2,3}` under the corrected sign.

## Investigated but deferred: VaR/CVaR-vs-paths data-consistency

A separate, independent inconsistency was also reported: in
`post_diffusion_stress_test`, the `paths` returned to the client are built
via `_clip_and_compound_diffusion_path`, which clips each per-step return to
`[-50%, +200%]` before compounding onto the spot price (needed to keep
compounded prices positive) — while VaR/CVaR were computed via
`compute_diffusion_var` on the **raw, unclipped** generated returns. These
two consumers can therefore read different effective data on a draw where
clipping actually engages.

A straightforward "clip once, feed the identical array to both" fix was
implemented and tested during this PR — and found to introduce a **worse,
live-reachable regression**: the clip bounds are asymmetric (-50% down vs.
+200% up, needed only for price positivity, not for the additive log-return
math VaR/CVaR performs), and summing many clipped steps compounds that
asymmetry into a systematic upward bias. On a high-variance draw (see the
disclosed calibration gap below — this is exactly the regime the endpoint's
real hyperparameters produce), this pushed the computed VaR/CVaR **negative**
— i.e. implying a nonsensical "guaranteed profit at 95% confidence" —
regressing `tests/test_pilots_api.py::TestDiffusionStressTest::
test_var_cvar_never_reach_or_exceed_spot_price_end_to_end`, the exact test
guarding against this class of result. Confirmed empirically: with only the
sign fix applied (no clip-consistency change), all 18 existing diffusion
endpoint/helper tests pass cleanly; adding the naive clip-before-VaR change
reintroduces two of them failing with VaR values in the thousands (e.g.
`VaR_95=-2412.65` against a `spot=150.0` test fixture).

This was reverted rather than shipped broken. The reported inconsistency is
real, but a correct fix needs to either (a) resolve the calibration gap
below first, so extreme clip-saturating draws stop being the common case, or
(b) redesign the VaR/CVaR computation to derive from the actual compounded
price paths' total simple returns (a genuinely consistent, single
source-of-truth computation) rather than a shared clipped log-return array —
both are out of scope for a focused sign-error fix. Left as a named
follow-up. The module-level `_DIFFUSION_PATH_MIN_STEP`/
`_DIFFUSION_PATH_MAX_STEP` constants (extracted from
`_clip_and_compound_diffusion_path`'s previously-hardcoded defaults) were
kept regardless — harmless, single-source-of-truth cleanup with no behavior
change, useful groundwork for whichever fix direction is chosen later.

## Disclosed, not fixed: endpoint calibration/undertraining gap

Fixing the sign does **not** fully resolve the endpoint's downstream
"VaR/CVaR saturates near 100% of spot" symptom at its literal production
hyperparameters (`epochs=15, steps=100, dt=1/252` → `tau_max≈0.4`). Per the
live-pipeline repro above, generated return std drops from ~2.38 (238%,
buggy) to ~1.41 (141%, fixed) — a real, mathematically necessary
improvement, but still roughly two orders of magnitude above the true
~1.2% training scale.

Root cause is separate and pre-existing, not caused by the sign bug:
`tau_max≈0.4` is far short of true OU stationarity (the true forward
marginal has `var≈0.55` there, not the `N(0,1)` the generator starts from),
and 15 training epochs is not enough for this small MLP to correct for the
Wiener noise injected over 100 integration steps at that budget. Confirmed
this isn't simply "more integration steps": `dt=0.01, steps=100`
(`tau_max=1.0`) made it *worse* (std=2.38), since more raw noise gets
injected than the undertrained score network can reel back in; more epochs
(200→1000) converges the std down (0.69→0.43) but does not close the gap
even at 1000 epochs.

This PR does not attempt to fix this — it needs its own calibration study
(epochs/tau_max/steps/network-capacity tradeoffs), and conflating it with
the sign fix would have made this PR's own regression tests unreliable (see
Tests below for how they were scoped around it). Left as a named follow-up;
an operator relying on this endpoint's absolute VaR/CVaR *magnitudes* today
should treat them as directionally improved but not yet realistic.

## Tests

```bash
python3 -m pytest tests/test_synthetic_diffusion_engine.py -v
python3 -m pytest tests/test_pilots_api.py -k Diffusion -v
python3 -m pytest tests/test_pilots_paper_broker.py -k Diffusion -v
```

`tests/test_synthetic_diffusion_engine.py` gained three new tests closing
the "no distributional correctness coverage" gap identified above, plus a
fixture fix to an existing test:

- `test_reverse_sde_drift_recovers_known_gaussian_analytic_score` —
  bypasses the trained network; integrates the real `_reverse_sde_drift`
  helper against the analytic score of a known `N(5,1)` conjugate target
  and asserts recovery within tolerance.
- `test_generate_synthetic_crash_paths_recovers_known_training_distribution` —
  full pipeline (`train_diffusion_model` + `generate_synthetic_crash_paths`)
  against data drawn from a known Gaussian.
- `test_generate_guided_crisis_paths_recovers_known_crash_regime_direction` —
  full pipeline for the conditional/guided code path the live endpoint
  actually calls, asserting the recovered mean has the correct (negative)
  sign for a genuine crash-class training set — the most direct test of
  this specific bug class, since the pre-fix drift recovers the *wrong
  sign entirely* (positive mean for a negative-mean training regime), not
  merely the wrong magnitude.
- `test_classifier_free_guidance_monotonicity` — fixture fixed (see "The
  fix" above); still asserts CFG's directional amplification behavior.

All three new tests, plus the fixed monotonicity test, were confirmed to
fail against a deliberately-reintroduced sign bug (verified by temporarily
flipping `_reverse_sde_drift`'s return value back to `-x - 2.0 * score` and
re-running) — they are real regression guards, not vacuous. Their
hyperparameters were deliberately chosen from this codebase's own
already-proven-reliable range (matching
`test_classifier_free_guidance_monotonicity`'s existing scale/epoch/tau_max
choices), not the live endpoint's literal `epochs=15/dt=1/252` combination,
which the calibration gap above prevents from converging within any
reasonable tolerance for reasons unrelated to this sign bug.

## Related

- CLAUDE.md's Phase 19-30/31-36 diffusion-engine bullets (Phase 34,
  `validation/synthetic_diffusion_engine.py`'s introduction and prior
  remediation rounds).
- `docs/architecture/ml-and-reports.md`'s `validation/synthetic_diffusion_engine.py`
  entry.
- `docs/known_issues/scenario_matrix_field_mismatch.md` and
  `docs/known_issues/options_desk_mock_live_parity_sweep_2026_08_19.md` —
  unrelated bug class, but the same general area of the Paper
  Broker/options-desk surface.
