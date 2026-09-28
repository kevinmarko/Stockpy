# Forecasting rebuild F1: walkthrough

Plan: `.claude/forecasting_rebuild_implementation_plan.md` (phase F1, "measure
what we publish"). Branch `forecast-f1-measure`.

F1 is measurement only. No published `Forecast_*` value, live skill weight or
signal changes because of this PR. The one indirect effect is disclosed under
"What does change" below.

## What was built

1. **The published blend is recorded.** `forecasting_engine.py::generate_forecast`
   adds the blended value as model `blend` to the recordable copy (next to
   `naive`), only when at least one component model produced output. It is
   never added to `model_forecasts`, and the `Is_Fallback` current-price case
   is never recorded as `blend`.

2. **One row per symbol × model × horizon × US/Eastern trading day.**
   - New nullable `forecast_day TEXT` column, added by the existing
     `PRAGMA table_info`-guarded migration. Pre-F1 rows keep `NULL`.
   - New partial index `idx_fe_symbol_model_horizon_day` on
     `(symbol, model_name, horizon_days, forecast_day) WHERE forecast_day IS NOT NULL`.
     Partial so the first build on the 2.4M-row ledger is cheap (0.10 s on a
     copy of the real DB).
   - `record_forecasts` upsert, per model, inside `self._lock`: UPDATE the row
     with the same key AND `actual_price IS NULL`, overwriting
     `forecast_price`, `forecast_lower`, `forecast_upper`, `forecast_ts`,
     `recorded_at`. INSERT (with `forecast_day`) only if the UPDATE touched no
     row. Matured rows and NULL-day legacy rows never match, so they are never
     rewritten. An unparseable `forecast_ts` gets `forecast_day = NULL` and is
     always inserted (never given a made-up key).
   - `update_actuals`, `get_skill_weights`, `coverage_report`,
     `interval_score_stats`, `get_covered_symbols`, `pending_count` and
     `completed_count` work per row and need no change; they now simply see
     one row per day. Covered by tests.

3. **Blend inputs cannot leak.** `_blend_with_skill` only blends names present
   in `model_forecasts`, so `naive`/`blend` are never blend inputs. But the
   maturity logic in `compute_skill_weights_from_stats` could leak: a mature
   `naive` with no mature real model returned `{naive: 1.0}`, which the blend
   intersected to nothing and fell back to the static sector blend instead of
   the equal-weight cold start. Fix:
   - `blend` and the BERT-LLA ablations are dropped from the arithmetic.
   - No `NON_BLEND_MODEL_NAMES` entry can end the cold start on its own.
   - `naive` otherwise stays in the arithmetic, exactly as before F1.
     Removing it would move published values by a floating-point ulp (it is a
     common scale factor the blend renormalizes away); a first draft that
     removed it was caught by the byte-identity test and changed.

4. **Skill vs naive.** `ForecastTracker.skill_vs_naive(horizon_days,
   window_days, min_price=1.0)` over the pure `compute_skill_vs_naive`. See
   the class docstrings for the exact definitions.

5. **CLI** `scripts/forecast_skill_report.py` (`--horizon`, `--window-days`,
   `--min-price`, `--db`, `--json`), read-only.

6. **Ledger cleanup** `scripts/clean_forecast_ledger.py`, dry run by default.
   - `--categories` is a comma list from `a,b,c`, **default `a,b`**.
     Category (c), the intra-day dedup, runs only when explicitly listed and
     must wait for the F3 naive gate (follow-up commit, coordinator request).
   - Every count, the deleted/remaining totals and the cold-start estimate
     reflect only the selected categories.
   - When (c) is selected, the dry run and `--apply` both print a warning at
     the top and bottom built from the measured numbers.
   - The estimate also reports which (symbol, horizon) pairs' live skill
     WEIGHTS change, recomputed with the live formula, and warns when any do.
     This found that (b) alone is not weight-neutral (see below).
   - `--apply` backs up via sqlite3 `.backup` to `<LOCAL_DATA_ROOT>/backups/`,
     verifies the backup's row count, then deletes only the selected
     categories in one transaction. The `forecast_day` backfill on survivors
     runs only with (c): without the dedup a legacy day still holds many
     pending duplicates, and giving them a day key would make the next upsert
     overwrite all of them at once.
   - **Not run on the real DB by the agent.**

7. **UI window.** `pilots/forecast_skill.py` passes
   `FORECAST_SKILL_WINDOW_DAYS`/`FORECAST_SKILL_MIN_OBS` to
   `get_skill_weights` (was the method default, 60 days / 30).

## How blend byte-identity was proven

- `tests/test_forecasting_engine.py::TestF1BlendRecordingNeverLeaksIntoBlend`
  runs `generate_forecast` end-to-end against two real `ForecastTracker` DBs
  with skill weighting ON (window 365, min_obs 30, the operator's settings)
  and a fixed Monte Carlo seed, and asserts `==` (no tolerance) on every
  `Forecast_{h}`:
  - warm regime: pre-F1 ledger (real models + naive) vs the same plus very
    accurate, very mature `blend` rows;
  - leak regime: real models cold, naive/blend mature vs the ordinary cold
    start.
  It also spies `_blend_with_skill` to show `naive`/`blend` never appear in
  `model_forecasts` and `blend` never in the weights, and checks the recorded
  `blend` row equals `Forecast_{h}` exactly.
- `tests/test_forecast_rebuild_f1.py::TestSkillWeightExclusion` compares the
  new `compute_skill_weights_from_stats` with a verbatim copy of the pre-F1
  function on 5,000 random ledger states (`==` on the dicts).
- Mutation checks: disabling either guard (the cold-start guard, or dropping
  `blend` from the arithmetic) makes these tests fail; so do disabling the
  upsert UPDATE and removing its `actual_price IS NULL` guard.
- **Live ledger:** for all 1,278 (symbol, horizon) pairs with completed rows in
  the real DB (read-only, window 365, min_obs 30), the new and pre-F1 weight
  functions return identical dicts. Zero pairs are in the leak regime today.

## What does change (disclosed)

- The live install runs `FORECAST_SKILL_WEIGHTING_ENABLED=true`. From now on
  each (symbol, model, horizon) gains one scored row per trading day instead
  of ~20. Existing symbols already have large mature histories, so nothing
  moves immediately, but a newly tracked symbol stays in the equal-weight cold
  start for about 30 trading days after its first horizon matures instead of
  about 2. That is the plan's intent ("removes the fake maturity") but it is a
  change in how fast weights warm up.
- Running the cleanup with category (c) would drop 1,194 of the 1,223
  currently-warm (symbol, horizon) pairs back to the equal-weight cold start.
  (c) is therefore opt-in and waits for F3.
- Running the default a,b cleanup sends no real symbol to the cold start, but
  it does move live weights: removing the 2026-08-14 Monte Carlo seed rows
  (squared errors of ~(100 - 20)^2 each) raises Monte Carlo's skill weight
  for 54 (symbol, horizon) pairs, by up to 0.337 (IVR@10, ABR@10). With skill
  weighting on, those symbols' published `Forecast_10`/`Forecast_30` move.
  That corrects a corrupted input, but it is a decision change the operator
  should know about before running `--apply`.

## Real-DB outputs (read-only)

Skill report, window 365 d, min price $1:

```
== horizon 10 trading days ==
  model               n  med|lnE|    naive   %beat  dir hit    sign p
  holt_winters      189    0.0482   0.0510   41.8%    44.6%     0.029
  arima             189    0.0498   0.0510   44.4%    46.8%      0.15
  monte_carlo       191    0.0522   0.0514   40.8%    43.6%     0.014
  cnn_lstm           27    0.3213   0.0436   18.5%    44.4%    0.0015
== horizon 30 ==  no completed naive rows to pair with
== horizon 60 / 90 ==  no completed forecasts in window
```

`naive` has only been recorded since 2026-09-06, so only 10-day pairs exist
yet. `blend` has no matured rows yet (it starts with this PR).

Cleanup dry run, **default categories a,b**, on a fresh read-only copy of
the live ledger (sqlite3 online backup from a `mode=ro` source), window 365,
min_obs 30:

```
categories: a,b
total rows:                           2,426,104
(a) symbol='TEST' rows:                   3,972
(b) 2026-08-14 MC seed rows:                480  (30 symbols)
(c) intra-day duplicates:            not selected (opt-in with --categories a,b,c; after F3)
rows deleted in total:                    4,452
rows after cleanup:                   2,421,652
keys (symbol, model, horizon) mature now: 4,596; falling below min_obs: 7 (all TEST)
(symbol, horizon) pairs warm now: 1,223; dropping to cold start: 2 (TEST@10, TEST@30)
pairs of remaining symbols whose weights change: 54 (max shift 0.337)
```

So zero real (symbol, horizon) pairs return to the cold start under a,b.
`--apply` with a,b was also run on that COPY: 2,421,652 rows remained,
matching the dry run, and a before/after comparison of the live weight
formula showed the same 54 changed pairs.

Dry run with `--categories a,b,c` (earlier run on the live ledger, same
numbers on the copy apart from rows written since):

```
total rows:                           2,425,936
(a) symbol='TEST' rows:                   3,972
(b) 2026-08-14 MC seed rows:                480  (30 symbols, 16 rows each)
(c) intra-day duplicate rows:         2,278,379
rows deleted in total:                2,282,831
rows after cleanup:                     143,105
keys (symbol, model, horizon) mature now:  4,596; falling below min_obs: 4,504
(symbol, horizon) pairs warm now: 1,223; dropping to cold start: 1,194
```

(b) rule: `monte_carlo` rows (any horizon) with US/Eastern forecast day
2026-08-14 whose price is > 3x or < 1/3 of the cycle anchor, the median of the
same (symbol, forecast_ts) cycle's 10-day `arima`/`holt_winters`/`naive`
prices. Two simpler rules were tried and rejected on the real data: a
per-horizon median of the other models flagged a healthy sub-$1 UWMC 90-day
row, and "out of band against every other model" missed real CMCL seed rows
(its CNN-LSTM sat at $33-50). The 480 rows are exactly 4 cycles × 4 horizons
for 30 symbols, 13:55-14:35 UTC. The plan said 29 symbols; the ledger shows 30.

## Not done in F1

- Exposing the skill-vs-naive report on `/symbols/{t}/forecast` and the
  observability skill sections (the plan lists it under F1; this task scoped
  F1 to the CLI). The math is in `ForecastTracker.skill_vs_naive`, ready to
  wire.
- MC/quantile coverage per model in the report: only `monte_carlo` publishes
  bounds and `coverage_report` already measures it; not duplicated here.
- Root cause of the 2026-08-14 seed bug (F2).
