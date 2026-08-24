# Fix transformer-forecaster macro publication-lag lookahead + dead macro-series request

## Context

An audit of `ml/transformer_vol_forecaster.py` found two bugs that currently cancel
each other out:

- **Bug A**: `_align_macro_causal()` reindexes/ffills a macro series onto the bars
  index using its *nominal* FRED observation date, with no publication-lag
  handling. For a monthly/lagged series (e.g. `UNRATE`-style aggregates), this
  leaks a value up to ~1 month before it was actually published — a real
  lookahead bug for exactly the kind of series that doesn't already get daily
  same-day treatment.
- **Bug B**: the two series that would trigger Bug A in production —
  `BAMLC0A0CM` (IG credit OAS) and `FEDFUNDS` (monthly-average fed funds rate)
  — are requested by `api/pilots_api.py:6893`'s `get_transformer_forecast`
  endpoint but never actually fetched anywhere: `data_engine.py::fetch_macro_history()`
  only returns `VIXCLS, T10Y2Y, BAMLH0A0HYM2, BAA10Y, UNRATE, T10YIE`. So
  `HistoricalStore().get_macro("BAMLC0A0CM"/"FEDFUNDS")` always returns an empty
  Series, and the endpoint silently degrades to VIX/yield-curve-only
  conditioning — Bug A's dormant code path never actually runs today.

Fixing Bug B alone (the obviously-good-looking fix) without also fixing Bug A
would silently introduce a real ~1-month lookahead bias into a live
volatility-forecast endpoint. Both must land together. Neither existing
lookahead test (`test_causal_macro_perturbation_no_lookahead`,
`test_causal_end_to_end_prediction_perturbation`) catches this — both feed
synthetic macro data already at daily/bars-index frequency, never a genuinely
lower-frequency series through `_align_macro_causal`.

**Existing precedent found and reused**: `scripts/refresh_validations.py`'s
`_reconstruct_macro_regime_series` already has a "Lag UNRATE by 1 month"
convention: `unrate_lagged = unrate.sort_index().shift(1)`. This plan
generalizes that exact convention (rather than reinventing a per-series
hardcoded day-count) into `_align_macro_causal`, auto-detected from each
column's own native observation cadence.

## Fix design

1. `ml/transformer_vol_forecaster.py` — `_align_macro_causal` gains
   per-column publication-lag detection (`_detect_low_frequency_macro_column`,
   `_MACRO_DAILY_CADENCE_MAX_GAP_DAYS = 5.0`) and applies `.shift(1)` to
   low-frequency columns before alignment.
2. `ml/transformer_vol_forecaster.py` — `build_tft_model()` gains a
   `TFT_RANDOM_SEED = 42` reproducibility seed, mirroring
   `CNN_LSTM_RANDOM_SEED` in `cnn_lstm_worker.py`.
3. `data_engine.py` — `DataEngine.fetch_macro_history()` extended to fetch
   `BAMLC0A0CM` and `FEDFUNDS`, matching the 4 series
   `api/pilots_api.py`'s transformer-forecast endpoint already requests.
4. Tests added to `tests/test_transformer_vol_forecaster.py` and
   `tests/test_data_engine_macro_history.py`.
5. `docs/architecture/ml-and-reports.md`'s transformer-forecaster bullet
   updated with a dated addendum.

## Execution plan — 4 parallel/staged agents

1. Branch `fix-transformer-macro-publication-lag` from `origin/main`.
2. Agents 1-3 run concurrently (disjoint files): Agent 1 = ml fix + its
   tests; Agent 2 = data_engine fix + its tests; Agent 3 = docs update.
3. Agent 4 (this agent) runs after 1-3 finish: combined verification +
   PR artifacts.
4. Orchestrator reviews the diff, commits, pushes, opens the PR.

## Verification

- `pytest tests/test_transformer_vol_forecaster.py tests/test_data_engine_macro_history.py -q` must be 100% green.
- New test explicitly asserts a monthly-dated value is not exposed before its real publication date.
- Existing daily-cadence tests unaffected.
- `ruff check` on the four changed/touched files.

## Status: Implemented

All 4 stages completed successfully:

1. **Branch**: `fix-transformer-macro-publication-lag` created from `origin/main` (confirmed current branch at verification time).
2. **Agent 1** (`ml/transformer_vol_forecaster.py` + its tests): landed —
   `_detect_low_frequency_macro_column`, `_MACRO_DAILY_CADENCE_MAX_GAP_DAYS`,
   per-column lag applied inside `_align_macro_causal`, plus
   `TFT_RANDOM_SEED = 42` seeding in `build_tft_model()`. 3 new tests added.
3. **Agent 2** (`data_engine.py` + its tests): landed —
   `fetch_macro_history()` now fetches `BAMLC0A0CM` and `FEDFUNDS` on both
   the success path and both failure-path empty-DataFrame column-list
   literals. A new `TestFetchMacroHistoryIncludesBamlc0a0cmAndFedfunds` test
   class (3 tests) added; 2 existing tests in
   `TestFetchMacroHistoryIncludesT10YIE` updated for the new 8-column shape.
4. **Agent 3** (`docs/architecture/ml-and-reports.md`): landed — dated
   2026-08-24 addendum appended to the existing `ml/transformer_vol_forecaster.py`
   bullet, describing both bugs, both fixes, and the reused precedent.
5. **Agent 4 (this verification agent)**: ran the combined test command —

   ```
   python3 -m pytest tests/test_transformer_vol_forecaster.py tests/test_data_engine_macro_history.py -q
   ```

   Result: **25 passed, 0 failed** (19 in `test_transformer_vol_forecaster.py`,
   6 in `test_data_engine_macro_history.py`) in 1.07s. `ruff check` on the
   four changed/touched files reported 129 pre-existing style findings
   (UP006/BLE001/UP045/etc.) across the full files, none newly introduced by
   this diff — informational only per task instructions, no fixes attempted.
   No file outside the 5 already-modified files (`data_engine.py`,
   `docs/architecture/ml-and-reports.md`, `ml/transformer_vol_forecaster.py`,
   `tests/test_data_engine_macro_history.py`,
   `tests/test_transformer_vol_forecaster.py`) was touched; no commit was
   made; no new branch was created.
