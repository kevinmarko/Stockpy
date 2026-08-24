# Known issue (2026-08-24): Reverse-time SDE integrated with a sign error in the generative diffusion stress engine

**Status: sign error fixed and verified; VaR/CVaR-vs-paths consistency
fixed and verified (2026-08-24 follow-up); endpoint calibration gap
partially mitigated (measurably improved, not resolved) with the
remaining gap explicitly disclosed.** Branch `fix-diffusion-reverse-sde-sign`.

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

## Partially mitigated: endpoint calibration/undertraining gap

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
here, and conflating it with this PR's regression tests would have made
them unreliable (see Tests below for how they were scoped around this
gap). Left as a named follow-up; an operator relying on this endpoint's
absolute VaR/CVaR *magnitudes* today should treat them as measurably
improved but not yet realistic.

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
