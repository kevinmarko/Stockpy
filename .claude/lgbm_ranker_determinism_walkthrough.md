# Walkthrough: fix `lgbm_ranker` training non-determinism

## What was broken

`ml/lgbm_ranker.py::_DEFAULT_PARAMS` — the single base dict every `lgb.LGBMRanker(**params)`
construction in this codebase merges from — set `feature_fraction=0.8`/`bagging_fraction=0.8`
(refreshed every boosting iteration, since `bagging_freq=1`) but never set `random_state`
(or any LightGBM seed alias) anywhere: not in the dict, not in
`LGBMCrossSectionalRanker.__init__`, not in `scripts/train_lgbm.py`. Every training run
therefore drew a genuinely random row/feature subsample, making the model — and every
metric computed downstream of it (Sharpe, DSR, PBO, MaxDD, the `deployable` verdict) —
non-reproducible from one run to the next, even with identical inputs.

This was directly confirmed, not just suspected: the durable `validation_runs` SQLite table
(`~/.stockpy_local/quant_platform.db`) shows two `lgbm_ranker` CPCV runs over the *identical*
date window (2005-01-01 → 2026-08-21) whose Sharpe differed at the 6th significant digit
(`0.6752608019782934` vs `0.6752606339208121`) — a small residual consistent with unseeded
bagging/feature-fraction subsampling averaged across 1365 CPCV paths (large-N averaging
mostly, but not entirely, cancels the per-fold randomness). Separately, most of the much
larger Sharpe swings visible in that same table (-0.57 to +24.9) are a confound, not this
bug: they come from concurrent, independent worktree sessions running the harness with
different `--start`/`--end` windows, not same-window reruns.

A validation deployability gate that isn't reproducible run-to-run isn't trustworthy at
all — this is what motivated the fix.

## The fix

`ml/lgbm_ranker.py`, actual current diff:

```diff
 # ──────────────────────────────────────────────────────────────────────────────
 # Hyper-parameters (Prompt 4.1 spec)
 # ──────────────────────────────────────────────────────────────────────────────
+
+# Fixed seed for reproducible LightGBM training -- same convention as
+# CNN_LSTM_RANDOM_SEED in cnn_lstm_worker.py. Without this, `feature_fraction`/
+# `bagging_fraction` (refreshed every iteration since `bagging_freq=1`) draw a
+# genuinely random row/feature subsample each run, making every
+# `lgb.LGBMRanker(**params)` fit non-deterministic. Confirmed as a real, live
+# problem (not theoretical): the durable `validation_runs` table shows two
+# `lgbm_ranker` CPCV runs over the IDENTICAL date window differing at the 6th
+# significant digit of Sharpe (0.6752608... vs 0.6752606...) -- see
+# docs/VALIDATION_STRATEGY_FIX_LOG.md's lgbm_ranker thread for the full
+# writeup and the (larger, separate) confound this does NOT explain by itself.
+LGBM_RANDOM_SEED = 42
+
 _DEFAULT_PARAMS: dict = {
     "objective": "lambdarank",
     "metric": "ndcg",
@@ -50,6 +63,17 @@ _DEFAULT_PARAMS: dict = {
     "bagging_fraction": 0.8,
     "bagging_freq": 1,
     "verbose": -1,
+    # random_state seeds LightGBM's bagging/feature_fraction/drop/data seeds
+    # internally (LightGBM's `seed` config aliases). `deterministic=True` +
+    # `force_row_wise=True` close LightGBM's own documented second
+    # non-determinism source (histogram-parallelism reduction order / the
+    # row-vs-col-wise auto-selection) per its "Reproducing results" FAQ --
+    # `deterministic=True` alone has a documented training-speed cost, which
+    # is accepted here because a validation deployability gate (PBO/DSR/
+    # Sharpe) that isn't reproducible run-to-run isn't trustworthy at all.
+    "random_state": LGBM_RANDOM_SEED,
+    "deterministic": True,
+    "force_row_wise": True,
 }
```

Three knobs, deliberately together, not just `random_state` alone:

- **`random_state=42`** — seeds LightGBM's internal bagging/feature-fraction/drop/data
  seeds (LightGBM's `seed` config aliases). This is the primary fix.
- **`deterministic=True`** — forces LightGBM's reproducible (but slower) code path.
  Accepted trade-off: a validation gate that silently flips `deployable` between runs is a
  worse cost than slower training.
- **`force_row_wise=True`** — pins LightGBM's row-vs-column-wise histogram construction
  auto-selection, which LightGBM's own "Reproducing results" FAQ names as a *second*,
  independent non-determinism source alongside `deterministic`. Without this, `deterministic`
  alone is not sufficient to guarantee bit-identical results.

`LGBM_RANDOM_SEED` is an unconditional module constant, not a new `settings.py` field —
this mirrors the existing `CNN_LSTM_RANDOM_SEED = 42` precedent in `cnn_lstm_worker.py`
rather than adding a new operator-tunable knob for something that should never vary.

## Why this propagates everywhere

`LGBMCrossSectionalRanker.__init__` merges `params` on top of `_DEFAULT_PARAMS`:

```python
self.params = {**_DEFAULT_PARAMS, **(params or {})}
```

Every call site either passes no `params` override at all, or only overrides
`num_leaves`/`learning_rate`/`n_estimators` — never `random_state`/`deterministic`/
`force_row_wise` — so the fix reaches all three real construction sites in this codebase
without any of them needing their own change:

1. `ml/lgbm_ranker.py` itself — the model's own per-fold CV fits and final fit.
2. `scripts/train_lgbm.py`'s `_CANDIDATE_PARAMS` hyperparameter-search trials.
3. `scripts/refresh_validations.py::_build_lgbm_ranker_adapter`'s per-CPCV-fold retrains
   (constructs `LGBMCrossSectionalRanker()` with no `params` override at all).

## Empirical proof

Two in-process offline `run_training()` calls (the offline `_SyntheticDataEngine` path is
itself seeded/deterministic, isolating LightGBM's own determinism from network/data
variability) produced **bit-identical** results across both runs:

| Metric | Run 1 | Run 2 |
|---|---|---|
| `dsr` | 0.9812207805846127 | 0.9812207805846127 |
| `pbo` | 0.14285714285714285 | 0.14285714285714285 |
| `mean_oos_sharpe` | 2.857031446734973 | 2.857031446734973 |
| `deployable` | True | True |

Before the fix, this same double-run would have differed (as directly observed in the live
`validation_runs` table evidence above). `ml/registry.yaml`'s `lgbm_ranker` entry was
updated by this same run (`trained_date: '2026-08-22'`, `cpcv_dsr: 0.9812207805846127`,
`pbo: 0.14285714285714285`, `hyperparameters.random_state: 42`,
`hyperparameters.deterministic: true`, `hyperparameters.force_row_wise: true`), corroborating
that the numbers above came from a real run against this fix, not a hand-typed estimate.

## What's still in progress / pending at the time of this walkthrough

Being upfront about the current state rather than claiming full completion:

- **Regression test**: `tests/test_lgbm_ranker_native_cv.py::TestReproducibility` (3 tests
  — fixed-seed params present, bit-identical predictions on the native-MultiIndex-CV path,
  bit-identical predictions on the flatten path) is written and verified passing:
  `python3 -m pytest tests/test_lgbm_ranker_native_cv.py -q` → **19 passed**, zero failures,
  including all pre-existing coverage in the file.
- **Full canonical validation re-run** — `python -m scripts.refresh_validations
  --strategies lgbm_ranker --start 2005-01-01 --output-dir reports --n-cpcv-splits 15
  --n-test-splits 4 --workers 1 --json` is running in the background (PID 16419, confirmed
  alive via `ps -p 16419`), log at `/tmp/validation_runs/lgbm_ranker_seedfix.log`. This run
  has historically taken ~2 hours wall-clock (1365 CPCV paths), and was only a few minutes
  into per-fold training at the time of writing — **its real Sharpe/PBO/DSR/MaxDD/
  `deployable` numbers are not yet available and are not reported here or in the fix-log
  docs.** No number has been estimated or fabricated in its place.
- **Documentation**: `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s existing `lgbm_ranker` thread
  still carries its prior `PENDING` marker, and `docs/signals/lgbm_ranker.md`'s matching
  Backtest Validation follow-up section is likewise not yet updated — both are blocked on
  the full validation re-run above landing with real numbers.
- **Investigation note** on `cross_sectional_momentum`/`sector_quality_rank`
  non-determinism (a documented *finding*, not a fix, per the approved plan's scope) has
  not yet been added.
- **Commit / push / PR**: not yet done — all three files above are still modified and
  uncommitted on the `fix-lgbm-ranker-nondeterminism` branch.

See `.claude/lgbm_ranker_determinism_task.md` for the itemized, checkbox-tracked status of
every step, and `.claude/lgbm_ranker_determinism_implementation_plan.md` for the full
approved plan this change follows.
