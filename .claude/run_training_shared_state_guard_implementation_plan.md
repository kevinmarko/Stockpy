# Fix: require explicit opt-in for `run_training()` to write shared state

## Context

During earlier work in this session (PR #857, `fix-lgbm-ranker-nondeterminism`), a quick
ad hoc `scripts.train_lgbm.run_training(_DEFAULT_TICKERS, offline=True)` call — issued
directly as a verification step, not a real training run — silently wrote synthetic-run
metrics into the MACHINE-GLOBAL `ml/registry.yaml` (`ml/registry_io.py`'s
`update_model_metrics(path=None)` dual-persistence writes to both
`settings.LOCAL_DATA_ROOT/ml_models/registry.yaml` and the repo-tracked
`ml/registry.yaml`, shared by every git worktree on this machine) and left a bogus model
pickle in the shared `ml_models/` directory that `LGBMCrossSectionalRanker.load_latest()`
would have picked up over the real production model. Caught and reverted by hand in the
same session, but the underlying mechanism was left unfixed and flagged as a known risk.
This closes that gap.

## Approach

`scripts/train_lgbm.py::run_training()` gains a required, explicit
`confirm_shared_write: bool = False` keyword. Whenever `save_path` and/or
`registry_path` are left at their default `None`, the function now raises `ValueError`
immediately — before any network/training work starts — unless the caller passes
`confirm_shared_write=True`.

The two genuine, deliberate production callers pass it explicitly:
- `scripts/train_lgbm.py`'s own CLI `main()`.
- `scripts/retrain_models.py`'s scheduled retraining job.

Every existing test in `tests/test_train_lgbm.py` already passes explicit, isolated
`save_path`/`registry_path` values — one exception
(`test_default_save_path_is_dated_not_mutable_latest`, which deliberately leaves
`save_path=None` to test that specific parameter-passing contract while mocking
`.save()` entirely) needed `confirm_shared_write=True` added alongside its
already-isolated `registry_path`.

## Files touched

- `scripts/train_lgbm.py`: the guard + `main()`'s call site.
- `scripts/retrain_models.py`: call site.
- `tests/test_train_lgbm.py`: new `TestSharedStateGuard` class (5 tests); one existing
  test updated to opt in.
- `docs/architecture/ml-and-reports.md`, `docs/VALIDATION_STRATEGY_FIX_LOG.md`:
  documentation, including an honest account of a second, smaller incident this fix's
  own first-draft regression test caused (see the walkthrough for detail) — a real
  demonstration of how easy this class of mistake is, closed by mocking the I/O calls
  directly rather than trying to redirect every internal path-resolution branch.

## Verification

- `tests/test_train_lgbm.py`: 24/24 passed.
- `tests/test_retrain_models.py`: 11/11 passed (unaffected — `run_training` fully
  mocked there already).
- Broader sweep (`test_control_api.py`, `test_orchestrator_runner.py`,
  `test_pilots_strategy_matrix.py`, `test_registry_load.py`, `test_retrain_models.py`,
  `test_scripts_bootstrap.py`, `test_train_meta_labelers.py`, `test_train_lgbm.py`):
  303/303 passed.
- After every test run in this session, both the real machine-global
  `~/.stockpy_local/ml_models/registry.yaml` and the repo-tracked `ml/registry.yaml`
  were directly diffed/inspected to confirm zero pollution — not merely assumed clean
  from the tests passing.
- Ruff: zero net-new lint errors (baseline on `main` for the three touched files: 46
  errors pre-existing; identical 46 post-change).
