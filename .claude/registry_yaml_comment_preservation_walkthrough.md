# Walkthrough — `ml/registry.yaml` comment preservation

## What was broken

`ml/registry_io.py::_dump_registry` — the single write choke point every registry caller
goes through (`scripts/train_lgbm.py`, `scripts/train_meta_labelers.py`,
`ml/forecast_backfill_registry_bridge.py` via `update_model_metrics`, plus
`load_registry`'s own self-sync of the `LOCAL_DATA_ROOT` copy) — did:

```python
body = yaml.safe_dump(data, ...)
f.write(_REGISTRY_HEADER)   # hardcoded duplicate of the file's real header
f.write(body)
```

PyYAML drops comments, so the header had to be re-attached. It was re-attached from a
**hardcoded constant in the source file**, which had drifted: it was missing the 4-line
explanation of why `cpcv_mean_oos_max_dd` is null for `meta_labeler_*` roles. Every write
therefore replaced 37 real comment lines with 33 stale ones.

The module docstring asserted the header was re-emitted "verbatim". It was not. A
hand-maintained duplicate is the mechanism of the drift, not a mitigation for it.

Confirmed recurrence of a documented bug class — CLAUDE.md's Forecast Backfill Meta-Labeler
Bridge bullet records the same file losing its whole header once before.

## What changed

### `ml/registry_io.py`

- **`_splice_registry_text(text, data)`** (new). Applies the update surgically:
  re-serializes only the `models.<key>` blocks whose parsed values actually changed and
  copies every other byte through verbatim. Bails out returning `None` — never a
  partially-applied write — on any shape it does not fully recognise (a change outside
  `models`, a removed model, duplicate keys, a flow-style mapping). Then **re-parses its
  own output and compares it to the intended `data`**; a splice that would change meaning
  is rejected rather than written (CONSTRAINT #6).
- **`_dump_registry`** now prefers the splice; on decline (or a brand-new file) it falls
  back to a full dump that re-emits the **real on-disk header**, read back from the file.
- **`_REGISTRY_HEADER`** is now bootstrap-only (a file that does not exist yet), re-synced
  with the current header, and pinned against `ml/registry.yaml` by a test.
- Supporting helpers: `_leading_comment_block`, `_find_models_blocks`,
  `_serialize_model_block`, `_dump_yaml`.
- Module docstring's false "NEVER hand-splice YAML text" design rule replaced with what the
  writer actually does now, and why.

### `tests/test_registry_yaml_comment_preservation.py` (new, 10 tests)

Core invariant: **write to a temp copy of the real registry, update one model's metrics,
assert every `#` line present before is present after** (and in the same order).

Plus: the exact 4-line sentinel the 2026-09-04 retrain deleted; untouched model blocks stay
byte-identical; a hand-added inline comment survives; a realistic retrain's diff is confined
to the changed scalar lines; the fallback path keeps the file's real header rather than
resurrecting the constant; a no-op write leaves the file byte-identical; the bootstrap
constant is pinned in sync; and two controls confirming the values are still genuinely
written and a fresh file still gets a header.

## Verification — actually run, not assumed

**The test was written first and confirmed failing against the unfixed writer.** Method:
saved the fixed file aside, `git checkout HEAD -- ml/registry_io.py`, ran the suite, restored.

| | Original writer | With the fix |
|---|---|---|
| `test_registry_yaml_comment_preservation.py` | **8 failed, 2 passed** | **10 passed** |

The 2 that pass either way are deliberate controls (`..._still_writes_the_real_values`,
`..._fresh_file_bootstrap_emits_the_header`) — they are not the bug.

The first failure output named the defect exactly:
`first extra item: '#   meta_labeler_* roles: their CPCV returns are discrete per-event R-multiples'`.

**No regressions:**
`tests/test_registry_load.py` + `tests/test_train_lgbm.py` + `tests/test_train_meta_labelers.py`
+ the new file → **84 passed**.
(`tests/test_analytics_signals.py` was excluded: it fails at *collection* with a pre-existing,
unrelated `numba`/`pandas_ta_classic` caching error, identically before and after this change.)

**Genuine-bug lint:** `ruff check ml/registry_io.py tests/... --select=F821,F822,F823,E9` → clean.
(The repo's broader default-ruff output is noisy at HEAD too — 20 fixable at baseline vs 24
with the fix, all style-tier rules the repo's gate does not select.)

**End-to-end, against the real file:** replaying the exact 2026-09-04 `lgbm_ranker` values
onto a copy of the committed `ml/registry.yaml` now produces this complete diff —

```
-    trained_date: '2026-08-24'
-    cpcv_dsr: 0.02407823552192498
+    trained_date: '2026-09-04'
+    cpcv_dsr: 1.7294176083023635e-16
-    n_train: 450
+    n_train: 460
```

— three changed scalar lines, zero comment loss, zero reformatting noise.

## The operator's working tree

Not reverted. Inspecting it turned up **more real training results than the report described**:
besides `lgbm_ranker` (2026-09-04, plus new `artifact_file`, `hyperparameters`, `train_window`,
`cpcv_mean_oos_*`), there are genuine retrains of `meta_labeler_timeseries_momentum`
(2026-09-06) and `meta_labeler_cross_sectional_momentum` (2026-09-07). All left intact.

Only the 4 comment lines were restored, by targeted text replacement asserted to match
exactly once:

- `/Users/kevinlee/Stockpy-live/ml/registry.yaml` — 33 → 37 comment lines; the header region
  of `git diff` is now empty.
- `~/.stockpy_local/ml_models/registry.yaml` (the machine-global runtime copy, which had the
  same erosion) — 33 → 37; **parsed data proven unchanged** by comparing `yaml.safe_load`
  before/after.

Backups of both pre-edit files are in this session's scratchpad.

## Known limits, stated plainly

- A comment sitting **inside** a model block whose values are being updated is still lost —
  that block is the one thing that gets re-serialized. Trailing comments/blank lines after
  the block are carried across. This is the minimal blast radius and it is documented in the
  code; `ml/registry.yaml` has no such comments today.
- `_BLOCK_DUMP_WIDTH = 80` reproduces 8 of the 10 current model blocks byte-for-byte. The
  other 2 were wrapped at a different historical width, so updating one of *those* models
  will re-flow its `notes` prose once. Cosmetic; never a data change.
- **Not touched:** `investyo_mcp_server.py`'s `watch_rules.yaml` writer has the same
  `safe_load → safe_dump` shape (different file, no doc header today).
- **Not touched:** `options_meta_labeler.notes` in `ml/registry.yaml` is truncated
  mid-sentence (`"...rather than a fabricated 0.0/1.0 (CONSTRAINT"`). This is present in the
  committed file and is **not** caused by this bug class. Flagged for the operator rather
  than guessing an ending.
