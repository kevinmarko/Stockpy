# Forecast ledger: contamination cleanup (d) + naive backfill (walkthrough)

Branch `forecast-ledger-contamination-and-naive-backfill`, on top of F3 (#1076).
Plan context: `.claude/forecasting_rebuild_implementation_plan.md` (F1-F3) and
`.claude/forecasting_f3_naive_gate_shadow_walkthrough.md` ("Open decisions" 1c and 2,
both approved by the operator on 2026-09-28).

**No `--apply` was run.** Both dry runs below opened the live ledger read-only
(`mode=ro`); its mtime and size were checked unchanged before and after.
The operator (via the coordinator) runs the applies.

## 1. `scripts/clean_forecast_ledger.py` category (d): price contamination

### The rule

* **cycle** = one `(symbol, forecast_ts)`. Its **anchor** is the cycle's `naive`
  price when it has one (naive is the price at forecast time), else the median
  `forecast_price` of its 10-trading-day rows, else the median of all its rows.
* **reference** = the symbol's daily close from `HistoricalStore` (opened
  read-only, `_read_from_db`, never fetches) on the forecast's US/Eastern day,
  else the last close before it within 5 calendar days (weekends, holidays).
* A row is **flagged** when BOTH the anchor AND the row's own `forecast_price`
  are more than 3x or less than 1/3 of the reference.
* No reference means **unverifiable**, which is never flagged.
* A reference close under $1 means **not checked**: sub-$1 bars are stored
  rounded to $0.0001 / $0.000001, so a ratio against them is noise, and F1
  already excludes sub-$1 names from all scoring.
* Opt-in (`--categories d`). `--d-symbols AAPL,SPY` restricts the delete. The
  report always lists every matched symbol, with a `[NOT SELECTED -- kept]`
  marker on the others.

### Why BOTH, and what was rejected (measured on the live ledger)

* **The row's own price vs the close, alone:** flags 17,315 rows. Most are
  real model blow-ups: UWMC/SINX CNN-LSTM, and ARIMA on sub-$1 names. F2's
  clamp handles those now; they are not contamination.
* **The cycle anchor alone:** flags 5,717 rows. Besides the right ones, it
  catches real ARIMA/Holt-Winters rows at the correct ~$20 that sat in ARCC/CMCL
  cycles whose input price was $100 (2026-09-07 and -11), and 1,859 sub-$1 rows
  whose bars are rounded.
* **AND, with the sub-$1 skip:** 3,730 rows. AAPL/SPY are exactly the 2,917
  rows the brief expected: every AAPL/SPY row under $50, and no AAPL/SPY row
  above it.

### Dry run on the live ledger (full output, `--window-days 365 --min-obs 30`)

```
!! WARNING: LIVE SKILL WEIGHTS CHANGE for 6 (symbol, horizon) pairs of symbols that stay in the ledger (largest single-model weight shift 0.568); their published Forecast_* values move when skill weighting is on. Largest: SPY@10 (|dw| 0.568), AAPL@10 (|dw| 0.405), ARCC@10 (|dw| 0.218), AAPL@30 (|dw| 0.195), SPY@30 (|dw| 0.104).
forecast_errors cleanup -- DRY RUN (nothing deleted; pass --apply to delete)
categories: d
ledger: /Users/kevinlee/.stockpy_local/quant_platform.db
total rows:                           2,422,337
(a) symbol='TEST' rows:                       0   [NOT SELECTED -- kept]
(b) 2026-08-14 MC seed rows:                  0  (0 symbols)   [NOT SELECTED -- kept]
(c) intra-day duplicates:            not selected (opt-in with --categories a,b,c; after F3)
(d) price-contamination rows:             3,730  (rule matched 3,730 rows in 8 symbols)
    per symbol (rows | days | models | forecast_price range | reference close range):
      AAPL      2,301 |  28d 2026-07-10..2026-09-28 | {'blend': 8, 'monte_carlo': 2125, 'naive': 168} | $9.991-$10.01 | $302.2-$341.1
      SPY         616 |  26d 2026-07-10..2026-09-28 | {'blend': 8, 'monte_carlo': 440, 'naive': 168} | $9.991-$10 | $738.2-$777.9
      TPET        507 |   3d 2026-07-13..2026-07-16 | {'arima': 156, 'holt_winters': 156, 'monte_carlo': 156, 'prophet': 39} | $0.02007-$0.3204 | $2.83-$3.02
      VIVK        160 |   3d 2026-07-13..2026-07-16 | {'monte_carlo': 160} | $0.03343-$0.2604 | $4.26-$6.14
      ARCC         64 |   2d 2026-09-07..2026-09-11 | {'monte_carlo': 32, 'naive': 32} | $100-$102.8 | $19.26-$20.04
      CMCL         64 |   2d 2026-09-07..2026-09-11 | {'monte_carlo': 32, 'naive': 32} | $100-$104.7 | $25.7-$26.33
      GRNQ          9 |   1d 2026-07-18..2026-07-18 | {'arima': 4, 'holt_winters': 4, 'prophet': 1} | $0.9455-$1.236 | $12.5-$12.5
      PRPL          9 |   1d 2026-07-18..2026-07-18 | {'arima': 4, 'holt_winters': 4, 'prophet': 1} | $0.04497-$0.2657 | $7.24-$7.24
    unverifiable rows (no bar within 5d, never flagged): 38,667 in 28 symbols
    sub-$1 reference close, not checked: 42,195
    near misses (2x-3x, kept): 2,175 {'APH': 2175}
rows deleted in total:                    3,730
Skill-weight impact (window=365d, min_obs=30):
  ... dropping to equal-weight cold start:             4  ['TPET@10', 'TPET@30', 'VIVK@10', 'VIVK@30']
  (symbol, horizon) pairs removed entirely:            8  (GRNQ/PRPL/TPET/VIVK @10/@30)
  pairs of remaining symbols whose weights change:     6  (max shift 0.568, SPY@10)
```

(Trimmed here for width; the script prints an example row per symbol and the
full rule text.)

### Non-AAPL/SPY hits, for the operator to judge

* **ARCC, CMCL (64 rows each).** On 2026-09-07 (Labor Day) and 09-11, 8 cycles
  have `naive` = $100.00 exactly, and Monte Carlo is seeded near $100. The real
  closes were $19-20 and $26. In those same cycles ARIMA/Holt-Winters sit at the
  real price and are NOT flagged. This is the same "$100 input price" shape as
  the unexplained 2026-08-14 Monte Carlo seed bug (category b); its root cause
  is still unknown. These naive rows matter: they are matured and in the gate
  window, and a $100 naive against a $19 outcome gives naive a |log error| of
  about 1.6 on those days, which makes any model look better than naive.
  **Recommend deleting.**
* **TPET (507), VIVK (160), GRNQ (9), PRPL (9).** Early-July cycles
  (07-13..07-18) whose prices are 10-30x BELOW the real close. Every model in
  the cycle agrees with that wrong level, so the input price was wrong, not a
  single model. The `actual_price` those rows got is the real bar, so their
  errors are huge. **Recommend deleting.** Deleting them moves TPET/VIVK @10/@30
  to the cold start and removes GRNQ/PRPL's only rows.
* To delete only the test leak: `--d-symbols AAPL,SPY` (2,917 rows).

### Found, but not flagged by (d) (for the record)

* **SYM0..SYM11:** 12 synthetic symbols, 545 rows each, ~$100, 2026-07-10..08-19.
  They look like another test leak, but have no bars, so they are
  "unverifiable" and never flagged. Not deleted by anything here; category (a)
  could be extended if the operator agrees.
* **APH:** 2,175 rows at 2.0-2.07x the close. This looks like a 2:1 split that
  the stored bars adjusted for, and the forecasts predate. It is a near miss,
  kept.
* **28 symbols with no stored bars** (MPW, FFIC, ...) are unverifiable.

## 2. `scripts/backfill_naive_forecasts.py`

### What the live code writes, and what the backfill reproduces

Live `naive` = `current_price` = the cycle's dashboard `Price`
(`ForecastingStep` passes `row['Price']`). It is recorded with the cycle's
`forecast_ts`, and `forecast_day` = `eastern_trading_day(forecast_ts)`.
Measured against the live naive rows, that `Price` is:

* **pre-open:** the previous close (89% exact);
* **after the close:** about the day's close (median diff 0.04%);
* **in session:** an intraday price, which daily bars cannot reproduce.

Per `(symbol, horizon, US/Eastern day)` with a real model row (not in
`NON_BLEND_MODEL_NAMES`, not a clean (a)/(b)/(d) row) and no `naive` row:

* **`forecast_ts`** = the key's latest real-model `forecast_ts`. That is the
  cycle `_latest_row_per_day` keeps.
* **`forecast_price`** = the **last completed session's close at that
  timestamp**: the same ET day's close at or after 16:00 ET, else the last
  trading day before it. It comes from `HistoricalStore` read-only, and the bar
  must be at most 5 calendar days old, else the key is skipped (`no_bar`).
  This is lookahead-free.
* **Why not "the last close strictly before the session":** the operator's
  suggested default, "always the previous close", was measured against the
  live rows. For after-close cycles it is off by a median 0.86%, vs 0.04% for
  this rule. It would also make naive one day staler than the models' input,
  which biases the gate toward admitting models. So the after-close case uses
  the same day's close.
* **`actual_price` / `squared_error`:** `update_actuals` resolves the price
  from `forecast_ts + BDay(h)` only. A live naive row sharing the cycle's
  `forecast_ts` is therefore stamped in the same call, with the same price, as
  that cycle's model rows. So:
  * a key whose siblings are matured gets their `actual_price` (all must
    agree, else `sibling_actual_conflict`);
  * a key whose siblings are pending is inserted pending, and the live daemon's
    `update_actuals` matures both together. No second implementation.
  * `squared_error` uses `update_actuals`'s formula.
* **Skipped:**
  * sub-$1 reconstructed price (F1);
  * today and later (the daemon owns today);
  * keys whose only naive row is a (d) row (`blocked_by_uncleaned_d`; run
    clean (d) first).
* **Marking:** the schema has no provenance column. Every inserted row gets
  one `recorded_at` stamp (the run time), and the ids go to
  `<backup-dir>/naive-backfill-manifest-<stamp>.json`.
  Identify with `model_name='naive' AND recorded_at='<stamp>'`. Undo is the
  same `DELETE`. `recorded_at` is not read by any decision path.

### Dry run on the live ledger (window 365d, skill min_obs 30, gate min_obs 60)

```
rows to insert: 31,360 (9,620 matured from their cycle's siblings, 21,740 pending) in 800 symbols
forecast days: ['2026-07-10', '2026-09-06']  (first live naive day 2026-09-07; 0 of the rows fill gaps on/after it)
by horizon:
  h=10:   7,840 rows  matured   7,832  pending      8  symbols 800
  h=30:   7,840 rows  matured   1,788  pending  6,052  symbols 800
  h=60:   7,840 rows  matured       0  pending  7,840  symbols 800
  h=90:   7,840 rows  matured       0  pending  7,840  symbols 800
last-cycle session of the backfilled days: {'after_close': 25756, 'in_session': 5568, 'pre_open': 36}
skipped: {'blocked_by_uncleaned_d': 8, 'today_or_later': 0, 'no_bar': 1772, 'sub_dollar': 980, 'sibling_actual_conflict': 0}
Reconstruction vs LIVE naive rows (n=2,108 keys where both exist): exact 65.6%, median |ln diff| 0.00000, p90 0.01209
  after_close  n=   820  exact 43.4%  median 0.00038  (prev-close rule 0.00862)
  in_session   n=   252  exact 42.9%  median 0.00167  (prev-close rule 0.00167)
  pre_open     n= 1,036  exact 88.8%  median 0.00000  (prev-close rule 0.00000)
Live skill-weight impact: raw weight dicts change for 63 (symbol, horizon) pairs; cold-start status changes for 0;
  max real-model weight shift after _blend_with_skill's renormalization 5.55e-17
Naive-gate n (window=365d, min_obs=60, margin 0.50%):
  h=10: max n now 5 -> 44; admitted now 0; first n=60: WITH earliest 2026-10-19 median 2026-10-21 latest 2026-12-04 | WITHOUT earliest 2026-12-10 median 2026-12-11
  h=30: max n now 0 -> 26; admitted now 0; first n=60: WITH earliest 2026-11-16 median 2026-11-18 latest 2027-01-01 | WITHOUT earliest 2027-01-07 median 2027-01-08
```

The per-symbol table (32 active symbols) is in the script's output. Most active
names go from 5 to 42 at h=10 and from 0 to 26 at h=30. The younger names catch
up later: IBN, PBF, SKHY and T reach n=60 between late November and early
December at h=10.

### Disclosures

* **In-session cycles.** 18% of backfilled days (5,568 of 31,360 rows) had
  their last cycle in session. There live saw an intraday price; the backfill
  uses the previous close (median gap 0.17% against real rows). This is a small,
  unbiased-in-sign difference, not lookahead.
* **`naive` is in the skill-weight arithmetic** (F1 kept it there). The raw
  weight dicts of 63 pairs gain a naive entry. After `_blend_with_skill`
  renormalizes over real models, the largest shift is 5.55e-17 (float noise),
  and no pair changes cold-start status. Published `Forecast_*` values do not
  move.
* **The projection is about `n` only**, not about whether a model beats naive.
  Future days assume each (symbol, model, horizon) active in the last 5
  business days keeps writing one row per trading day. "n now" comes from the
  REAL `ForecastTracker.naive_gate_stats` run on a scratch copy of the ledger
  (no hand-rolled cutoff).
* **The first real `naive` rows are from 2026-09-07.** The 2026-09-06 rows are
  the AAPL/SPY test contamination.

## 3. Tests

`tests/test_forecast_ledger_contamination_and_naive_backfill.py`: 18 tests,
every one on a tmp ledger with an explicit path.

| Class | Covers |
|---|---|
| `TestCategoryD` | A test-price cycle is flagged and a same-day real cycle is not. A real model blow-up is not flagged. A bad-input cycle flags naive/MC, not the correct ARIMA. Sub-$1 is not checked. No bars and a stale bar are unverifiable. A 2.5x near miss is listed, not flagged. Saturday checks against Friday's bar. No `price_bars` table flags nothing. The `--d-symbols` filter. Opt-in totals. Apply backs up first and deletes only (d). CLI. |
| `TestBackfillEqualsLiveCode` | Runs the REAL `ForecastingEngine.generate_forecast` with a tracker, deletes the naive rows it wrote, backfills, and asserts identical `(h, forecast_ts, forecast_day, forecast_price, actual_price)` at all 4 horizons. Runs the REAL `update_actuals` (due-date lookup on) on a ledger with naive + sibling pending, and on one with the sibling only, then backfills the second: `actual_price` and `squared_error` are equal. |
| `TestBackfillPlan` | After-close uses the same-day close. Pre-open uses the previous close. Matured siblings copy; pending stays pending. Measurement-only names never seed a row. No bar, sub-$1 and today are skipped. A stale bar is skipped. A sibling conflict is skipped. (d) rows never seed and a (d)-only naive blocks until clean (d) is applied, then it backfills. |
| `TestBackfillCli` | The dry run writes nothing (row count and max id unchanged). Apply backs up first, rows are identifiable by stamp, the manifest is written, and a rerun plans 0. A failed backup writes nothing. A mid-transaction failure rolls back. |
| `TestProjection` | 70 matured model days: gate n goes 0 -> 70 via the real `naive_gate_stats`, and the day-based projection agrees. |

Two existing F1 tests were adjusted: `"a,d"` is now a valid category (the
invalid example is `"a,e"`), and the `analyze` monkeypatch accepts `**kw`.

**Mutation checks** (each applied, the new file run, then restored). All 12 were caught:

* (d) anchor-only
* (d) row-only
* (d) no sub-$1 skip
* (d) no staleness tolerance
* backfill always-previous-close
* backfill same-day close (lookahead)
* (d) not excluded from the backfill
* no sub-$1 skip in the backfill
* today allowed
* always pending
* first cycle instead of last
* no bar-age limit

## 4. Apply order (operator/coordinator; stop the daemon first)

1. `python scripts/clean_forecast_ledger.py --categories d --window-days 365 --min-obs 30 --apply`
   (or add `--d-symbols AAPL,SPY` to delete only the test leak)
2. `python scripts/backfill_naive_forecasts.py --apply`
3. Optional: `VACUUM` with the daemon stopped.

Both take a verified online backup to `<LOCAL_DATA_ROOT>/backups/` first and
write in one transaction.
