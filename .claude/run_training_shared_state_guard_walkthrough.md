# Walkthrough: run_training() shared-state guard

## The incident

Earlier in this session, an ad hoc verification step —

```python
from scripts.train_lgbm import run_training, _DEFAULT_TICKERS
run_training(_DEFAULT_TICKERS, offline=True)
```

— called with no `save_path`/`registry_path` override, silently:
1. Wrote synthetic-run metrics (`dsr=0.98...`, `n_train=250`, a tiny offline panel) into
   the **machine-global** `~/.stockpy_local/ml_models/registry.yaml`'s `lgbm_ranker`
   entry, overwriting the real production model's recorded metrics.
2. Also wrote the identical mutated entry into the **repo-tracked** `ml/registry.yaml`
   (git-tracked — would have shown up as an unexplained diff in any subsequent commit).
3. Saved a bogus `lgbm_20260822.pkl` into the shared `ml_models/` directory, which
   `LGBMCrossSectionalRanker.load_latest()` (a plain glob-sort of dated filenames) would
   have picked up as "the latest model" over the real one.

Caught and reverted by hand before anything was committed. The fix below closes the gap
so this can't happen silently again.

## The fix

```python
def run_training(
    tickers, *, offline=False, save_path=None, registry_path=None,
    data_engine=None, historical_store=None,
    confirm_shared_write: bool = False,
) -> dict:
    if (save_path is None or registry_path is None) and not confirm_shared_write:
        raise ValueError(
            "run_training() called with save_path and/or registry_path left as "
            "None, and confirm_shared_write is not True. This combination would "
            "write to the MACHINE-GLOBAL model registry/artifact directory under "
            "settings.LOCAL_DATA_ROOT, shared by every git worktree on this "
            "machine. ..."
        )
    ...
```

Real, deliberate callers opt in explicitly:

```python
# scripts/train_lgbm.py::main() -- this CLI's entire purpose is a real training run.
summary = run_training(tickers, offline=..., save_path=..., registry_path=...,
                        confirm_shared_write=True)

# scripts/retrain_models.py -- scheduled retraining job, same reasoning.
summary = run_training(lgbm_tickers, offline=offline, registry_path=registry_path,
                        confirm_shared_write=True)
```

## Why "either is None", not "both are None"

The original incident had BOTH left `None`. But `save_path=None` alone still writes the
model pickle to the shared `_MODELS_DIR` (a module constant in `ml/lgbm_ranker.py`), and
`registry_path=None` alone still writes to both shared registry locations regardless of
`save_path`. Either one being `None` is independently dangerous, so the guard fires on
either.

## The second incident — while writing this fix's own test

Worth recording honestly: the first draft of the "does the escape hatch actually work"
test tried to PROVE `confirm_shared_write=True` genuinely lets a caller through by
**redirecting** every path the call would touch — monkeypatching
`settings.LOCAL_DATA_ROOT` and `ml.lgbm_ranker._MODELS_DIR` to an isolated `tmp_path`,
then calling `run_training(..., confirm_shared_write=True)` with no path overrides.

The model pickle write was correctly isolated (confirmed via the returned `model_path`).
But the test still leaked a write into the **real** machine-global `ml/registry.yaml` —
caught by directly diffing the real file after the test run, not by trusting the test's
own pass/fail. Root cause: `ml/registry_io.py::update_model_metrics(path=None)` writes
to `[get_local_registry_path(), _DEFAULT_REGISTRY_PATH]`. The first target is
settings-driven and was correctly redirected. The second,
`_DEFAULT_REGISTRY_PATH = Path(__file__).parent / "registry.yaml"`, is a **hardcoded
constant, entirely independent of `settings.LOCAL_DATA_ROOT`** — exactly the same
mechanism behind the original incident.

Fixed by abandoning path-redirection for that test entirely: mock
`LGBMCrossSectionalRanker.save` and `update_model_metrics` directly, and assert they were
called with `path=None` (proving the guard didn't block the call). This cannot touch real
shared state regardless of how many internal path-resolution branches exist — sidestepping
the need to enumerate every one of them correctly.

**Lesson for future guard/isolation tests in this codebase**: mocking the actual I/O call
is more robust than trying to redirect every path a function might resolve internally —
prefer it whenever the function under test has more than one settings-independent write
target.

## What was verified

- `tests/test_train_lgbm.py::TestSharedStateGuard`: 5/5 passed (both-None raises before
  any work starts — verified via a canary that raises if the data engine is ever
  constructed; either-alone raises; both-explicit never requires the flag; the escape
  hatch works, verified via mocking).
- Full `tests/test_train_lgbm.py`: 24/24 passed.
- Broader sweep (8 files): 303/303 passed.
- After every test run, the real machine-global and repo-tracked registry files were
  directly read/diffed to confirm zero pollution — not merely inferred from green tests.
