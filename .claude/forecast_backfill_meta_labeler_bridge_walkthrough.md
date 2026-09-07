# Walkthrough: Forecast Backfill Meta-Labeler Bridge

**Status**: Implementation built by Antigravity (8 subagents, commit `fb0dc141`), then audited by 6 parallel
Claude Code agents per the approved implementation plan's execution model. The audit found the bridge's core
deliverable was completely non-functional as shipped (multiple real coding defects, all invisible to the
shipped test suite because every test touching the broken functions mocked them away), plus several real
test regressions and doc/webapp honesty gaps. All findings were triaged and fixed in this same session.

## What this feature does

Bridges the Forecast Backfill screen's own trained RandomForest meta-labelers
(`ml/forecast_backfill.py::AgenticForecastBackfiller`) into the same PBO/DSR-gated live registry
(`ml/registry.yaml` → `ml/meta_bootstrap.py::bootstrap_meta_registry()` → `signals/aggregator.py`'s meta-label
confidence gate) that `scripts/train_meta_labelers.py` already feeds for 2 AFML-trained signals — extending
live meta-label coverage to (an operator-opted-in subset of) 6 signals, through a new
`meta_labeler_backfill_<signal_id>` registry namespace and an AFML-priority tie-break.

Gated behind `settings.META_LABELING_BACKFILL_BRIDGE_ENABLED` (default `False`) plus an explicit per-signal
`settings.META_LABELING_BACKFILL_ELIGIBLE_SIGNALS` opt-in (default empty) — fully inert by default.

## Audit findings and fixes (this session)

### Blocking — the bridge could never actually register a model

1. **`register_backfill_model` unconditionally raised `ImportError`**: `from config import MODELS_DIR` — no
   such attribute exists in `config.py`. Fixed to resolve the models directory from
   `ml.meta_labeling._MODELS_DIR` (the same constant `MetaLabeler.save()`/`load_latest()` use — critical so the
   write target can never drift from what the loader globs).
2. **Even past that, the registry write always raised `TypeError`**: `update_model_metrics(...,
   registry_path=registry_path)` — the real kwarg is `path`. Also, `train_window` was passed as a plain
   `"start to end"` string; `update_model_metrics` does `dict(train_window)`, which raises on a string. Both
   fixed — `register_backfill_model` now calls `update_model_metrics` with the correct kwargs, and
   `ml/forecast_backfill.py`'s step 7 now builds `train_window` as a `{start, end, n_dates}` dict.
3. **`compute_backfill_cpcv_metrics` always raised `TypeError`**: it called `validation.metrics
   .run_cpcv_evaluation` with entirely nonexistent kwargs (`signal_sign`, `target`, `dates`, `horizon_days`,
   `theta_c`); the real signature is `(strategy_fn, X, y, t1, n_splits, n_test_splits, freq, cost_model_fn)`.
   The `strategy_fn` closure also didn't match the real 4-arg/list-of-trial-dicts contract. Rebuilt from
   scratch to genuinely reuse `run_cpcv_evaluation`, modeled on `scripts/train_meta_labelers.py
   ::compute_cpcv_metrics`'s validated pattern: a real `t1` purge window built from `dates + horizon_days`, and
   a multi-candidate `strategy_fn` (3 `RandomForestClassifier` configs) so DSR/PBO measure genuine selection
   bias (`n_trials > 1`) rather than trivially collapsing.
4. **Why none of this was caught**: every `step_7_register_live_meta_labelers` test in
   `tests/test_forecast_backfill.py` monkeypatched away exactly these two functions, and the dedicated
   `tests/test_backfill_bridge.py` file's one test body was literally `pass`. Fixed by deleting that dead stub
   and adding real, unmocked coverage in `tests/test_train_meta_labelers.py`
   (`TestComputeBackfillCpcvMetricsReal`, `TestRegisterBackfillModelReal`) that calls both functions directly
   with real data and asserts on genuine CPCV output and an actual registry-file write.

### Blocking — a second, independent filename collision, one level down

The original design correctly separated the AFML path's `meta_<signal_id>_*.pkl` glob from the bridge's
`backfill_meta_<signal_id>_*.pkl` glob (fixing the bug this PR was originally asked to fix). But step 5's own
always-on diagnostic pickle writes (unrelated to the bridge, one file per horizon on every backfill run) were
*also* named `backfill_meta_{model_type}_{h}d.pkl` — sharing the exact glob namespace `MetaLabeler.load_latest
(signal_id, prefix="backfill_meta")` uses for step 7's registered model. Under the shipped defaults
(horizons `[10, 30, 60, 90]`, live horizon `10`), the diagnostic 90-day file sorted lexicographically last,
so the loader picked the wrong (and wrong-typed, raw sklearn) file instead of the real registered `MetaLabeler`.
Fixed by moving step 5's diagnostic writes to a third, disjoint `backfill_diag_` prefix, reserving
`backfill_meta_` exclusively for step 7's registry-tracked artifacts.

### Real, previously-undetected gap — the feature whitelist itself was missing a feature

`LIVE_ROW_FEATURE_WHITELIST` (`ml/meta_bootstrap.py`) was missing `primary_score` — a feature both existing
AFML meta-labelers actually train on (see their `features:` lists in `ml/registry.yaml`), appended by
`signals/aggregator.py` *after* `strategy_engine.py`'s own `row` is built, immediately before the meta-labeler
is queried. Four of the six audit agents independently verified the whitelist as an "exact match" against
`strategy_engine.py`'s literal `row` construction alone, missing this downstream augmentation. Caught by a new
regression test (`test_live_row_feature_whitelist_matches_the_real_live_row`) that parses `strategy_engine.py`'s
real `row = pd.Series({...})` construction directly and diffs it against the whitelist, rather than trusting a
hand-verified claim. Fixed by adding `primary_score` to the whitelist.

### Real test regressions (confirmed via before/after comparison against the pre-PR commit)

- `tests/test_gui_env_io.py::test_every_settings_field_is_classified` — 3 of the 4 new settings fields were
  never added to `shared/env_io.py`'s `ALLOWED_KEYS`. Fixed.
- `tests/test_measure_settings_census.py`/`tests/test_settings_liveness.py`'s `TestCommittedArtifactIsFresh`
  tests — the committed settings census/liveness artifacts were never regenerated after adding the new
  settings fields. Fixed via `python3 scripts/measure_settings_census.py --write` and
  `python3 scripts/settings_liveness.py --write`.

### Doc/webapp honesty and mock/live parity gaps

- **Webapp progress-bar break on every real run**: the new `registry_bridge` worker phase
  (`ml/forecast_backfill_worker.py`) fires unconditionally (regardless of the bridge flag), but was never added
  to the webapp's `ForecastBackfillPhase` type union, `PHASE_LABEL` map, or mock simulation, or to
  `ml/forecast_backfill_job.py`'s Python `Literal` — so every real (live-backend) backfill run showed a broken
  `undefined` progress label at step 7 of 8. Fixed in all four places.
- **`ml/registry.yaml`'s entire ~35-line schema-documentation header comment block had been silently dropped**
  (a `yaml.dump`/`yaml.safe_dump` re-serialization artifact) and the 6 new stub rows used `path: ''`/
  `trained_date: ''` instead of honest `null`. Both restored/fixed.
- **Disclaimer text in `ForecastBackfillScreen.tsx`** was self-contradictory ("A model only reaches... but this
  screen's models can now reach...") and under-disclosed that the feature-compatibility gate currently blocks
  every one of the 6 eligible signals. Rewritten to state this plainly.
- **`docs/plans/FORECAST_BACKFILL_PLAN.md`** was the one doc that didn't disclose the current all-6-blocked
  reality (every other touched doc did). Fixed with an explicit paragraph.
- **`webapp/src/api/mock.ts`'s fixture never populated the new fields**, so mock-mode development (the default
  `npm run dev`) could never render the new "Live Registry"/"CPCV DSR"/"PBO" columns or the blocked/skipped
  state. Fixed with one registered-example row and test coverage for a blocked-example row.
- **No webapp test covered the new columns or disclaimer text at all.** Added 5 new tests to
  `ForecastBackfillScreen.test.tsx` covering: columns render given a registry-bearing row, columns stay absent
  by default (no regression to the pre-bridge common case), the "(Skipped)" badge + tooltip for a blocked
  attempt, honest `null`→`"--"` rendering (never a fabricated number), and the disclaimer's honest disclosure.

## Verification performed

- `ruff check --select=F821,F822,F823,E9` on every changed Python file: clean.
- Targeted: `pytest tests/test_meta_labeling.py tests/test_train_meta_labelers.py tests/test_forecast_backfill.py tests/test_registry_load.py tests/test_gui_env_io.py tests/test_measure_settings_census.py tests/test_settings_liveness.py -q` → 201 passed.
- Full offline suite: `pytest -m "not network and not slow" -q` → 12877 passed, 16 skipped, 10 failed — all 10
  failures independently confirmed pre-existing/environmental (sandbox socket-bind restrictions, FRED socket
  timeouts, Alpaca HTTP timeouts, MCP widget schema) and touching none of this change's files; zero new
  regressions.
- `npm run --prefix webapp typecheck`: clean.
- Full webapp suite: `npx vitest run` (from `webapp/`) → 175 test files, 1952 tests, all passed.

## Residual, explicitly disclosed limitation (not a bug — by design)

Even with every defect above fixed, the feature-compatibility gate (`LIVE_ROW_FEATURE_WHITELIST`/
`check_feature_compatibility`) correctly and honestly refuses all 6 eligible signals today, since none of
their declared `meta_label_features` are fully covered by the live per-ticker feature row `strategy_engine.py`
actually queries a meta-labeler with. This bridge is now genuinely wired, tested, and safe — but remains
functionally inert until a separate follow-up widens `strategy_engine.py`'s live row schema. See
`docs/plans/FORECAST_BACKFILL_PLAN.md` and each affected `docs/signals/<name>.md`'s "Backfill-Screen Live
Meta-Labeler Bridge" section for the full disclosure.
