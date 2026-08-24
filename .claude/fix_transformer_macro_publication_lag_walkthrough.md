# Walkthrough: transformer-forecaster macro publication-lag fix

## The two bugs, and why they had to be fixed together

**Bug A — dormant lookahead bug in `_align_macro_causal`**
(`ml/transformer_vol_forecaster.py`, previously ~line 464-486): the function
reindexed/forward-filled every macro column onto the bars index using a
single shared `full_dt = bars_dt.union(macro_sorted.index)` step, with no
notion of *when* an observation was actually published versus its *nominal*
FRED-assigned date. For a daily/business-day series (VIX, yield-curve slope,
credit-spread OAS, breakeven inflation) that's correct — those publish
same-day or next-business-day. For a periodic-aggregate series like
`FEDFUNDS` (a monthly average, dated the 1st of the month it summarizes but
not actually published until early the following month), treating the
nominal date as the availability date leaks that month's value up to ~1
month before it existed.

**Bug B — dead macro-series request** (`data_engine.py::DataEngine.fetch_macro_history()`,
previously ~line 245-311): `api/pilots_api.py`'s `get_transformer_forecast`
endpoint (around line 6893) requests 4 macro series via
`HistoricalStore().get_macro(...)`: `VIXCLS`, `T10Y2Y`, `BAMLC0A0CM`,
`FEDFUNDS`. But `fetch_macro_history()` — the method `HistoricalStore.get_macro()`
tops up from — only ever fetched `VIXCLS, T10Y2Y, BAMLH0A0HYM2, BAA10Y,
UNRATE, T10YIE`. `BAMLC0A0CM` and `FEDFUNDS` were never in that list, so
`get_macro()` always returned an empty `Series` for both, and the live
endpoint silently degraded to VIX/yield-curve-only conditioning — 2 of its 4
intended series.

**Why they had to land together**: Bug B is what was actually hiding Bug A
in production. Fixing only Bug B (adding the two missing series) — the
obviously-good-looking, isolated-seeming change — would have been the thing
that *activated* Bug A live: `FEDFUNDS` is exactly the monthly-cadence series
Bug A mishandles, and once it started flowing through `_align_macro_causal`
unfixed, the transformer forecaster would have started conditioning on
up-to-1-month-early Fed funds data. Neither existing lookahead test caught
this gap — `test_causal_macro_perturbation_no_lookahead` and
`test_causal_end_to_end_prediction_perturbation` both feed synthetic macro
data already at daily/bars-index frequency, so neither ever exercised a
genuinely lower-frequency series through `_align_macro_causal`.

## The precedent reused

`scripts/refresh_validations.py::_reconstruct_macro_regime_series` already
had a "Lag UNRATE by 1 month" convention:

```python
unrate_lagged = unrate.sort_index().shift(1)
```

This fix generalizes that exact idea — a plain positional `.shift(1)` on a
series with one row per native period, which moves each period's real value
to the *next* period's date-slot — into `_align_macro_causal`, but detected
automatically by cadence rather than hardcoded per series-id, so any future
lower-frequency series added to a caller's macro request list is lagged
correctly by default instead of silently leaking.

## The fix, file by file

### `ml/transformer_vol_forecaster.py`

1. New module-level constant `TFT_RANDOM_SEED = 42`, and `build_tft_model()`
   now calls `np.random.seed(TFT_RANDOM_SEED)` before initializing weights.
   This model is an "ELM"-style design: attention/gating weights (`W_q`,
   `W_k`, `W_v`, `W_o`, `W_gate1`, `W_gate2`, per-quantile output weights)
   are randomly initialized once and then frozen forever — only the output
   layer is fit. Without a fixed seed, two back-to-back `build_tft_model()`
   calls against identical input produced numerically different forecasts
   for no principled reason. Mirrors `cnn_lstm_worker.py`'s
   `CNN_LSTM_RANDOM_SEED` convention exactly.

2. New `_MACRO_DAILY_CADENCE_MAX_GAP_DAYS = 5.0` threshold constant and new
   helper `_detect_low_frequency_macro_column(raw_series) -> bool`: drops
   NaNs from the column's own native series, computes the median day-gap
   between consecutive real observations, and returns `True` when that
   median exceeds 5 days (tolerates weekend/holiday gaps in a genuinely
   business-daily series while correctly flagging monthly/quarterly
   cadences). Cadence is detected per-column from the data itself, not a
   hardcoded series-id allowlist.

3. `_align_macro_causal(bars_index, macro_df)` rewritten from a single
   shared `full_dt = bars_dt.union(macro_sorted.index)` reindex/ffill step
   into a per-column loop:
   - For each column, take its own `dropna()`'d native series.
   - If `_detect_low_frequency_macro_column(...)` is `True`, apply
     `.sort_index().shift(1)` to that column's native series before anything
     else — the generalized "lag by one native period" step.
   - Union each (possibly-lagged) column's own index into `full_dt` (rather
     than the whole macro_df's shared index), build a per-column-populated
     `macro_reindexed` DataFrame on that union index, then `.ffill()` and
     `.reindex(bars_dt)` exactly as before.
   - Daily-cadence columns get zero additional shift and remain
     contemporaneously available, unchanged from prior behavior — matching
     `regime/hmm_regime.py::build_feature_matrix`'s existing documented
     same-day treatment of VIX/T10Y2Y.

### `data_engine.py`

`DataEngine.fetch_macro_history()`:
- Docstring extended to document `BAMLC0A0CM` (ICE BofA US Corporate Index
  OAS, investment-grade, daily cadence — same family as the existing
  `BAMLH0A0HYM2` high-yield series) and `FEDFUNDS` (Federal Funds Effective
  Rate, monthly average — FRED dates it the 1st of the month it summarizes
  but doesn't publish until early the following month), plus a new paragraph
  tracing the `HistoricalStore.get_macro()` → `api/pilots_api.py`'s
  `get_transformer_forecast` chain that this closes.
- Both empty-DataFrame column-list literals (the `if not self.fred:` early
  return and the `except Exception:` failure path) were factored into one
  shared `_EMPTY_COLUMNS` list and extended from 6 to 8 columns, so the
  failure-path schema always matches the success-path schema (CONSTRAINT #6
  — never a fabricated partial frame; the column list itself must still be
  honest even when empty).
- The success path now also calls `self.fred.get_series('BAMLC0A0CM')` and
  `self.fred.get_series('FEDFUNDS')`, and both are added to the `pd.concat`
  call that builds `history_df`.

### `docs/architecture/ml-and-reports.md`

The existing `ml/transformer_vol_forecaster.py` bullet's macro-conditioning
sentence was updated from "strictly causal FRED macro indicator conditioning
... with lookahead-free alignment" to "causal FRED macro indicator
conditioning ... with cadence-aware, publication-lag-adjusted alignment (see
2026-08-24 addendum below)", and a full dated addendum was appended
describing both bugs, both fixes, the reused precedent, and the new test
names (see the plan/task files for the exact appended text).

## Verification

```
python3 -m pytest tests/test_transformer_vol_forecaster.py tests/test_data_engine_macro_history.py -q
```

Expected and actual result: **25 passed, 0 failed** (19 tests in
`test_transformer_vol_forecaster.py`, 6 in
`test_data_engine_macro_history.py`), 1.07s wall time.

Key new tests:
- `test_align_macro_causal_lags_monthly_series_no_early_exposure` — builds a
  synthetic monthly (first-of-month-dated) macro series across a 120-day
  bars span, and asserts a given month's value never appears on the bars
  index before the *next* native monthly date (i.e. it's genuinely lagged by
  one period), while still eventually showing up via ffill from that next
  native date onward.
- `test_align_macro_causal_daily_series_still_contemporaneous` — guard test
  proving a business-daily column gets **zero** additional lag: the aligned
  output must equal the raw daily input series exactly, protecting against
  the cadence threshold or detection logic being loosened later and
  silently lagging genuinely daily series too.
- `test_build_tft_model_is_seeded_deterministic` — two separate
  `build_tft_model(...)` calls with identical arguments must produce
  bit-identical frozen weight matrices (`W_q`, `W_k`, `W_v`, `W_o`,
  `W_gate1`, `W_gate2`, and every per-quantile output weight/bias pair).
- `TestFetchMacroHistoryIncludesBamlc0a0cmAndFedfunds` (3 tests in
  `tests/test_data_engine_macro_history.py`) — success path returns real
  (mocked-but-realistic) `BAMLC0A0CM`/`FEDFUNDS` values that round-trip
  exactly from the fake FRED source; both failure paths (no FRED client,
  mid-fetch exception) degrade to the same honest 8-column empty
  `DataFrame`, never a fabricated partial frame.
- 2 existing tests in `TestFetchMacroHistoryIncludesT10YIE` were updated in
  place (not newly added) to expect the new 8-column shape instead of 6.

`ruff check ml/transformer_vol_forecaster.py data_engine.py
tests/test_transformer_vol_forecaster.py
tests/test_data_engine_macro_history.py` reports 129 findings, all
pre-existing style issues spread across the full files (72 `UP006`
non-PEP585 annotations, 12 `BLE001` blind-except, 12 `UP045` non-PEP604
optional annotations, and smaller counts of `UP035`/`PIE790`/`B023`/
`DTZ005`/`I001`/`RUF059`/`B006`/`UP007`/`F841`/`RUF012`/`RUF046`/`S110`) —
none of these are new findings introduced by this diff; informational only,
no fixes attempted.
