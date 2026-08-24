# Known issue (2026-08-24): Reverse-time SDE integrated with a sign error in the generative diffusion stress engine

**Status: sign error fixed and verified; VaR/CVaR-vs-paths consistency
fixed and verified (2026-08-24 follow-up); endpoint calibration gap
substantially mitigated via early-stop + analytic Tweedie denoising plus
a request-contract horizon bound (2026-08-24, second follow-up) --
generated-return scale improved a further ~2-3x on top of the epoch bump,
still NOT an exact match to the true training scale, disclosed explicitly.
A real trade-off discovered and disclosed during this work: the fix's
analytic finishing step deliberately discounts CFG guidance for stability,
which measurably weakens (and can invert, on some data) classifier-free
guidance's directional effect on the final reported numbers.** Branches
`fix-diffusion-reverse-sde-sign` (merged, #882),
`fix-diffusion-varcvar-consistency-and-calibration` (merged, #884), and
this round's follow-up.

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

## Fixed (follow-up, same day): VaR/CVaR-vs-paths data-consistency

A separate, independent inconsistency was also reported: in
`post_diffusion_stress_test`, the `paths` returned to the client are built
via `_clip_and_compound_diffusion_path`, which clips each per-step return to
`[-50%, +200%]` before compounding onto the spot price (needed to keep
compounded prices positive) — while VaR/CVaR were computed via
`compute_diffusion_var` on the **raw, unclipped** generated returns. These
two consumers could therefore read different effective data on a draw where
clipping actually engaged.

**First attempt, reverted.** A straightforward "clip once, feed the
identical array to both" fix was implemented and tested — and found to
introduce a **worse, live-reachable regression**: the clip bounds are
asymmetric (-50% down vs. +200% up, needed only for price positivity, not
for the additive log-return math VaR/CVaR performs), and summing many
clipped steps compounds that asymmetry into a systematic upward bias. On a
high-variance draw (exactly the regime the endpoint's real hyperparameters
produce — see the calibration gap below), this pushed the computed VaR/CVaR
**negative** — implying a nonsensical "guaranteed profit at 95%
confidence" — regressing `test_var_cvar_never_reach_or_exceed_spot_price_end_to_end`.
Reproduced: `VaR_95=-2412.65` against a `spot=150.0` test fixture. Reverted
rather than shipped broken.

**Real fix.** `post_diffusion_stress_test` now derives VaR/CVaR directly
from the compounded `paths` array itself — each path's total realized
*simple* return, `final_price / spot_price - 1` — instead of from a
separately-clipped variant of the raw log-returns. This is trivially
consistent with `paths` by construction (the VaR input is *computed from*
`paths`, not from a second, independently-transformed view of the same
draw), and is already the correct 1-D per-path-return shape
`compute_diffusion_var` expects (no per-step summing needed — it treats a
1-D array as one total return per path). Converted to dollars via a plain
linear multiply (the fraction is already a simple return, not a log
return — `_diffusion_logret_loss_to_dollars`'s exponential transform would
be the wrong transform here; that function remains correct and is kept,
unused by this endpoint, for its own log-return contract). A defensive
`max(0.0, ...)` floor is applied on top (VaR/CVaR are loss magnitudes; a
"negative loss" is a category error to ever display, regardless of what an
intermediate computation produced) — not load-bearing, since the bound is
already guaranteed by construction: `_clip_and_compound_diffusion_path`
floors every price at `min_price=0.01 > 0`, so every total return is
strictly `> -1.0` (a loss can never imply more than -100%), which is what
keeps `spot * var_fraction` inside `[0, spot)`.

**Verification**: stress-tested across 15 seeds × 5 regimes × 2 confidence
levels (150 checks total) with **zero violations** of `0 <= VaR/CVaR <
spot` — vs. the reverted approach's reproducible failure on the very first
scenario tried. All 18 pre-existing diffusion endpoint/helper tests still
pass, plus a new dedicated regression test,
`test_var_cvar_computed_from_the_same_paths_returned_to_the_client`, which
independently recomputes VaR/CVaR from *only* the `paths` field in the HTTP
response body and asserts an exact match against the endpoint's own
reported figures — a direct proof that VaR/CVaR really is derived from the
same data the client sees.

The module-level `_DIFFUSION_PATH_MIN_STEP`/`_DIFFUSION_PATH_MAX_STEP`
constants (extracted from `_clip_and_compound_diffusion_path`'s
previously-hardcoded defaults during the reverted first attempt) were kept
regardless — harmless, single-source-of-truth cleanup with no behavior
change.

## Historical: epoch-bump mitigation (2026-08-24, first follow-up, PR #884)

Fixing the sign does **not** fully resolve the endpoint's downstream
"VaR/CVaR saturates near 100% of spot" symptom at its literal production
hyperparameters (`steps=100, dt=1/252` → `tau_max≈0.4`). Per the
live-pipeline repro above, generated return std drops from ~2.38 (238%,
buggy) to ~1.41 (141%, fixed sign, still 15 epochs) — a real, mathematically
necessary improvement, but still roughly two orders of magnitude above the
true ~1.2% training scale.

Root cause is separate and pre-existing, not caused by the sign bug:
`tau_max≈0.4` is far short of true OU stationarity (the true forward
marginal has `var≈0.55` there, not the `N(0,1)` the generator starts from),
and 15 training epochs is not enough for this small (64-hidden-unit) MLP to
correct for the Wiener noise injected over 100 integration steps at that
budget. An epoch sweep (15→100→300→600→1000→2000→5000→10000, measured on
this endpoint's real hyperparameters) shows the generated-return std
shrinking monotonically (1.50→1.25→1.25→1.05→0.98→0.94→0.24→0.22 across two
different but representative training-data scenarios) but with sharply
diminishing returns that never approach the true ~0.011-0.012 training
scale even at 10,000 epochs — confirming this is a network-capacity/
training-quality limit, not simply "needs more epochs." Also confirmed
this isn't about integration granularity either: `dt=0.01, steps=100`
(`tau_max=1.0`, more total steps at the same per-step scale) made the
buggy-era number *worse*, since more raw noise gets injected than an
undertrained network can reel back in — and per SDE theory, total injected
Wiener-noise variance over a reverse pass is `2·tau_max` regardless of how
finely it's discretized, so the real lever is training quality (or
`tau_max` itself), not step count. A bootstrap-initialization variant
(starting the reverse process from real training-data samples propagated
through the exact analytic forward marginal at `tau_max`, instead of a bare
`N(0,1)` prior) was also tested and found to help only marginally (~8%
std reduction) — most of the excess variance is injected *during* the
100-step integration itself, not from a poorly-approximated starting point.

**Partial fix shipped**: the endpoint's training epoch count was raised
from `epochs=15` to `epochs=1000` — verified negligible latency cost
(~0.002s → ~0.1-0.2s on this tiny MLP, dwarfed by the endpoint's real
750-day historical-bars fetch) and a real, consistent, verified narrowing
of the underlying generated-return distribution (std ~1.50 → ~0.98 on 500
paths, a genuine ~35% reduction, reproduced across the endpoint's real
conditional/windowed training-data shape, not just idealized i.i.d. data).
At the endpoint's real `num_paths=500` cap, the reported dollar VaR/CVaR
also move consistently in the correct (less-saturated) direction with the
epoch increase (e.g. `VaR_95: $149.01 → $147.19`, `CVaR_99: $149.93 →
$149.77` against a `$150` spot, reproduced deterministically) — real, but
still deeply saturated near spot; **this is a measured mitigation, not a
resolution**. At smaller `num_paths` (e.g. 50, used by some test fixtures)
the percentile-based VaR/CVaR estimate is noisy enough that the direction
of improvement is not always visible on a single draw, even though the
underlying distribution genuinely narrowed — the aggregate std reduction is
the reliable signal, not any single VaR percentile draw at low sample
count.

A genuine fix needs its own calibration/architecture study (a
noise-schedule-aware network, explicit `tau` embedding, a
higher-order/predictor-corrector SDE solver, or simply accepting a
materially smaller `tau_max` for this endpoint's real-time latency budget
and re-deriving what "stress" means at that shorter horizon) — out of scope
for that PR, and conflating it with its regression tests would have made
them unreliable. This is exactly the gap the round below addresses.

## Further mitigated: early-stop + analytic Tweedie denoising (2026-08-24, second follow-up)

The user asked to "fully fix" the residual calibration gap. This round
diagnosed the ACTUAL root cause (not just "undertrained"), implemented a
real fix, discovered and corrected a genuine flaw in the fix's first
design, and shipped a substantially — not fully — improved result. The
honest account, including the false start:

### Diagnosis: score accuracy collapses below tau~0.1

Directly measured the trained score network's prediction accuracy against
the ANALYTIC target score (sampling `x0` from real training data,
propagating through the exact forward marginal, comparing the network's
prediction to the true `score = -z/std`):

```
tau=0.500: true_score std=1.26  pred_score std=1.34   (median rel err 16%)
tau=0.100: true_score std=2.33  pred_score std=2.54   (median rel err 17%)
tau=0.050: true_score std=3.19  pred_score std=2.77   (median rel err 17%)
tau=0.010: true_score std=7.16  pred_score std=1.87   (median rel err 74%)  <- training's own tau floor
tau=0.001: true_score std=22.31 pred_score std=0.65   (median rel err 97%)  <- generation's floor
```

The tiny (64-hidden-unit, single-hidden-layer, raw-scalar-`tau`-input) MLP
never learns to reproduce the true score's `~1/sqrt(tau)` blow-up as
`tau→0`. Ruled out: score clipping (max abs raw score seen during
generation was ~11.4, nowhere near the ±50 clip) and a
generation-vs-training tau-floor mismatch (generation's `1e-3` floor vs.
training's `0.01` — tested explicitly, made ~0% difference). Also tested
and **rejected** an eps-parametrization redesign (train the network to
predict unscaled `-z` instead of `-z/std`, reconstruct
`score = -eps_hat/std` at inference): eps-prediction accuracy was much
better in absolute terms, but dividing by the near-zero `std` at small
`tau` to reconstruct the score amplifies whatever residual eps error
remains — measured to make generation noticeably *worse*, not better.

### The fix: stop early, finish with one analytic Tweedie step

Instead of integrating the noisy Euler-Maruyama loop all the way to
`tau≈1e-3`, stop early at `tau_stop` (while the score is still reasonably
accurate) and finish with one deterministic analytic step — Tweedie's
formula for this OU/VP-SDE:

```python
x0_hat = (x + var(tau_stop) * score(x, tau_stop)) / exp(-tau_stop)
```

Implemented as `_tweedie_denoise` in `validation/synthetic_diffusion_engine.py`,
independently re-derived two ways (Tweedie's formula directly, and the
closed-form Gaussian-Gaussian conjugate posterior mean for the
`x0 ~ N(mu0,1)` case this module's own tests already use) — both give the
exact same expression, confirmed via an exact pointwise unit test
(`test_tweedie_denoise_recovers_known_posterior_mean_gaussian`). This is
the same "denoised estimate"/x0-prediction technique used in the
diffusion-modeling literature (Karras et al. 2022 EDM, DDIM's
x0-parametrization), though a genuinely new pattern for this codebase (no
existing precedent).

`generate_synthetic_crash_paths` and `generate_guided_crisis_paths` both
gained an optional `tau_stop: Optional[float] = None` parameter (`None` →
`_DEFAULT_TAU_STOP`; `0.0` disables early-stopping entirely and reproduces
the original full-integration behavior byte-for-byte — verified via
`test_generate_paths_tau_stop_zero_disables_early_stop`). `_resolve_tau_stop`
clamps the effective stop point to guarantee at least 2 real noisy
integration steps always run, so a short-`tau_max` caller can never hit a
degenerate zero-step case (`test_tau_stop_clamp_preserves_at_least_two_noisy_steps_on_short_horizons`).

### A real flaw found in the first design: CFG amplifies the network's own noise

The FIRST version of this fix (`tau_stop=0.18`, chosen from a sweep on a
flawed prototype -- see below) measured a dramatic ~7-10x improvement.
Re-verifying against the ACTUAL shipped `generate_guided_crisis_paths`
function at the endpoint's real `guidance_scale=2.0` default showed only a
~3-5% improvement — the sweep that chose `tau_stop=0.18` had a real bug: it
always evaluated the model's *unconditional* score (`c_uncond`), never
actually exercising the CFG combination `(1+w)*score_cond - w*score_uncond`
the live endpoint uses. Re-swept properly against the real CFG-guided
function:

| guidance_scale | 0.0 | 1.0 | 2.0 (endpoint default) | 3.0 |
|---|---|---|---|---|
| ratio at tau_stop=0.18 | 28.8x | 56.9x | 87.1x | — |

CFG's `(1+w)*score_cond - w*score_uncond` combination amplifies EACH
underlying score prediction's own inaccuracy by up to a `(1+2w)` factor —
at `w=2.0` this dominates over the early-stop benefit almost entirely.

**Fix for the fix**: the analytic Tweedie step now always uses
`guidance_scale=0` (pure conditional, no CFG) for its own single step,
regardless of what guidance was requested for the noisy loop — the noisy
loop still uses the full requested CFG guidance (that's what supplies the
regime's real directional signal), only the final denoising step drops it.
Re-swept `tau_stop` with this correction at the real `guidance_scale=2.0`:
best around **`tau_stop=0.28`** (ratio ~27-32x across 3 data seeds and all
4 regimes), vs. ~87-98x with early-stopping disabled — a real, verified
~3x further improvement, this time on the actual production code path.

### A disclosed trade-off: CFG's directional effect on the FINAL output is weakened

Discounting CFG for the final step is not free. On this module's own
`test_classifier_free_guidance_monotonicity` fixture (5 well-separated
synthetic classes), running the DEFAULT (denoise-stop enabled) behavior at
`guidance_scale=0` vs. `guidance_scale=3.0` **reversed** the expected
monotonic relationship (`var95: 1.89→1.51`, decreasing with more guidance,
not increasing) — because the neutral final step "resets" toward the
sober, pure-conditional estimate regardless of how strongly the noisy loop
was guided. On the production-representative scenario (real overlapping
windows, `L=29`) with a genuinely-trained (not degenerate) two-class
regime distinction, the effect is smaller but still present (`var95` at
`w=0..3`: `2.72→2.70→2.67→2.65`, a slight, unintended *decrease*).

This is a genuine, disclosed limitation, not silently accepted: CFG
guidance still works correctly *during the noisy loop* (verified via
`test_generate_guided_crisis_paths_early_stop_measurably_improves_calibration`'s
negative-mean/crash-direction check), but its effect on the exact FINAL
reported VaR/CVaR magnitude is now muted, and can go the wrong direction
on some data. `test_classifier_free_guidance_monotonicity` was updated to
pass `tau_stop=0.0` explicitly, isolating what it actually verifies (the
CFG combination formula's own correctness) from this separate,
denoise-stop-specific side effect — the calibration property of the
DEFAULT behavior is covered by the new dedicated tests instead, not
re-asserted in that test.

### Honest final numbers

At the endpoint's real hyperparameters (`steps=100, dt=1/252,
guidance_scale=2.0`, the shipped `epochs=1000`, `tau_stop=0.28`,
`_predict_score`'s final-step `w=0` fix):

| Scenario | Disabled (tau_stop=0.0) | Default (tau_stop=0.28, final w=0) |
|---|---|---|
| Unconditional, `L=29` | ~38x true scale | ~18x true scale |
| Guided, `L=29`, real crash bias, `w=2.0` | ~14x true scale | ~7x true scale |

Roughly a further ~2x improvement on top of the epoch-bump mitigation
above, reproducible and verified by the new tests — **still not an exact
match**, and the residual gap remains a genuine, disclosed limitation of
this small hand-rolled MLP architecture. An operator relying on this
endpoint's absolute VaR/CVaR magnitudes should treat them as measurably,
substantially improved across two rounds of mitigation, but not yet
realistic — a true fix needs its own architecture/training redesign
(explicit `tau` embedding or a noise-schedule-aware network, likely a
bigger model), out of scope for both rounds so far.

### L-dependence and the new horizon bound

The fixed `tau_stop=0.28` default was tuned for the endpoint's actual
default `horizon=30` (`L=29`) and is **not** re-tuned per `L`. Measured
(not estimated, with the real CFG-guided function, `guidance_scale=2.0`, a
genuinely-trained two-class regime distinction) across the realistic `L`
range:

| L (horizon) | 9 (10) | 14 (15) | 29 (30) | 44 (45) | 59 (60) |
|---|---|---|---|---|---|
| ratio | 22.4x | 21.1x | **18.3x** | 35.1x | 41.8x |

Good and relatively stable for `L` up to ~30 (the production default),
degrading materially beyond it — a real, disclosed limitation of this
small fixed-capacity network, not something `tau_stop` alone can paper
over for larger horizons.

`DiffusionStressTestRequest.horizon` was previously an unbounded bare
`int`; it is now `Field(30, ge=5, le=35)` — matching the range the fix is
actually verified for — so an operator can no longer request a horizon
this fix was never tuned for and get a silently worse-calibrated result.
The webapp's horizon `<input>` (`GenerativeDiffusionStressView.tsx`) got a
matching `max="35"` cap.

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
  fix" above); still asserts CFG's directional amplification behavior (as
  of the second follow-up below, now with `tau_stop=0.0` explicit, to
  isolate the CFG formula's own correctness from the separate
  denoise-stop-specific side effect documented in "A disclosed trade-off"
  above).

All three new tests, plus the fixed monotonicity test, were confirmed to
fail against a deliberately-reintroduced sign bug (verified by temporarily
flipping `_reverse_sde_drift`'s return value back to `-x - 2.0 * score` and
re-running) — they are real regression guards, not vacuous. Their
hyperparameters were deliberately chosen from this codebase's own
already-proven-reliable range (matching
`test_classifier_free_guidance_monotonicity`'s existing scale/epoch/tau_max
choices), not the live endpoint's literal `dt=1/252` combination, which the
calibration gap above prevents from converging within any reasonable
tolerance for reasons unrelated to this sign bug.

`tests/test_pilots_api.py::TestDiffusionStressTest` gained one new test for
the VaR/CVaR-vs-paths consistency fix:

- `test_var_cvar_computed_from_the_same_paths_returned_to_the_client` —
  recomputes VaR/CVaR entirely independently from only the `paths` field of
  a real HTTP response body and asserts an exact match against the
  endpoint's own reported `VaR_95`/`CVaR_95`/`VaR_99`/`CVaR_99` — proving
  they are genuinely derived from the same data, not a separate draw or
  transform of it.

The existing `test_var_cvar_never_reach_or_exceed_spot_price_end_to_end`
(the invariant the reverted first attempt broke) was re-run 8 consecutive
times with the real fix applied, plus a 150-combination seed/regime/
confidence-level sweep outside the test suite (documented above) — zero
violations in either.

`tests/test_synthetic_diffusion_engine.py` gained five more new tests for
the second follow-up (early-stop + Tweedie denoising):

- `test_tweedie_denoise_recovers_known_posterior_mean_gaussian` — exact
  pointwise match against the closed-form conjugate posterior mean, not
  just a statistical bound (the two formulas are algebraically identical,
  per the derivation above).
- `test_resolve_tau_stop_defaults_and_clamps` — `None` resolves to
  `_DEFAULT_TAU_STOP`; the short-horizon clamp preserves at least 2 real
  steps.
- `test_generate_synthetic_crash_paths_early_stop_measurably_improves_calibration`
  and `test_generate_guided_crisis_paths_early_stop_measurably_improves_calibration` —
  production-representative (`steps=100, dt=1/252`) before/after tests for
  the unconditional and CFG-guided (`guidance_scale=2.0`, the endpoint's
  real default) code paths respectively; both assert the default run's
  std-to-true-std ratio is strictly better than the same draw with
  early-stopping explicitly disabled, plus a concrete absolute bound.
  Confirmed to fail (both, correctly) when `_DEFAULT_TAU_STOP` is
  temporarily forced to `0.0` (simulating a reintroduced regression).
- `test_generate_paths_tau_stop_zero_disables_early_stop` — `tau_stop=0.0`
  reproduces the pre-2026-08 full-integration loop bit-for-bit (verified
  via a hand-rolled replica of the exact original discretization and RNG
  consumption order).
- `test_tau_stop_clamp_preserves_at_least_two_noisy_steps_on_short_horizons` —
  finite, non-degenerate output at the tightest `tau_max` already exercised
  elsewhere in this file.

`tests/test_pilots_api.py::TestDiffusionStressTest` gained one more test
for the new horizon bound:

- `test_horizon_out_of_bounds_returns_honest_422` — `horizon=50` and
  `horizon=1` both return a clean Pydantic validation 422, never a 500 or
  a silent clamp.

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
