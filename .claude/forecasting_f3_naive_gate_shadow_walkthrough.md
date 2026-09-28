# Forecasting rebuild F3: naive gate, shadow first (walkthrough)

Plan: `.claude/forecasting_rebuild_implementation_plan.md` (phase F3). Branch
`forecast-f3-naive-gate-shadow`, on top of F2 (#1073, `c2968b7c`).

## The review command (run this in 2 weeks)

```bash
python scripts/forecast_skill_report.py --gate
# optional: --horizon 10 --horizon 30 --window-days 365 --json
```

It reads the live ledger read-only and prints, per horizon: `blend` vs
`gated_blend` vs `naive` on the same matured days, a gated-vs-blend
head-to-head, how often the gate fell back to naive, which models it
admitted, and the date the first pending shadow rows mature.

**Read this before the review.** On the live ledger today the gate admits
nothing anywhere (see "Real ledger, today" below). `naive` rows only exist
from 2026-09-06, and a model needs 60 matured daily pairs with naive for that
symbol and horizon. So:

* h=10: the first model can be admitted around mid-December 2026.
* h=30: around mid-January 2027.
* h=60/h=90: later still.

In 2 weeks the report will show `gated_blend` = naive on every day, and at
h>=30 no matured shadow rows at all (`n=0, not yet scorable`). Flipping
`FORECAST_NAIVE_GATE_ENABLED` then would publish naive (neutral in
`forecast_alignment`) for every symbol and horizon. See "Open decisions".

## What changed

### 1. The gate rule (pure): `forecasting/forecast_tracker.py::compute_naive_gate`

`compute_naive_gate(skill_stats, candidates, min_improvement, min_obs)`:

* `skill_stats` is `compute_skill_vs_naive`'s output (F1's primitive, reused
  unchanged) for one (symbol, horizon): each model's median
  `|ln(forecast / actual)|`, naive's median on exactly the same
  (symbol, US/Eastern day) pairs, and `n`.
* A model is admitted when it is blend-eligible (not in
  `NON_BLEND_MODEL_NAMES`), `n >= min_obs`, and
  `median_model <= median_naive * (1 - min_improvement)`.
* Weights are `1 / median_model` over admitted models that are in
  `candidates`, normalized. `candidates` are this cycle's guarded
  `model_forecasts` (after F2's clamp/input check), so an F2-dropped model can
  never be weighted. An admitted model that produced no forecast this cycle
  is left out, and the rest renormalize (graduated degrade).
* Nothing left means `fallback_to_naive=True` and empty weights. The caller
  publishes naive, never a blend of models that failed the gate.
* A median of exactly 0 is floored at `1e-6` before inverting.

Settings (non-secret, in `shared/env_io.py` `ALLOWED_KEYS`; all `live_safe`,
read per call):

* `FORECAST_NAIVE_GATE_ENABLED` (default `False`)
* `FORECAST_NAIVE_GATE_MIN_IMPROVEMENT` (default `0.005`)
* `FORECAST_NAIVE_GATE_MIN_OBS` (default `60`)

The window is the live skill blend's own `FORECAST_SKILL_WINDOW_DAYS`, so the
gate and the blend it would replace judge models over the same history.

`FORECAST_NAIVE_GATE_ENABLED` is not in `settings_keysets.SAFETY_CRITICAL_KEY_REASONS`.
The repo's convention puts forecasting/tunable flags behind the
`GENERAL_SETTINGS_WRITES_ENABLED` editor gate rather than the typed-confirmation
set, which is reserved for execution quarantines and trust boundaries
(`ADVISORY_ONLY`, `DRY_RUN`, `MACRO_REGIME_GATE_ENABLED`, ...);
`FORECAST_SKILL_WEIGHTING_ENABLED` is the direct precedent and is not in it
either. As a trading-behavior flag, it defaults to `False`.

### 2. Gate inputs with zero lookahead: `ForecastTracker.naive_gate_stats`

`naive_gate_stats(symbol, horizons, window_days, as_of, min_price=1.0)` reads
completed rows for ONE symbol and all horizons in one query, then:

1. Collapses them to the last row per (symbol, model, horizon, US/Eastern
   day). This is F1's dedup, extracted into `_latest_row_per_day` and now
   shared with `skill_vs_naive`.
2. **No-lookahead cutoff:** a row counts only if
   `forecast_day + BDay(h) < eastern_day(as_of)`. Its outcome must have
   matured on a trading day strictly before the forecast being gated. A row
   due today is excluded even if `update_actuals` already stamped it,
   because today's close is not final until the session ends. Rows forecast
   after `as_of` are excluded too.
3. Pairs each model row with naive on the same day (`_pairs_with_naive`, also
   extracted and shared with `skill_vs_naive`), and scores the pairs with
   `compute_skill_vs_naive` (sub-$1 names excluded, as in F1).

It returns `{"by_horizon": {h: stats}, "reason": ...}` and never raises;
`reason="error"` marks an untrusted read.

`generate_forecast` calls it once per symbol, right after `update_actuals`,
with `as_of = now_utc`, the same timestamp the forecast is recorded under.

### 3. Shadow recording: `gated_blend`

**Design choice: one model name plus two metadata columns**, not a second
model name for the fallback case.

`gated_blend` is recorded at every horizon with the F1 upsert key (one row
per symbol × model × horizon × US/Eastern day). Its row carries two new
nullable `forecast_errors` columns (additive, `PRAGMA table_info`-guarded
migration):

* `gate_fallback`: 1 when the gate fell back to naive, 0 for a real gated blend.
* `gate_admitted`: comma-joined names of the models that carried weight
  (`''` on a fallback).

Every other model's row has both columns NULL.

Why not a separate name such as `gated_blend_naive`? The gate's decision can
change between hourly cycles on the same day. With two names, a day could end
up with one row under each name, which breaks one-row-per-day, and every
query would need a union. With columns, the upsert overwrites the price and
the decision together, so each day's row always carries the decision that
produced its price. Scoring stays a plain `model_name = 'gated_blend'`, and
fallback frequency plus admitted-model counts are plain column reads.

More rules:

* **A fallback is still recorded.** Its price is naive's (`current_price`, the
  value the `naive` row records), so `gated_blend` is scored on every day,
  including the days it deferred to naive. That is exactly what the review
  must measure.
* **A failed stats read records no shadow row.** An untrusted decision is not
  scored.
* **Excluded from skill arithmetic.** `gated_blend` is in
  `NON_BLEND_MODEL_NAMES` and `_SKILL_ARITHMETIC_EXCLUDED`, so it can never be
  a blend input or end the cold start.
* **`blend` keeps recording the UNGATED blend in both flag states.** After the
  flip, `blend` vs `gated_blend` is still the comparison of the old number
  vs the new published one. The report stays meaningful, and flipping back is
  informed by continued data.

### 4. `FORECAST_NAIVE_GATE_ENABLED`

* **Off (default):** the published `Forecast_{h}` is the live blend and the
  results dict keeps exactly its pre-F3 key set. The shadow row, one extra
  read query per symbol and the per-cycle log line are the only additions.
* **On:** `Forecast_{h}` is the gated value. When the gate fell back to naive:
  * `Forecast_{h}_Is_Fallback=True`. The published value is today's price,
    i.e. "no model view". Unflagged, `forecast_alignment` would read
    `forecast == price` as bearish (−10); flagged, F2 scores it 0.
  * `Forecast_{h}_Gated_Naive=True` says why. This key only exists with the
    flag on. `ForecastingStep` adds the four `_Gated_Naive` dashboard
    columns only then.
* **No tracker attached** (ad-hoc or diagnostic callers: the Forecast Viewer's
  `/metrics/forecast`, Gravity, the validation adapter, scripts): the gate is
  not evaluated, so the flag changes nothing there. These callers have no
  ledger to judge models by, and F4 moves them onto the pipeline's persisted
  forecast. The production producers (daemon `ForecastingStep`, advisory
  path) always attach a tracker.
* **Tracker attached but the stats read failed:** no evidence, so nothing is
  admitted and naive is published (with the flag on). This counts toward
  `gate_stats_unavailable`.
* **The Monte Carlo `Forecast_{h}_Lower/Upper` band is unchanged** either way.
  It is Monte Carlo's own interval. F5 revisits bands.

`pop_guard_stats()` gains `gate_admitted_horizons`, `gate_naive_horizons` and
`gate_stats_unavailable`. `ForecastingStep` logs them as one separate line per
cycle, tagged `shadow` or `LIVE`. The F2 guard line now prints only when a
guard key is non-zero, so the gate counters don't force it every cycle.

### 5. Side-by-side report: `scripts/forecast_skill_report.py --gate`

This is backed by `ForecastTracker.gate_side_by_side(horizon, window_days,
min_price)`:

* `blend` and `gated_blend` are scored with `compute_skill_vs_naive` ONLY on
  (symbol, day) pairs where all three of blend, gated_blend and naive were
  recorded and have matured. Both blends are judged on identical days, and
  naive's median is shown on the same days. Columns are the same as F1's
  report: n, median |log error|, % beating naive, direction hit rate, sign-test p.
* **Head-to-head:** gated_blend vs blend on those days. This reuses
  `compute_skill_vs_naive` with blend as the baseline: % of days gated was
  better, wins/losses/ties, sign-test p.
* **Gate activity** over every gated_blend day in the window, matured or not:
  days, matured days, fallback days and %, admitted-model counts, and the
  date the earliest pending row matures.
* **It degrades honestly.** With no rows it prints
  `n=0, not yet scorable (...)`, plus the first maturity date when rows are
  pending. A pre-F3 ledger (no gate columns) prints "ledger predates F3".
  `--json` is supported.

## Verification

### New tests: `tests/test_forecast_rebuild_f3.py` (46 tests)

| Class | Covers |
|---|---|
| `TestComputeNaiveGate` | 1% better admitted; 0.3% not; the 0.5% edge is inclusive (`nextafter` just above is rejected); worse than naive rejected; n=59 rejected, n=60 admitted; nothing admitted → naive + flag and empty weights, with per-model reasons; inverse-error weights (0.05 vs 0.075 → 0.6/0.4); admitted-but-not-produced renormalizes; measurement-only names never admitted; zero median doesn't divide by zero. |
| `TestGateOnSyntheticLedger` | A real tracker on a synthetic ledger with naive error exactly 0.02: ARIMA at 0.0198 (1%) admitted, Holt-Winters at 0.01994 (0.3%) not, Monte Carlo worse not. n=59 matured days not enough. Pre-F1 intra-day duplicates count once. Sub-$1 excluded. Other symbols/horizons don't leak in. Window respected. A read error is reported, never raised. |
| `TestGateHasNoLookahead` | Two ledgers with the same 60 matured days plus 70 rows that had not matured by `as_of` (due that day or later, or forecast after it), with the future rows made great in one and awful in the other. Stats and decision are bit-identical. The non-vacuous check: judged 120 days later the awful rows flip the decision. A row due on the `as_of` day is excluded and counts the next day. The engine passes its own forecast timestamp as `as_of` and the live skill window. |
| `TestShadowRecording` | gated_blend at all 4 horizons, one row per horizon after two same-day cycles, equal to naive with `gate_fallback=1` on an empty ledger. With ARIMA admitted at h=10, gated_blend equals ARIMA's forecast with `gate_fallback=0` and `gate_admitted='arima'`, and differs from blend. The upsert overwrites the metadata. No shadow row on a failed stats read. No tracker, no gate. |
| `TestGatedBlendNeverEntersSkillArithmetic` | Pure weights unchanged by a near-perfect gated_blend; it cannot end the cold start; `get_skill_weights` on a ledger with gated_blend rows is unchanged; `_blend_with_skill` ignores it. |
| `TestFlagOffIsByteIdentical` | 3 seeds × skill weighting off/on, on a ledger where the gate DOES admit a model and changes the value: the results dict and every non-gated_blend ledger row (blend included) equal a baseline with the gate code removed (stats read forbidden, gated forecast returns `None`). Default settings are off / 0.005 / 60. |
| `TestFlagOn` | h=10 publishes ARIMA's value (the admitted model), `Gated_Naive=False`. h=30/60/90 publish today's price with `Is_Fallback=True` and `Gated_Naive=True`. blend rows still equal the flag-off published values. Every other result key is unchanged. `forecast_alignment` scores a flagged naive 0 vs −10 unflagged. No tracker: flag on changes nothing. An unreadable ledger publishes naive with the flags. |
| `TestGateReport` | Metrics on a seeded side-by-side (0.03 vs 0.01 vs naive 0.02; gated better on 100% of 20 days; 5/20 fallbacks; admitted counts). Pending-only rows give `n=0, not yet scorable` with the exact first-maturity date. A pre-F3 ledger degrades honestly. The CLI `--gate` flag works. |

**Mutation checks** (a scratch script: each applied, the suite run,
then restored). Every one was caught:

| Mutation | Result |
|---|---|
| gated_blend removed from `_SKILL_ARITHMETIC_EXCLUDED` | 3 failed |
| maturity cutoff removed | 2 failed |
| cutoff `<` → `<=` | 2 failed |
| flag-off publishes the gated value | 8 failed |
| no improvement margin | 3 failed |
| equal weights instead of inverse-error | 1 failed |
| `min_obs` off by one | 2 failed |
| gate fallback not flagged `Is_Fallback` | 2 failed |
| `blend` row records the published (gated) value when on | 1 failed |

### Byte-identity against the real pre-F3 engine

`origin/main:forecasting_engine.py` (`c2968b7c`) was loaded as a separate
module and run next to the F3 engine, flag OFF, with a real `ForecastTracker`
on a ledger seeded so the gate admits ARIMA at h=10 and would change the value.
The cases:

* 2 skill-weighting states × 8 seeds × 3 price/vol regimes × 4 sectors
* Prophet and TF off
* Monte Carlo seed 42

**Result: 192/192 identical**, comparing the full results dict and every
non-gated_blend ledger row. The gate admitted a model in 192/192 cases, so
the comparison is not vacuous.

### Suites

* Targeted: `test_forecast_rebuild_f3` (46), `test_forecasting_engine`,
  `test_forecast_tracker`, `test_forecast_rebuild_f1`, `test_forecast_rebuild_f2`:
  all pass.
* Full offline suite: `pytest -m "not network and not slow" -n auto --dist
  loadgroup -q -p no:randomly`, 11,552 passed, 18 skipped, 0 failed.
  It was run with `LOCAL_DATA_ROOT` pointed at a scratch directory, because
  of the two leaks below.
* `ruff check . --select=F821,F822,F823,E9`: clean.
* `scripts/settings_liveness.py --write` and `scripts/measure_settings_census.py --write`
  were regenerated. The 3 new settings are `live_safe`.

## Two pre-existing test leaks into the live ledger (found and fixed)

1. **`tests/test_forecast_tracker.py::test_non_sqlite_database_url_falls_back_to_the_historical_literal`**
   built a WRITE-mode `ForecastTracker()` whose path resolves to the real
   `settings.LOCAL_DATA_ROOT / "quant_platform.db"`. Every run of that file
   therefore ran `_ensure_table()` (CREATE/ALTER) against the operator's live
   ledger.
   **It happened during this work:** the PostToolUse hook ran that file right
   after F3's new columns were added to the DDL, and `gate_fallback` and
   `gate_admitted` were added to the live `forecast_errors` table at
   during this session on 2026-09-28. They are nullable, and no row was written
   (0 `gated_blend` rows in the live ledger). Every reader in the repo names
   its columns: the one `SELECT *` (`scripts/repair_forecast_errors_horizon.py`)
   uses `sqlite3.Row`. The pre-F3 daemon is unaffected, and F3 would add the
   same columns on first start anyway. I did not drop them: that would be a
   second write to the live DB.
   The test now uses `readonly=True`, since it only checks the resolved path.
2. **`tests/test_orchestrator_e2e.py`'s module-scoped `orchestrator_run`
   fixture** runs a real `_main_body()` BEFORE conftest's function-scoped
   `_isolate_forecast_tracker_db_in_tests` is active. Its bare
   `ForecastTracker()` wrote AAPL/SPY forecast rows at MockDataEngine prices
   (~$10) into the live ledger on every full-suite run.
   The fixture now patches `forecasting.forecast_tracker.resolve_database_url`
   to a temp DB. Verified with a per-test row-count probe plugin: 0 rows
   leak now.

**Live-ledger contamination from leak 2 (read-only count, not cleaned):**

* 2,917 AAPL/SPY rows priced $5–$20, 1,133 of them matured.
* This includes all 16 `blend` rows currently in the ledger. No real daemon
  cycle has run since F1 merged: the daemon stopped 2026-09-27 15:27.
* 168 `naive` rows for each of AAPL and SPY.

These rows feed F1's `skill_vs_naive` aggregates and would feed the gate for
AAPL/SPY. F1's `scripts/clean_forecast_ledger.py` category (a) only covers
the `TEST` symbol. Deleting them is left to the operator (see follow-ups).

## Real ledger, today (read-only, 2026-09-28)

Opened read-only (`ForecastTracker(readonly=True)` / sqlite `mode=ro`):

```
matured naive days per symbol (max / median) by horizon:
  h=10: max 8, median 6, symbols 32
  first naive forecast day: 2026-09-06
window=180d and window=365d, 34 symbols with naive rows:
  h=10: admitted 0, naive 34, max scored n 7
  h=30: admitted 0, naive 34, max scored n 0
  h=60: admitted 0, naive 34, max scored n 0
  h=90: admitted 0, naive 34, max scored n 0
```

`python scripts/forecast_skill_report.py --gate --db ~/.stockpy_local/quant_platform.db`:

```
Naive gate side-by-side (log error)  window=180d  min_price=$1.00
gate: OFF (shadow only)  min_improvement=0.50%  min_obs=60
== horizon 10 trading days: blend vs gated_blend vs naive ==
  n=0, not yet scorable (no gated_blend rows in window)
  gate activity: no gated_blend rows recorded in window yet
... (same for 30 / 60 / 90)
```

The window above is the code default (180). The operator install uses 365,
which gives the same result.

## Open decisions / follow-ups

1. **The 2-week review will be nearly empty** (see the top of this file). The
   n ≥ 60 per-(symbol, horizon) floor against naive rows that start
   2026-09-06 means no admission is possible before about mid-December (h=10)
   or mid-January (h=30).
   Options for the operator, none implemented here:
   * (a) wait;
   * (b) pool gate stats across symbols per horizon, so one n is shared;
   * (c) reconstruct naive for the Jul–Sep model rows from `price_bars`
     closes on each forecast day. This is real data, but the price is not the
     one recorded at forecast time, so it is a scoring-semantics change;
   * (d) temporarily lower `FORECAST_NAIVE_GATE_MIN_OBS`.
2. **Clean the AAPL/SPY test contamination** out of the live ledger. The
   operator runs the delete; category (a) of `clean_forecast_ledger.py` could
   be extended.
3. The two gate columns already on the live table (leak 1) are harmless, and
   are noted here for the record.
4. Prophet's exception fallback, which returns the last price as "prophet",
   still enters `model_forecasts` (a pre-existing F2 follow-up). Under the gate
   it would be scored like naive and could never beat it by 0.5%, so the gate
   contains it. F4 decides Prophet's fate.
