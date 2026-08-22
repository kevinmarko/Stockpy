# Task tracker: fix-options-meta-labeler-gate-safety

Source: audit of `ml/options_meta_labeler.py` + `execution/options_paper_executor.py`
+ `api/pilots_api.py`'s retrain endpoint — six compounding gate-safety bugs in the
Stage 4 Options ML Meta-Labeler, all more permissive than intended on unclean input.

- [x] Item 1 — wire real `vrp`/`vix`/`short_delta`/`credit_to_width_ratio` into
      `execution/options_paper_executor.py::get_actionable_directives`'s `item` dict
      instead of letting them silently default inside the model.
- [x] Item 2 — drop `trend_bias` from `OptionsMetaLabeler.FEATURE_NAMES`/
      `_extract_feature_vector` entirely (train/serve semantic mismatch; disclosed
      decision not to attempt a matching train-time signal).
- [x] Item 3 — `_resolve_numeric_feature`/`_finite_or_nan` propagate NaN for an
      explicitly-unresolved (present-but-None/NaN) value instead of silently
      defaulting; `predict_probability` gates on feature finiteness before ever
      calling the model, returning the neutral 0.65/1.0x fallback.
- [x] Item 4 — clip the degenerate `"baseline"` branch in `predict_probability` to
      `[0.05, 0.95]`, matching the other two model branches.
- [x] Item 5 — `execute_strategy_directives`'s ML-gating exception handler fails
      closed (skips the trade, logs at WARNING) instead of silently proceeding at
      full un-derated size at DEBUG-only logging.
- [x] Item 6 — minimal fix: `train()` and both `api/pilots_api.py` meta-model
      endpoints echo `metrics_are_in_sample: True`; webapp relabels the two metric
      tiles and the retrain toast with "(in-sample)".
- [x] Tests: `tests/test_options_meta_labeler.py` (6 tests, incl. 2 new NaN/None
      finiteness tests, 1 new degenerate-clip test) and
      `tests/test_options_paper_executor.py` (15 tests, incl. 4 new tests covering
      real feature wiring, no-short-leg honesty, vrp/vix explicit-None, fail-closed
      exception handling, and a full end-to-end proof that the real derived features
      — not hardcoded defaults — drive the model's decision).
- [x] Docs: `docs/architecture/ml-and-reports.md` updated;
      `docs/known_issues/options_meta_labeler_serving_time_gaps.md` written;
      `docs/known_issues/README.md` index entry added.
- [x] Webapp: `types.ts`/`mock.ts` gained the four new optional candidate fields for
      mock/live parity; `PaperBroker.tsx` relabeled for in-sample disclosure.
- [x] Regenerated `docs/settings_field_census.json`/`.md` and
      `docs/settings_liveness.json` (both went stale from `api/pilots_api.py`'s
      docstring additions shifting line numbers — the same "expected to fire on an
      otherwise-unrelated PR" case both artifacts' own tests document).
- [x] Verification: `pytest tests/test_options_meta_labeler.py
      tests/test_options_paper_executor.py tests/test_pilots_paper_broker.py
      tests/test_pilots_api.py tests/test_options_harness.py
      tests/test_options_queue_builder.py` → 668 passed. Full offline suite
      (`pytest -m "not network and not slow"`) → 12002 passed, 31 skipped, 5 failed
      (all pre-existing, unrelated to this change — missing `openai`/`google-genai`
      packages in this sandbox, confirmed failing identically on a clean checkout
      via `git stash`). `ruff check . --select=F821,F822,F823,E9` → clean.
      `npm run --prefix webapp typecheck` → clean. `vitest run
      src/screens/PaperBroker.test.tsx` → 22 passed.
- [x] Open PR against `main`: https://github.com/kevinmarko/Stockpy/pull/873
