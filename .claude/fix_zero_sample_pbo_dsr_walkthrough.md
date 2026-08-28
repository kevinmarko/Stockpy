# Walkthrough: Fix Fabricated PBO/DSR for Zero-Sample options_meta_labeler

## What changed and why

`get_model_registry_status` (the MCP tool that reads `ml/registry.yaml`) reported the
`options_meta_labeler` model as `Training Samples: 0`, `CPCV DSR: 0.0`, `PBO: 1.0`,
`Deployability: NOT DEPLOYABLE`. A model trained on zero samples cannot have a real CPCV
result — those numbers were fabricated, violating this repo's CONSTRAINT #4 ("never fabricate a
metric — degrade to an honest reason string instead").

**Root cause**: `ml/registry.yaml`'s `options_meta_labeler` entry was hand-typed in commit
`693f3717` ("chore: sync main-checkout work...") with `cpcv_dsr: 0.0`/`pbo: 1.0` alongside
`n_train: 0`. Unlike `lgbm_ranker`/`meta_labeler_*`, there is no automated CPCV training script
for this model — `api/pilots_api.py`'s `/pilots/options/meta-model/retrain` only trains
`global_options_meta_labeler` in-sample and never touches the registry. So this row never passed
through `ml.registry_io.update_model_metrics`, the shared writer that already honestly stores
`None` when CPCV can't run. The fabrication was in the data, not in any CPCV/PBO/DSR
computation — `validation/metrics.py` and `scripts/train_meta_labelers.py::compute_cpcv_metrics`
were all independently confirmed to already return `None`/`NaN` on degenerate/empty input.

## Files changed

### `ml/registry.yaml`
- `options_meta_labeler.cpcv_dsr`/`pbo`: `0.0`/`1.0` → `null` (matches
  `update_model_metrics`'s own `None` convention for an unevaluable metric).
- `notes` extended: "Not yet trained on any paper trades (n_train=0) -- cpcv_dsr/pbo are null
  rather than a fabricated 0.0/1.0 (CONSTRAINT #4); this is 'not evaluated,' not 'evaluated and
  failed.'"

### `investyo_mcp_server.py::get_model_registry_status`
- Hoisted `cpcv_dsr`/`pbo`/`n_train` reads to the top of the per-model rendering block.
- New `not_evaluated_reason`: set whenever `cpcv_dsr is None or pbo is None` — `"not evaluated —
  N training samples"` when `n_train <= 0`, else a generic `"not evaluated — CPCV metrics
  unavailable"` fallback.
- Deployability line: `⚠️ NOT EVALUATED ({reason})` when `not_evaluated_reason` is set, else the
  original `✅/❌ DEPLOYABLE` off `deployable`.
- CPCV DSR / PBO lines: render `{reason}` in place of a number when not evaluated; otherwise
  unchanged (only rendered when non-`None`, as before).
- Removed the now-duplicate second `n_train = meta.get("n_train")` read further down.

### `tests/test_investyo_mcp_server.py` (`TestGetModelRegistryStatus`)
- `test_zero_sample_model_not_evaluated_not_fabricated`: a `null`/`null`/`n_train=0` entry
  renders `NOT EVALUATED` + `"0 training samples"`, contains no `CPCV DSR**: 0.0` or
  `PBO**: 1.0` substring, and does **not** render `❌ NOT DEPLOYABLE`.
- `test_evaluated_and_failed_keeps_numeric_metrics`: a real, genuinely-failed entry
  (`cpcv_dsr=0.10`, `pbo=0.80`, `n_train=500`, `deployable=false`) still renders its actual
  numbers and `❌ NOT DEPLOYABLE`, and never renders `NOT EVALUATED` — proving the two states
  stay distinguishable in both directions.

## Key design decision: "not evaluated" vs. "evaluated and failed"

`deployable: false` is identical in the registry schema for both a model that never ran CPCV and
one that ran CPCV and failed the 0.95 DSR / 0.50 PBO gate. Only `cpcv_dsr`/`pbo` being `None`
distinguishes them. The renderer branches on that `None` check specifically (not on
`deployable`), so a real measured failure — e.g. `meta_labeler_timeseries_momentum`'s
`DSR=0.10`/`PBO=0.80` in the existing `test_production_shaped_registry` test — keeps showing its
actual numbers and `❌ NOT DEPLOYABLE`, while a genuinely-untrained model now says so honestly
instead of borrowing that same "failed" language.

## Runtime mirror sync (outside git)

`ml.registry_io.update_model_metrics` dual-persists to both the repo-tracked `ml/registry.yaml`
and a machine-global mirror at `~/.stockpy_local/ml_models/registry.yaml`
(`settings.LOCAL_DATA_ROOT`), and `load_registry()` merges the two per-model, preferring
whichever side has the newer-or-equal `trained_date`. Since this fix doesn't represent a new
training run, `trained_date` is unchanged — meaning the stale local mirror (found to still hold
the old `cpcv_dsr: 0.0`/`pbo: 1.0`) would otherwise keep winning the merge and silently shadow
the repo fix on any machine that already had it cached. Directly edited that file's
`options_meta_labeler` entry to match (`cpcv_dsr`/`pbo` → `null`, notes synced) — a plain file
edit outside the git repo, not a commit.

## Verification results

```
.venv/bin/python3 -m pytest tests/test_investyo_mcp_server.py -k TestGetModelRegistryStatus -q
8 passed

.venv/bin/python3 -m pytest tests/test_pbo.py tests/test_dsr.py tests/test_registry_load.py -q
29 passed
```

Both re-run clean after `git rebase origin/main` (picked up ~15 unrelated upstream commits —
fundamentals-deadline fix, pipeline-timeout fix, module-efficiency audit docs, universe_engine
`iterrows` optimization — no conflicts, diff unchanged).

Live end-to-end check (`investyo_mcp_server.get_model_registry_status()` against the real
registry files) after both the repo fix and the local-mirror sync:

```
## options_meta_labeler
- **Role**: options_meta_labeler
- **Last Trained**: 2026-08-22
- ✅ Fresh (6 days old)
- **Deployability**: ⚠️ NOT EVALUATED (not evaluated — 0 training samples)
- **CPCV DSR**: not evaluated — 0 training samples
- **PBO**: not evaluated — 0 training samples
- **Training Samples**: 0
```

No fabricated `0.0`/`1.0` anywhere in the output; `deployable=False` unchanged, matching
`compute_deployable(None, None) -> False` from `ml/registry_io.py`.

## What a human should sanity-check

- The generic-fallback branch of `not_evaluated_reason` ("CPCV metrics unavailable", used when
  `n_train` is `None`/positive but a metric is still `None`) has no direct test coverage in this
  change — only the `n_train<=0` branch was exercised, since that's the confirmed bug shape.
- This fix does not add an automated CPCV training path for `options_meta_labeler` — that
  remains a separate follow-up if the model is ever meant to clear the deployability gate for
  real. Today it correctly, honestly reports "not evaluated" rather than any pass/fail.
