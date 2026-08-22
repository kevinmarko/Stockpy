# Task Tracker: Fix HMM regime state mislabeling for spherical/tied covariance types

- [x] Reproduce the `spherical` bug directly against installed `hmmlearn==0.3.3`
- [x] Explore existing test/doc/wiring context (tests, `dto_models.py`, `settings.py`, docs)
- [x] Design and validate fix approach (Plan agent found + I fixed 2 additional real
      defects the original report's suggested fix would have introduced:
      the `min_covar` floor colliding with the new signed `tied` metric, and a
      contradiction in the n_states>=4 label-fix wording that would have broken the
      pinned `n_states==2` contract)
- [x] `git checkout -b fix-hmm-spherical-tied-state-mislabeling`
- [x] Fix `spherical` branch (`_covars_` instead of public `covars_`)
- [x] Fix `tied` branch (`_tied_covariance_risk_proxy()` directional lookup)
- [x] Make `min_covar` floor branch-aware (skip for tied's directional metric)
- [x] Add `logger.error` on the length-mismatch fallback
- [x] Fix `n_states >= 4` label construction; confirm `n_states` 2/3 unchanged
- [x] Fix stale `tied` code comment
- [x] Write new semantic-correctness test, parametrized over all 4 covariance types
- [x] Write new risk_on_probability integration test (diag/full/spherical; `tied`
      excluded and documented — see finding below)
- [x] Write n_states=4 label-position regression test
- [x] Write fallback-logging regression test
- [x] Confirm all 4 new/extended test scenarios FAIL pre-fix (proved repro via
      `git stash` of the code fix, tests kept) and PASS post-fix
- [x] Discover and document separate finding: `tied` covariance has a structural
      regime-detection limitation independent of the labeling fix (EM fit collapses
      to one dominant state on realistic variance ratios)
- [x] Update `docs/architecture/signal-engines.md`
- [x] Update `docs/regime_model_tuning_guide.md`
- [x] Write `docs/known_issues/hmm_regime_state_mislabeling_spherical_tied.md`
- [x] Update `docs/known_issues/README.md` index
- [x] Update `CLAUDE.md` (confirmed `AGENTS.md` auto-synced via hook)
- [x] Run targeted test suite (39/39 pass)
- [x] Run broader hmm/regime/dto_models sweep (417/417 pass, 1 unrelated skip)
- [x] Ruff-lint changed files (0 new findings vs. pre-existing baseline)
- [x] Write `.claude/`-scoped PR artifacts (this file, implementation plan, walkthrough)
- [ ] Open PR against `main`
