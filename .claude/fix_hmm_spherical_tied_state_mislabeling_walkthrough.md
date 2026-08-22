# Walkthrough: Fix HMM regime state mislabeling for spherical/tied covariance types

## What was broken

`HMMRegimeDetector.identify_states_by_vol()` decides which hidden HMM state
gets called `"bull"` vs `"bear"` by ranking states by fitted variance. This
label feeds `hmm_risk_on_probability`, which in turn feeds
`MacroEconomicDTO.killSwitch`'s crash-sensitivity escalation. Two of the
four supported `covariance_type` values were silently broken:

- **`spherical`**: hmmlearn's public `covars_` getter, for spherical
  covariance, returns a malformed shape whose length never matches
  `n_states` — verified directly against the installed `hmmlearn==0.3.3`
  (`(12, 4, 4)` for a 3-state, 4-feature fit; flattened length 192 vs.
  `n_states=3`). This meant `spherical` *always* fell through to an
  unlogged, arbitrary index-based fallback, completely decoupled from
  actual volatility. Reproduced: a real crash regime labeled `"bull"`.
- **`tied`**: tied covariance has no per-state variance (by construction,
  it's shared), so the code substituted the undirected magnitude of each
  state's mean feature vector. Not risk-directional. Reproduced: inverted
  labeling on bear-majority synthetic data.

Both are reachable today — `settings.HMM_COVARIANCE_TYPE` accepts both, and
`scripts/audit_regime_model.py --cov spherical|tied` was always a normal
invocation.

## What was fixed, and why each piece matters

1. **`spherical` → read `self.model._covars_`** instead of the public
   getter. This file already had the fix pattern documented in its own
   `fit()` warm-start code (`_covars_` is the compact, correct-shaped
   internal attribute) — `identify_states_by_vol()` just never used it.

2. **`tied` → directional risk-feature lookup.** Instead of an undirected
   norm, rank by the fitted mean of `realized_vol_20d` (or `vix_level`,
   `credit_spread`, negated `spy_return`, in that priority order) — these
   are genuinely "higher = riskier" (or, for spy_return, sign-flipped to
   match). `yield_curve_spread` is deliberately skipped: its risk direction
   depends on inversion, not level, so it isn't a safe drop-in.

3. **`min_covar` floor made branch-aware.** This is a defect a first-draft
   fix would have introduced: the new `tied` metric is a *signed*,
   near-zero-centered z-scored mean, and the existing
   `np.maximum(variances, self.min_covar)` floor — correct for genuine
   non-negative variances — would collapse every below-floor state to an
   identical value, silently reintroducing index-order ties. Caught by a
   validation pass before implementation, not found by testing after the
   fact.

4. **Logged the length-mismatch fallback.** This is what let the
   `spherical` bug ship silently in the first place — CONSTRAINT #6
   (fail-closed, never silent) now applies here.

5. **Fixed `n_states >= 4` labeling**, a separate, lower-severity but live
   bug: the labels list was built by loop position, not sorted rank, so the
   worst state at `n_states=4` got a generic `"state_3"` instead of
   `"bear"`. Reachable via `scripts/audit_regime_model.py --compare`'s
   default `state_counts=[2,3,4]` sweep. Fixed with an explicit
   n_states-branch (not a uniform rule) to keep the pre-existing,
   deliberately-different `n_states==2 → ["bull","sideways"]` contract
   exactly unchanged — a contradiction in the initial suggested wording was
   caught during plan validation and corrected before writing any code.

## An honest finding along the way

Fixing `tied`'s *labeling* did not make `tied` a *good* covariance choice
for regime detection. Tested empirically: on synthetic bull/bear data with
realistic within-state variance ratios, the `tied` EM fit collapsed to a
single dominant state for both a calm and a turbulent window
(`risk_on_probability` pinned at `1.0` for both), reproducibly across every
seed/init combination tried. This is a structural consequence of forcing
one shared covariance across states whose whole point is different
variance — not a bug this PR introduces or could fix inside
`identify_states_by_vol()`. Documented rather than hidden: the new
integration test excludes `tied` with an explanatory comment, and
`docs/regime_model_tuning_guide.md` now recommends against `tied` for
regime detection specifically.

## How this was verified, not just asserted

Every new/extended test was run against the **pre-fix** code first (via a
temporary `git stash` of only the code change, keeping the new tests) to
confirm it actually reproduces the bug — not just a plausible-sounding
story:

| Test | Pre-fix | Post-fix |
|---|---|---|
| semantic correctness — `spherical` | **FAILED** (guaranteed) | PASSED |
| semantic correctness — `tied` | passed (this seed happened not to trigger it) | PASSED |
| risk_on_probability integration — `tied` (excluded from final parametrize) | **FAILED** | still fails post-fix (structural, not a labeling bug — see above) |
| n_states=4 label position | **FAILED** | PASSED |
| length-mismatch logging | **FAILED** (no log) | PASSED |

Full targeted suite (39 tests across
`test_hmm_synthetic.py`/`test_hmm_no_lookahead.py`/`test_macro_hmm_integration.py`/`test_regime_diagnostics.py`/`test_hmm_state_persistence.py`)
passes. A broader sweep (`pytest -k "hmm or regime or macro_dto or dto_models"`,
417 tests) passes. `ruff check` shows 0 new findings vs. the pre-existing
25-finding baseline on the same two files.

## What this does NOT change

- `diag` (the default) and `full` were confirmed, not assumed, correct
  already — no behavior change for either.
- No downstream caller (`dto_models.py`, `signals/regime_multiplier.py`,
  `execution/risk_gate.py`, `technical_options_engine.py`,
  `validation/regime_diagnostics.py`, the Gravity audit script) assumes a
  label vocabulary this change alters — verified by grep across the repo.
- `n_states` 2 and 3 labeling is byte-identical to before.
