# HMM regime state mislabeling for `spherical`/`tied` covariance types

**Status: Fixed and verified (2026-08-22).**

## Summary

`regime/hmm_regime.py::HMMRegimeDetector.identify_states_by_vol()` ranks the
fitted Gaussian HMM's hidden states by variance (ascending) and labels the
lowest-variance state `"bull"`, the highest `"bear"`. `predict_proba()`
turns this into `hmm_risk_on_probability` (probability mass on the state(s)
labeled `"bull"`), which feeds `MacroEconomicDTO.killSwitch`'s HMM-agreement
fast-trigger and `market_regime`'s downgrade logic in `dto_models.py` — this
labeling directly affects whether the platform's crash-sensitivity
kill-switch escalation engages during an actual crash.

Two real bugs in the covariance-type-dependent variance extraction meant
`covariance_type="spherical"` and `covariance_type="tied"` could silently
mislabel states — including labeling an actual crash regime `"bull"`.
`settings.HMM_COVARIANCE_TYPE` already accepted both as legal values (the
default, `"diag"`, was never affected).

## Root cause

### `spherical`

```python
elif self.covariance_type == "spherical":
    variances = np.asarray(self.model.covars_, dtype=float).flatten()
```

hmmlearn's **public** `covars_` getter does not reliably return an
`(n_states, ...)`-shaped array for spherical covariance. Verified directly
against `hmmlearn==0.3.3` (matching `requirements.txt`'s pin):

```python
>>> m = GaussianHMM(n_components=3, covariance_type='spherical', n_iter=20)
>>> m.fit(X)  # X: (500, 4)
>>> np.asarray(m.covars_).shape
(12, 4, 4)
>>> np.asarray(m.covars_).flatten().shape
(192,)
>>> np.asarray(m._covars_).shape   # the compact internal attribute
(3, 4)
```

`flatten().shape == (192,)` never equals `n_states=3`, which trips this
function's own defensive fallback:

```python
if len(variances) != self.n_states:
    variances = np.arange(self.n_states, dtype=float)   # silent, unlogged
```

— meaning **every single `spherical` fit** fell back to raw, arbitrary
hidden-state index order, completely unrelated to actual volatility/risk.
`regime/hmm_regime.py`'s `fit()` method (in the same file, for
warm-starting) already knows about and documents this exact hmmlearn quirk:
*"hmmlearn's `covars_` getter expands to full matrices, but the setter
expects the compact shape. Use `_covars_` directly to bypass."*
`identify_states_by_vol()` was never updated to follow its own file's
precedent.

Reproduced end-to-end with synthetic two-regime data (a calm/bull regime,
mean realized-vol ≈ 0.08, and a crash/bear regime, mean realized-vol ≈
0.40): the genuine crash regime was labeled `"bull"`.

### `tied`

```python
elif self.covariance_type == "tied":
    variances = np.linalg.norm(self.model.means_, axis=1)
```

Tied covariance is identical across all states by construction, so there is
no per-state variance to extract. This substituted the **undirected**
magnitude of each state's mean feature vector (across all 4 features,
including `spy_return` and `yield_curve_spread`, not just volatility-like
features) as a proxy — not directional, and biased toward whichever state
is closer to the sample's global mean. Reproduced with a bear-majority
synthetic sample: labeling came out fully inverted (calm state labeled
`"bear"`, volatile state labeled `"bull"`).

### Reachability

`scripts/audit_regime_model.py --compare` (an operator-facing tool
described in `docs/regime_model_tuning_guide.md`) compares AIC/BIC across
covariance types (its own comparison grid is hardcoded to
`["diag", "full"]`, but a plain, non-`--compare` invocation with
`--cov spherical`/`--cov tied` was always reachable) — if an operator or
future automated tuning process ever adopted `spherical`/`tied` based on
model fit quality alone, this bug would silently activate.

### Also found while root-causing

- The `tied` branch's code comment ("Tied covariance has a single shared
  matrix ... across all states") was itself wrong about hmmlearn's actual
  `covars_` shape (empirically `(n_states, n_features, n_features)`, a
  broadcast copy, not one shared matrix) — harmless since the fixed branch
  doesn't read `covars_` at all, corrected anyway.
- Separate, lower-severity but live/reachable bug: for `n_states >= 4`,
  `identify_states_by_vol()` labeled the highest-variance (worst) state a
  generic `"state_<n-1>"` instead of `"bear"`, because the labels list was
  built by loop position rather than by sorted rank.
  `validation/regime_diagnostics.py::compare_model_configurations` defaults
  to `state_counts=[2, 3, 4]` and `scripts/audit_regime_model.py --compare`
  calls it with no override, so this was already reachable in a shipped
  operator tool.
- The `len(variances) != self.n_states` fallback had no logging at all — a
  CONSTRAINT #6 (fail-closed, never silent) violation, and exactly what let
  the `spherical` bug ship undetected.

## Fix

- `spherical`: read `self.model._covars_` (compact shape, verified
  `(n_states, n_features)`) instead of the public `covars_` getter, reduced
  the same way as `diag` (`.reshape(n_states, -1).sum(axis=1)`).
- `tied`: replaced the undirected mean-vector-norm proxy with a directional
  risk-feature lookup — tries `realized_vol_20d` → `vix_level` →
  `credit_spread` (all "higher mean = riskier") → negated `spy_return`
  ("higher return = safer"), in that priority order, using
  `self.feature_names_`/`self.model.means_`. `yield_curve_spread` is
  deliberately excluded: unlike the others its risk direction is ambiguous
  on the raw level alone (inversion signals risk; level alone does not).
  Falls back to the original undirected norm, with a logged error, only if
  none of the above features are present (e.g. a caller-supplied custom
  `feature_columns` subset stripped every candidate).
- The `min_covar` floor (`np.maximum(variances, self.min_covar)`) is now
  skipped for the `tied` *directional* metric specifically — it's a signed,
  near-zero-centered z-scored mean, and flooring it to `>= min_covar` would
  collapse every below-floor state (roughly half, in a z-scored feature) to
  an identical value, silently reintroducing index-order-dependent ties.
  The floor still applies to `diag`/`full`/`spherical` and to `tied`'s
  undirected fallback (all genuine non-negative variance-like quantities).
- Added `logger.error(...)` before the `np.arange` fallback, naming the
  covariance type and the shape mismatch.
- Fixed `n_states >= 4` labeling: the highest-variance state now always
  gets `"bear"` and the lowest `"bull"`, with generic `"state_<rank>"`
  labels only for states in between. `n_states == 2` (→
  `["bull", "sideways"]`, a deliberately different, pre-existing contract —
  see `tests/test_hmm_synthetic.py::test_identify_states_by_vol_labels_lower_variance_state_as_bull`)
  and `n_states == 3` (→ `["bull", "sideways", "bear"]`) are unchanged.

## A separate finding surfaced during verification: `tied` has a structural regime-detection limitation independent of this fix

Fixing `tied`'s *labeling* did not make `tied` a good choice for volatility
regime detection. `tests/test_hmm_synthetic.py::test_risk_on_probability_higher_in_calm_regime_across_covariance_types`
deliberately excludes `tied` from its parametrization: on synthetic
bull/bear data with realistic within-state variance ratios (calm state
variances ~1e-5–4e-4; turbulent state ~3e-4–25, a >1000x spread), the
`tied`-covariance EM fit collapsed to a single dominant state for both a
purely-calm and a purely-turbulent window — `risk_on_probability == 1.0`
for **both** windows — reproducibly across every `random_state`/`n_inits`
combination tried. This is a structural property of forcing one shared
covariance matrix across states whose defining characteristic IS different
variance, not a labeling bug this PR could or should fix within
`identify_states_by_vol()`. `docs/regime_model_tuning_guide.md`'s
Covariance Structures section now recommends against `tied` for regime
detection even though its labeling is correct.

## Verification

```
python3 -m pytest tests/test_hmm_synthetic.py tests/test_hmm_no_lookahead.py \
  tests/test_macro_hmm_integration.py tests/test_regime_diagnostics.py \
  tests/test_hmm_state_persistence.py -v
```

New tests in `tests/test_hmm_synthetic.py`:
- `test_identify_states_by_vol_semantic_correctness_across_covariance_types`
  (parametrized over all 4 covariance types) — confirmed to FAIL for
  `spherical` against the pre-fix code (guaranteed repro), PASS for all 4
  post-fix.
- `test_risk_on_probability_higher_in_calm_regime_across_covariance_types`
  (parametrized over `diag`/`full`/`spherical`; `tied` excluded and
  documented, see above) — confirmed to FAIL for `tied` pre-fix (on this
  seed/scenario) even against the fix, for the structural reason above, not
  a labeling defect.
- `test_identify_states_by_vol_n4_highest_variance_labeled_bear` — confirmed
  to FAIL pre-fix, PASS post-fix.
- `test_identify_states_by_vol_logs_error_on_variance_length_mismatch` —
  confirmed to FAIL pre-fix (no log emitted), PASS post-fix.

All 15 tests in `tests/test_hmm_synthetic.py` and 39 tests across the
targeted HMM suite pass post-fix.

## Related

- CLAUDE.md's "Gaussian HMM Regime Detector tuning & diagnostics" bullet.
- [`docs/architecture/signal-engines.md`](../architecture/signal-engines.md)'s
  `regime/hmm_regime.py` entry.
- [`docs/regime_model_tuning_guide.md`](../regime_model_tuning_guide.md)'s
  Covariance Structures section.
