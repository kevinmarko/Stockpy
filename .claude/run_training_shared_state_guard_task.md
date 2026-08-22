# Task tracker: run_training() shared-state guard

Branch: `fix-run-training-shared-state-guard`.

- [x] `scripts/train_lgbm.py::run_training()` gains `confirm_shared_write: bool = False`,
      raises `ValueError` before any work when `save_path`/`registry_path` are None and
      the flag isn't set.
- [x] `scripts/train_lgbm.py::main()` passes `confirm_shared_write=True`.
- [x] `scripts/retrain_models.py`'s call site passes `confirm_shared_write=True`.
- [x] `tests/test_train_lgbm.py::TestSharedStateGuard` — 5 new tests (both-None raises
      before work starts, either-alone raises, both-explicit never requires the flag,
      the escape hatch itself works — via mocking, not path-redirection, after the
      redirection approach itself leaked a write into real shared state on first try).
- [x] `test_default_save_path_is_dated_not_mutable_latest` updated to opt in.
- [x] Full `tests/test_train_lgbm.py`: 24/24 passed.
- [x] Broader regression sweep (8 files touching train_lgbm/registry_io/retrain_models):
      303/303 passed.
- [x] Verified zero real-shared-state pollution after every test run (direct diff/read
      of both the repo-tracked and machine-global registry files, not just trusting
      green tests).
- [x] Ruff: zero net-new lint errors.
- [x] Documentation: `docs/architecture/ml-and-reports.md` new bullet.
- [x] Documentation: `docs/VALIDATION_STRATEGY_FIX_LOG.md` new entry, including an
      honest account of the second incident this fix's own test caused during
      development.
- [ ] Commit, push branch, open PR.
