# Implementation Plan: Fix Fabricated PBO/DSR for Zero-Sample options_meta_labeler

## Objective

`get_model_registry_status` reported `options_meta_labeler` as `Training Samples: 0`,
`CPCV DSR: 0.0`, `PBO: 1.0`, `Deployability: NOT DEPLOYABLE` — a model trained on zero samples
was rendering a numeric CPCV result it never actually produced. Per this repo's CONSTRAINT #4
("never fabricate a metric — degrade to an honest reason string instead"), a model that was
never evaluated must say so, not report a fabricated 0.0/1.0.

## Root-cause investigation

1. Grepped `options_meta_labeler` across `scripts/` and `ml/` — no automated CPCV training
   script exists for this model (unlike `lgbm_ranker`/`meta_labeler_*`, which go through
   `scripts/train_lgbm.py` / `scripts/train_meta_labelers.py` →
   `ml.registry_io.update_model_metrics`, which already writes honest `None` when CPCV can't
   run). `api/pilots_api.py`'s `/pilots/options/meta-model/retrain` trains
   `global_options_meta_labeler` in-sample only and never touches `ml/registry.yaml` at all.
2. `git log -p -- ml/registry.yaml` traced the entry to commit `693f3717` ("chore: sync
   main-checkout work..."), where it was hand-typed with `cpcv_dsr: 0.0`, `pbo: 1.0`,
   `n_train: 0` — never produced by any of this repo's honesty-enforcing writers.
3. Audited the layers most likely to hide a real degenerate-input bug:
   - `ml/registry_io.py::update_model_metrics` / `compute_deployable` — already honest
     (`None` in → `deployable=False`, never fabricates).
   - `validation/metrics.py::deflated_sharpe_ratio` / `probability_of_backtest_overfitting` —
     already return `NaN` on degenerate/empty input, never a fabricated 0.0/1.0.
   - `scripts/train_meta_labelers.py::compute_cpcv_metrics` — already returns all-`None` when
     the event set is too small.
   All three are correct. **The fabrication originated purely from a hand-authored registry
   row, not from a computation bug.**
4. Read `investyo_mcp_server.py::get_model_registry_status` — it renders `cpcv_dsr`/`pbo`
   whenever they are not `None`, and a plain `✅/❌ DEPLOYABLE` off `deployable` with no way to
   distinguish "never ran" from "ran and failed."

## Scope

1. `ml/registry.yaml`: `options_meta_labeler.cpcv_dsr`/`pbo` → `null` (matching
   `update_model_metrics`'s own `None` convention); `notes` extended to explain why.
2. `investyo_mcp_server.py::get_model_registry_status`: when `cpcv_dsr` or `pbo` is `None`,
   render `⚠️ NOT EVALUATED (not evaluated — N training samples)` for Deployability, CPCV DSR,
   and PBO instead of a fabricated number or a `❌ NOT DEPLOYABLE` that would read identically
   to a model that genuinely ran CPCV and failed the 0.95/0.50 gate.
3. `tests/test_investyo_mcp_server.py`: two new tests in `TestGetModelRegistryStatus` proving
   (a) a zero-sample model reports "not evaluated" with no fabricated 0.0/1.0 anywhere in the
   output, and (b) a genuinely-evaluated-and-failed model (real non-null `cpcv_dsr`/`pbo`,
   `deployable=false`) is unaffected — it still renders its real numbers and `❌ NOT DEPLOYABLE`.
4. Sync the machine-local runtime mirror (`~/.stockpy_local/ml_models/registry.yaml`, outside
   git — `ml/registry_io.py`'s dual-persistence target) the same way, since `load_registry()`'s
   repo/local merge prefers whichever side has the newer-or-equal `trained_date`, and this
   entry's `trained_date` isn't changing (no real retrain happened) — a repo-only fix would be
   silently shadowed on any machine that already has this stale local copy.

## Design decision: "not evaluated" vs. "evaluated and failed"

`deployable: false` means the same thing in the registry schema whether CPCV never ran or it
ran and failed the gate — only the presence of `None` in `cpcv_dsr`/`pbo` distinguishes the two.
Conflating them would hide a real, measured failure (e.g. `meta_labeler_timeseries_momentum`'s
DSR=0.10/PBO=0.80) behind the same "not deployable" language used for "no data yet." The fix
therefore branches on `cpcv_dsr is None or pbo is None` specifically, independent of the
`deployable` flag, so:
- zero-sample / never-evaluated → `⚠️ NOT EVALUATED (not evaluated — N training samples)`,
  no numeric CPCV DSR/PBO rendered.
- genuinely evaluated and failed → unchanged `❌ NOT DEPLOYABLE` plus the real DSR/PBO numbers.

## Verification

```
.venv/bin/python3 -m pytest tests/test_investyo_mcp_server.py -k TestGetModelRegistryStatus -q
.venv/bin/python3 -m pytest tests/test_pbo.py tests/test_dsr.py tests/test_registry_load.py -q
```

Plus a live check of `investyo_mcp_server.get_model_registry_status()` against the real
`ml/registry.yaml` and the synced `~/.stockpy_local/ml_models/registry.yaml` mirror, confirming
the rendered output contains `NOT EVALUATED (not evaluated — 0 training samples)` and no
`CPCV DSR**: 0.0` / `PBO**: 1.0` anywhere.
