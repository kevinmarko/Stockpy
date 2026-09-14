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

---

# Follow-up — the two items the first commit flagged as out of scope

## 1. `options_meta_labeler.notes` was truncated — a THIRD, distinct bug class

Not the comment-erosion bug, and **the new writer would not have prevented it**: the loss
happens on *read*, before `_dump_registry` ever sees the value.

The note was hand-written as an **unquoted (plain) YAML scalar** containing `(CONSTRAINT #4)`.
In YAML, ` #` inside a plain scalar starts a comment — so PyYAML's *reader* silently discarded
everything from ` #4);` onward at parse time, and the next `safe_dump` wrote the
already-truncated value back, making it permanent.

Reproduced directly:

```
PARSED BACK: ' -- cpcv_dsr/pbo are null rather than a fabricated 0.0/1.0 (CONSTRAINT'
TRUNCATED AT ' #': True
```

Traced through history: intact at `339d2b0b` / `ff718ea3` (2026-08-28), truncated at
`e7b2ac74` (2026-09-06) — **the same 6-agent audit pass CLAUDE.md already records for the
header loss on this file.**

**The ending was recovered, not invented.** Verbatim from `ff718ea3`:

> `... (CONSTRAINT #4); this is "not evaluated," not "evaluated and failed."`

Restored by re-serializing that one note through PyYAML, which quotes a string containing
` #` so it round-trips (verified). Writing it back unquoted by hand would have re-armed the
same bomb.

### The guard

`_unquoted_hash_offenders` flags a ` #` sitting inside an unquoted scalar — both
`key: <unquoted value>` and plain-scalar continuation lines. It tracks quote state across
lines, so a continuation of an *already-quoted* scalar is correctly skipped (that was a real
false positive on the first draft, caught and fixed).

Proven against the real history rather than only synthetic input:

| version | flagged |
|---|---|
| `ff718ea3` — intact but vulnerable | **1** (exactly the offending line) |
| `e7b2ac74` — after the damage | 0 (data already gone — an honest limit, not a pass) |
| working tree — fixed | 0 |

So this would have failed CI on the commit that introduced the hazard, before the next
retrain made it permanent. That is the realistic window; a writer cannot recover data the
reader already dropped.

## 2. `watch_rules.yaml` — I was wrong about this one

My earlier note said it has "no doc header today." That was incorrect. It is 90 lines,
**76 of them documentation** — the rule schema, edge-trigger semantics, ntfy setup steps,
worked examples. It is the only place any of that is written down.

Measured, before the fix: a single `update_watch_rules` call collapsed it **4246 → 247 bytes**.
That is a total documentation wipe on an operator-facing config file, and it was live, not
latent.

Comments there are **interleaved among the rules** (lines 51–53, 60–62, 68–90), not confined
to a header — so header-only preservation would still have lost ~28 lines and is explicitly
not what the tests accept.

### The fix

New `yaml_comment_io.py` — one home for comment-safe YAML writes, so a third copy of this
logic never gets written:

- `leading_comment_block` — read a file's own header back off disk (never a hardcoded copy).
  `ml/registry_io.py` now imports this instead of keeping its own duplicate.
- `splice_sequence_section` — the sequence-shaped sibling of the registry's mapping splicer.
  Re-serializes only genuinely-new list items; kept items pass through verbatim with their
  attached comments. Same two rules: never write a partial result, and re-parse to verify
  before returning.

`investyo_mcp_server.py::update_watch_rules`'s two `yaml.safe_dump` call sites now both go
through `_write_watch_rules`.

**The verify-before-write step earned its keep during development**: emptying the rules list
produced a bare `rules:`, which parses back as `None`, not `[]` — the splicer correctly
*refused* rather than writing a file that meant something different. Fixed by emitting
`rules: []` explicitly for that case.

## Verification

| suite | result |
|---|---|
| `test_registry_yaml_comment_preservation.py` | **14 passed** (was 10) |
| `test_watch_rules_comment_preservation.py` (new) | **9 passed** |
| `test_registry_load.py` + `test_train_lgbm.py` + `test_train_meta_labelers.py` | passed |
| genuine-bug lint (`F821,F822,F823,E9`) | clean |

The watch_rules tests were written first and confirmed failing against the old writer, with
the collapse measured in the assertion output (`assert 247 > (4246 * 0.9)`).

**`tests/test_investyo_mcp_server.py` has 21 failures — all pre-existing.** Verified by
running it against `HEAD`'s `investyo_mcp_server.py` and against the changed one and
diffing the failing test names: **identical**, 21 failed / 294 passed both ways. They are in
`TestRunBacktest` and unrelated to watch_rules.

## Also fixed in passing

`watch_rules.yaml` referenced `NTFY_TOPIC` in 3 places. That variable was renamed to
`ALERT_NTFY_TOPIC` (confirmed: `settings.py:1862`; CLAUDE.md records the rename and notes the
old name was "a silent no-op due to a mismatched env var read"). Updated — an operator
following those setup steps verbatim would have configured a dead variable.
