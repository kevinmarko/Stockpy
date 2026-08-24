# Walkthrough: reverse-SDE sign fix in the generative diffusion stress engine

## What was wrong

`validation/synthetic_diffusion_engine.py`'s two generation functions
(`generate_synthetic_crash_paths`, `generate_guided_crisis_paths`)
integrated the reverse-time OU SDE with a negated drift:

```python
drift = -x - 2.0 * score   # WRONG
```

instead of the correct

```python
drift = x + 2.0 * score    # CORRECT
```

Training was already correct; only sampling was affected. The sampler
diverged instead of denoised — verified with matching numbers three
independent ways (hand-derivation, an analytic-score isolation test, a
live-pipeline repro). Full detail:
`docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md`.

## What changed

1. **`validation/synthetic_diffusion_engine.py`** — added a shared
   `_reverse_sde_drift(x, score)` helper (with the Anderson-1982 derivation
   in its docstring); both generation functions now call it instead of
   each inlining its own copy of the formula. Fixed the three stale
   comments/docstring describing the old formula.
2. **`tests/test_synthetic_diffusion_engine.py`** — added 3 new tests that
   assert the generated distribution actually recovers a *known* target
   (not just shape/NaN-freedom, which every prior generation test already
   checked and which the bug passed anyway). Fixed a pre-existing test
   fixture bug in `test_classifier_free_guidance_monotonicity` (a
   "vol_shock" class fabricated with a positive mean, which only passed
   pre-fix via the bug's own divergence).
3. **`docs/architecture/ml-and-reports.md`** — corrected the same sign in
   its LaTeX formula.
4. **`api/pilots_api.py`** — a small, safe refactor only:
   `_clip_and_compound_diffusion_path`'s previously-hardcoded `-0.5`/`2.0`
   defaults are now named module constants
   (`_DIFFUSION_PATH_MIN_STEP`/`_DIFFUSION_PATH_MAX_STEP`), no behavior
   change. A more ambitious VaR/CVaR-vs-paths consistency fix was
   attempted and reverted — see below.
5. **`docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md`**
   (new) + `docs/known_issues/README.md` index row + a CLAUDE.md bullet
   (auto-mirrored to AGENTS.md).

## VaR/CVaR-vs-paths consistency — fixed in a same-day follow-up

The original report also flagged that `api/pilots_api.py`'s VaR/CVaR
computation reads from raw, unclipped generated returns while the
displayed price paths read from a clipped version — a real, independent
inconsistency. My first attempt (clip once, feed the same array to both
consumers) broke an existing, load-bearing test:
`test_var_cvar_never_reach_or_exceed_spot_price_end_to_end` started
failing with `VaR_95=-2412.65` against a `$150` spot fixture — a
"guaranteed profit" result, clearly nonsensical.

Root cause: the clip bounds are asymmetric (`-50%` down vs. `+200%` up —
correct for keeping a *compounded price* positive), but VaR/CVaR sum
returns *additively* in log-return space. Summing many asymmetrically-clipped
steps compounds that asymmetry into a systematic upward bias, which on a
high-variance draw pushes the 5th-percentile total return positive — hence
a negative "loss." I reverted that first attempt rather than ship it.

**The operator then asked me to pursue a real fix for this rather than
leave it deferred.** I derived VaR/CVaR directly from the compounded
`paths` array's own total simple returns (`final_price/spot - 1`) instead
of a separately-clipped variant of the raw draw — trivially consistent
with `paths` by construction, since it's computed *from* `paths`. Verified
robust across a 150-combination sweep (15 seeds × 5 regimes × 2 confidence
levels): zero violations of `0 <= VaR/CVaR < spot`, versus the reverted
approach's reproducible failure on the very first scenario tried. Added a
new regression test,
`test_var_cvar_computed_from_the_same_paths_returned_to_the_client`, which
independently recomputes VaR/CVaR from only the `paths` field of a real
HTTP response and asserts an exact match — direct proof of consistency.

## Endpoint calibration gap — partially mitigated, not resolved

Separately, even after the sign fix, the endpoint's literal production
hyperparameters (`steps=100, dt=1/252`) don't converge to a realistic
scale (generated std ~141% vs. a true ~1.2% training scale) — a
calibration/undertraining gap unrelated to the sign bug.

**The operator also asked me to pursue this.** I ran an epoch sweep
(15→10,000) and confirmed sharply diminishing returns that never approach
the true scale even at 10,000 epochs — a network-capacity/architecture
limit (this tiny 64-hidden-unit MLP), not simply "needs more epochs." I
also tested a bootstrap-initialization variant (starting the reverse
process from real training data through the exact analytic forward
marginal, instead of a bare `N(0,1)` prior) and found it helps only
marginally (~8%) — most of the excess variance is injected *during* the
100-step integration itself, not from a poorly-approximated starting
point. A genuine fix needs its own architecture/training redesign, which I
did not attempt (out of scope for what's verifiable in this pass).

I shipped a verified, low-risk **partial mitigation** instead: raised the
endpoint's training epochs from 15 to 1000. Negligible latency cost
(~0.002s → ~0.1-0.2s, dwarfed by the endpoint's real 750-day bars fetch),
and a real, measured ~35% reduction in the underlying generated-return
distribution's spread, with dollar VaR/CVaR moving consistently in the
correct (less-saturated) direction at the endpoint's real `num_paths=500`
cap. This is honestly documented as a mitigation, not a resolution — the
output remains far from realistic, and a real fix is a disclosed follow-up.

## How to verify

```bash
python3 -m pytest tests/test_synthetic_diffusion_engine.py -v
python3 -m pytest tests/test_pilots_api.py -k Diffusion -v
python3 -m pytest tests/test_pilots_paper_broker.py -k Diffusion -v
```

All pass (628/628 across the three files' full suites, not just the
diffusion-scoped subsets). The three new distributional-recovery tests
(plus the fixed monotonicity test) were confirmed to fail against a
deliberately reintroduced sign bug; the new VaR/CVaR consistency test was
confirmed to reflect the endpoint's actual response contract exactly.

## Scope decisions

- The optional `seed` parameter (item 5 of the original report) remains
  deferred to a follow-up PR — orthogonal to both fixes above, and the
  reporter's own framing ("lower priority, worth doing if convenient")
  granted discretion to scope it out.
- CI's `test (offline suite)` initially failed on this PR for an unrelated
  reason: `docs/settings_field_census.md` had gone stale (the repo's
  Autofix bot regenerated the `.json` companion but not the rendered
  `.md`). Fixed by re-running `scripts/measure_settings_census.py --write`
  at current HEAD — a 2-line diff (only the embedded commit hash).
