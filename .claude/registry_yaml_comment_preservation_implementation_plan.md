# Implementation Plan — `ml/registry.yaml` comment preservation

## Problem

`ml/registry.yaml` carries a 37-line schema-documentation header comment block. It is the
only place that explains every registry field — including *why* `cpcv_mean_oos_max_dd` is
`null` for `meta_labeler_*` roles.

PyYAML does not preserve comments. `ml/registry_io.py::_dump_registry` did
`yaml.safe_load → mutate → yaml.safe_dump`, then re-emitted a **hardcoded copy** of the
header (`_REGISTRY_HEADER`) that had drifted out of sync with the real file.

Result: every training run silently replaced the real header with the stale constant.

### This is a recurrence, not a first occurrence

CLAUDE.md's "Forecast Backfill Meta-Labeler Bridge" bullet already records that
"the file's entire ~35-line schema-documentation header comment block had been silently
dropped (a `yaml.dump`/`yaml.safe_dump` re-serialization artifact, not a deliberate edit)".

Measured recurrence: a real `lgbm_ranker` training run (2026-09-04) took the file from
**37 comment lines → 33**, deleting exactly the 4-line `meta_labeler_*` explanation.

## Root cause (confirmed by reading the code, not inferred)

`ml/registry_io.py::_dump_registry` — the single write choke point for every registry
caller (`scripts/train_lgbm.py`, `scripts/train_meta_labelers.py`,
`ml/forecast_backfill_registry_bridge.py`, all via `update_model_metrics`, plus
`load_registry`'s own local-copy self-sync):

```python
body = yaml.safe_dump(data, ...)
f.write(_REGISTRY_HEADER)   # <- hardcoded, stale, drifts on every doc edit
f.write(body)
```

The module docstring claimed the header was re-emitted "verbatim". It was not — it was a
duplicate maintained by hand, and duplication is exactly why it drifted.

## Approach

`ruamel.yaml` (round-trip mode) is **not** a dependency of this repo and is not installed —
verified, not assumed. Per the operator's instruction, no new runtime dependency was added.

So: **surgical in-place splice**, which is also the stronger property.

`_dump_registry(data, path)`:

1. **File exists →** `_splice_registry_text(existing_text, data)`:
   - re-serialize **only** the `models.<key>` blocks whose parsed values actually changed;
   - copy every other byte through verbatim — header, blank lines, key order, untouched
     models, hand-added inline comments;
   - append genuinely-new models at the end of the `models:` section;
   - **bail out returning `None`** (never a partially-applied write) on any shape the
     splicer does not fully recognise: a change outside `models`, a removed model, a
     duplicate/unparsed key, a flow-style mapping;
   - **verify before trusting**: re-parse the spliced text and compare to `data`; a splice
     that would change meaning is rejected, not written (CONSTRAINT #6 — fail closed).
2. **Splice declined, or file is brand new →** full PyYAML dump, re-emitting the **real
   on-disk header** read back from the file. The hardcoded `_REGISTRY_HEADER` constant is
   used **only** to bootstrap a file that does not exist yet.
3. `_REGISTRY_HEADER` re-synced with the current file, and pinned against it by a test so
   it can never silently drift again.

`_BLOCK_DUMP_WIDTH = 80` is chosen empirically so a re-serialized block matches the file's
existing wrap convention (8 of 10 current model blocks reproduce byte-for-byte); a mismatch
is cosmetic prose re-flow inside the block being updated anyway, never a data change.

## Documentation-update step (required by CLAUDE.md)

- `ml/registry_io.py` module docstring: the false "NEVER hand-splice YAML text … re-emitting
  the leading banner comment block verbatim" design rule is replaced with what the writer
  now actually does, and why.
- `_REGISTRY_HEADER`'s own comment states it is bootstrap-only and test-pinned.
- This plan + task tracker + walkthrough under `.claude/registry_yaml_comment_preservation_*`.
- No `docs/architecture/*.md` change needed: `ml/registry.yaml`'s write path is not described
  there, and no field semantics changed.

## Out of scope (disclosed, not silently skipped)

- `investyo_mcp_server.py`'s `watch_rules.yaml` writer has the same `safe_load → safe_dump`
  shape. Different file, no documentation header today; not touched here.
- `options_meta_labeler.notes` in `ml/registry.yaml` is truncated mid-sentence
  (`"... rather than a fabricated 0.0/1.0 (CONSTRAINT"`). Pre-existing in the committed
  file — **not** caused by this bug class; left for the operator, since inventing an ending
  would be fabrication.
