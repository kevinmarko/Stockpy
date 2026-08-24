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

## What was investigated and deliberately NOT shipped

The original report also flagged that `api/pilots_api.py`'s VaR/CVaR
computation reads from raw, unclipped generated returns while the
displayed price paths read from a clipped version — a real, independent
inconsistency. I implemented the obvious fix (clip once, feed the same
array to both consumers) and it broke an existing, load-bearing test:
`test_var_cvar_never_reach_or_exceed_spot_price_end_to_end` started
failing with `VaR_95=-2412.65` against a `$150` spot fixture — a
"guaranteed profit" result, clearly nonsensical.

Root cause: the clip bounds are asymmetric (`-50%` down vs. `+200%` up —
correct for keeping a *compounded price* positive, since a price can't
lose more than 100%), but VaR/CVaR sum returns *additively* in log-return
space. Summing many asymmetrically-clipped steps compounds that asymmetry
into a systematic upward bias, which on a high-variance draw pushes the
5th-percentile total return positive — hence a negative "loss." I confirmed
this empirically both ways: with only the sign fix (no clip-consistency
change), all 18 existing diffusion endpoint tests pass; adding the naive
clip-before-VaR change reintroduces 2 failures.

Rather than ship that regression, I reverted it and documented the finding
in full (with the reproduced negative VaR value) as a disclosed follow-up,
alongside a second, genuinely separate, pre-existing finding surfaced
during verification: even after the sign fix, the endpoint's literal
production hyperparameters (`epochs=15, steps=100, dt=1/252`) don't
converge to a realistic scale (generated std ~141% vs. a true ~1.2%
training scale) — a calibration/undertraining gap unrelated to the sign
bug, also left as a disclosed follow-up rather than silently claimed fixed.

## How to verify

```bash
python3 -m pytest tests/test_synthetic_diffusion_engine.py -v
python3 -m pytest tests/test_pilots_api.py -k Diffusion -v
python3 -m pytest tests/test_pilots_paper_broker.py -k Diffusion -v
```

All pass (627/627 across the three files' full suites, not just the
diffusion-scoped subsets). The three new tests (plus the fixed
monotonicity test) were confirmed to fail against a deliberately
reintroduced sign bug, proving they're real regression guards.

## Scope decisions carried from the plan

- The optional `seed` parameter (item 5 of the original report) is
  deferred to a follow-up PR — orthogonal to the sign bug, and the
  reporter's own framing ("lower priority, worth doing if convenient")
  granted discretion to scope it out of a focused, severity-driven fix.
