# Task tracker: `lgbm_ranker` training non-determinism fix

Branch: `fix-lgbm-ranker-nondeterminism`. PR: https://github.com/kevinmarko/Stockpy/pull/857.
Status as of 2026-08-22, final.

- [x] Add `LGBM_RANDOM_SEED = 42` + `random_state`/`deterministic`/`force_row_wise` to
      `ml/lgbm_ranker.py::_DEFAULT_PARAMS`.
- [x] Empirical verification (in-process offline double-run) — bit-identical
      `dsr=0.9812207805846127`, `pbo=0.14285714285714285`, `mean_oos_sharpe=2.857031446734973`,
      `deployable=True` confirmed across two runs.
- [x] Independent adversarial re-verification via a different code path (adapter-level
      `strategy_fn` called twice on frozen synthetic data, plus a full
      `StrategyValidationHarness.run()` end-to-end called twice) — bit-identical at both
      levels.
- [x] Regression test added to `tests/test_lgbm_ranker_native_cv.py` —
      `TestReproducibility` class (3 tests). `pytest tests/test_lgbm_ranker_native_cv.py
      tests/test_train_lgbm.py tests/test_lgbm_no_leakage.py
      tests/test_lgbm_purged_integration.py tests/test_lgbm_feature_pit.py -q` → **49
      passed, 0 failed**. Reproducibility test re-run 3x — not flaky.
- [x] Full canonical validation re-run
      (`python -m scripts.refresh_validations --strategies lgbm_ranker --start 2005-01-01
      --output-dir reports --n-cpcv-splits 15 --n-test-splits 4 --workers 1 --json`) —
      **completed** 2026-08-22 12:40:02 (~2h25m wall-clock). Real result:
      `sharpe=0.41962450878656576`, `dsr=0.8404066793562799`, `pbo=0.0`,
      `max_drawdown=0.024846641363470222`, `deployable=False` (`DSR 0.84<0.95, Sharpe
      0.42<0.50`). Confirmed independently via `reports/lgbm_ranker_validation_summary.json`
      and the durable `validation_runs` DB row.
- [x] Investigation note on `cross_sectional_momentum`/`sector_quality_rank`
      non-determinism — completed by a parallel agent, confirmed with file:line evidence
      that both FMP's and EDGAR's per-host rate limiters are process-local, not
      cross-process, so concurrent worktree sessions on this machine can jointly exceed
      the shared request budget. Documented as a flagged-for-future-investigation finding,
      not fixed (out of scope).
- [x] Documentation: `docs/VALIDATION_STRATEGY_FIX_LOG.md` appended to the existing
      `lgbm_ranker` thread — the seed bug, DB evidence, the fix, empirical proof, the
      cross_sectional_momentum/sector_quality_rank finding, and (in a follow-up commit
      once the background run landed) the real, measured post-fix numbers.
- [x] Documentation: `docs/signals/lgbm_ranker.md` Backtest Validation follow-up appended
      with the same resolved numbers.
- [x] Housekeeping: an early empirical-verification script accidentally wrote synthetic
      offline-run metrics into the *shared, machine-global* `ml/registry.yaml`
      (`settings.LOCAL_DATA_ROOT/ml_models/registry.yaml`, used across every worktree on
      this machine) and left a bogus `lgbm_20260822.pkl` model artifact that
      `load_latest()` would have shadowed the real production model with. Caught and
      reverted before committing: the machine-global registry's `lgbm_ranker` entry was
      restored to its real pre-pollution values, the bogus pickle deleted, and the
      worktree's own tracked `ml/registry.yaml` was `git checkout`'d clean (this also
      preserved unrelated concurrent updates other worktree sessions had made to other
      registry entries in the shared file).
- [x] Commit, push branch, open PR — done:
      https://github.com/kevinmarko/Stockpy/pull/857 (plus a follow-up commit landing the
      real validation numbers once the background run completed).
- [x] CI: `webapp (Pilots PWA)` failed on an unrelated, pre-existing flaky test
      (`StrategyMatrix.test.tsx`, a `fireEvent.change`/React re-render timing race — this
      PR touches zero webapp files). Confirmed non-reproducible locally (8x targeted runs
      + 1x full 1845-test suite, all green); re-ran the CI job rather than blindly editing
      unrelated code.
