# Implementation Plan: Fix HMM regime state mislabeling for spherical/tied covariance types

Branch: `fix-hmm-spherical-tied-state-mislabeling`

## Context

`regime/hmm_regime.py::HMMRegimeDetector.identify_states_by_vol()` ranks the
Gaussian HMM's hidden states by fitted variance (ascending) and labels the
lowest-variance state `"bull"`, the highest `"bear"`. `predict_proba()`
turns this into `hmm_risk_on_probability`, which feeds
`MacroEconomicDTO.killSwitch`'s HMM-agreement fast-trigger and
`market_regime`'s downgrade logic (`dto_models.py`) — this labeling
directly affects whether the platform's crash-sensitivity kill-switch
escalation engages.

A direct repro against the installed `hmmlearn==0.3.3` confirmed two real
bugs in the covariance-type branch: `spherical` reads a public `covars_`
getter that returns a malformed shape (silently tripping an unlogged
arbitrary-index fallback on every fit), and `tied` ranked states by an
undirected mean-vector norm instead of a directional risk proxy. A
synthetic repro showed a genuine crash regime mislabeled `"bull"`.

Full root-cause detail, fix rationale, and verification:
[`docs/known_issues/hmm_regime_state_mislabeling_spherical_tied.md`](../docs/known_issues/hmm_regime_state_mislabeling_spherical_tied.md).

## Changes made

### `regime/hmm_regime.py`

1. `spherical`: reads `self.model._covars_` (compact internal shape)
   instead of the public `covars_` getter, reduced the same way as `diag`.
2. `tied`: new `_tied_covariance_risk_proxy()` helper — directional lookup
   over `realized_vol_20d` → `vix_level` → `credit_spread` → negated
   `spy_return`, falling back to the original undirected norm (with a
   logged error) only if none are present.
3. `min_covar` floor made branch-aware — skipped for `tied`'s new signed
   directional metric (flooring it would reintroduce ties).
4. Added `logger.error(...)` before the `np.arange` length-mismatch
   fallback (was completely silent).
5. Fixed `n_states >= 4` label construction (highest-variance state now
   correctly `"bear"`, not a generic `"state_<n-1>"`); `n_states` 2 and 3
   are byte-identical to before.
6. Corrected the stale `tied` code comment.

### `tests/test_hmm_synthetic.py`

- `test_identify_states_by_vol_semantic_correctness_across_covariance_types`
  (parametrized `diag`/`full`/`spherical`/`tied`) — confirmed FAILS for
  `spherical` pre-fix, PASSES for all 4 post-fix.
- `test_risk_on_probability_higher_in_calm_regime_across_covariance_types`
  (parametrized `diag`/`full`/`spherical`; `tied` deliberately excluded and
  documented — see "Separate finding" below).
- `test_identify_states_by_vol_n4_highest_variance_labeled_bear` — confirmed
  FAILS pre-fix, PASSES post-fix.
- `test_identify_states_by_vol_logs_error_on_variance_length_mismatch` —
  confirmed FAILS pre-fix (no log), PASSES post-fix.

### Docs

- `docs/architecture/signal-engines.md`: corrected the
  `identify_states_by_vol()` description (previously described the buggy
  behavior as intentional).
- `docs/regime_model_tuning_guide.md`: fleshed out the Covariance
  Structures section to cover all 4 types, including the `tied` structural
  caveat below.
- New `docs/known_issues/hmm_regime_state_mislabeling_spherical_tied.md`
  + index row in `docs/known_issues/README.md`.
- `CLAUDE.md` addendum to the existing HMM bullet (AGENTS.md auto-synced
  via `sync_agent_docs.sh`, confirmed).

## Separate finding surfaced during verification

Fixing `tied`'s labeling did not make it a good choice for volatility
regime detection: on synthetic bull/bear data with realistic within-state
variance ratios, the `tied`-covariance EM fit collapsed to a single
dominant state for both a calm and a turbulent window
(`risk_on_probability == 1.0` for both), reproducibly across every
`random_state`/`n_inits` tried — a structural property of sharing one
covariance matrix across states whose defining characteristic is different
variance, not something fixable within `identify_states_by_vol()`. Documented
in the known-issues write-up and the tuning guide; `tied` is now recommended
against for regime detection even though its labeling is correct.

## Verification (all run and passing)

```
python3 -m pytest tests/test_hmm_synthetic.py tests/test_hmm_no_lookahead.py \
  tests/test_macro_hmm_integration.py tests/test_regime_diagnostics.py \
  tests/test_hmm_state_persistence.py -v
# 39 passed

python3 -m pytest tests/ -k "hmm or regime or macro_dto or dto_models" -q
# 417 passed, 1 skipped (unrelated), 11649 deselected

ruff check regime/hmm_regime.py tests/test_hmm_synthetic.py
# 25 pre-existing findings, unchanged before/after this diff (0 new)
```
