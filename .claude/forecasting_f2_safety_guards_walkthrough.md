# Forecasting rebuild F2: safety guards (walkthrough)

Plan: `.claude/forecasting_rebuild_implementation_plan.md` (phase F2). Branch
`forecast-f2-safety-guards`, on top of F1 (#1066).

## What changed

### 1. Clamp (`FORECAST_CLAMP_SIGMA_K`, default 4.0)

`forecasting_engine.py::apply_forecast_guards` is a pure module-level function.
`generate_forecast` calls it once per horizon, right after `model_forecasts` is
assembled and before `_blend_with_skill`. That is the single point where model
outputs enter the blend.

A model is dropped when

    |ln(model_price / last_close)| > k * sigma_daily * sqrt(h)

* `last_close` is the last Close of the price history the models were fit on.
* `sigma_daily` is the GJR-GARCH term-structure vol for that horizon, the same
  one Monte Carlo uses. The term structure returns annualized vol, so it is
  converted with `annual / sqrt(252)` (inside
  `_estimate_daily_sigma_multi_horizon`). For horizon h, the GARCH term
  structure is defined so that `sigma_daily * sqrt(h)` is the model's own
  cumulative h-day log-return standard deviation.
* `h` is the horizon in trading days (10/30/60/90).

**No GARCH sigma, no clamp.** `_estimate_daily_sigma_multi_horizon` gained
`return_source=True`, which returns `(sigmas, from_garch)`. When the sigma is the
flat historical-stdev fallback (the GARCH flag is off, there are fewer than 22
rows, or the fit failed), the clamp is skipped for that symbol and counted as
`sigma_unavailable`. A sigma is never invented. One caveat: `volatility/garch.py`
itself degrades to a flat 20-day realized vol when `arch` fails. That still
counts as the engine's GARCH source, since it is what Monte Carlo uses.

A value `<= 0` disables the clamp.

### 2. Input-price check (`FORECAST_INPUT_PRICE_TOLERANCE`, default 0.05)

A model is dropped when `|anchor / last_close - 1| > tolerance`. The anchors:

| Model | Anchor | Can the check fire? |
|---|---|---|
| ARIMA, Holt-Winters | `history_series` (last close) | No: fit on that same history |
| Prophet | `history_series` | No |
| CNN-LSTM, BERT-LLA | `history_df['Close']` | No in production (the same frame as `history_series`) |
| Monte Carlo | `current_price` (the row's `Price`) | **Yes**: the only independent anchor |

So in practice only Monte Carlo can trip the check. That is the 2026-08-14 shape:
Monte Carlo seeded at ~$100 while the symbol traded at ~$20. When the check drops
Monte Carlo, its `Forecast_{h}_Lower/Upper` band is not published either, because
the band is as wrong as the mean. With no price history there is nothing to
check against, so the check is skipped (counted as `no_reference_close`).

A value `<= 0` disables the check. Its root cause is chased separately, as the plan says.

### Drop semantics (both guards)

* A dropped model is removed and never replaced.
* **Renormalization.** The skill-weighted branch of `_blend_with_skill` already
  restricted itself to present models and renormalized. The static branch did
  not: its fixed weights assumed Monte Carlo was always present, so a missing
  Monte Carlo left `lstm*0.4 + arima*0.2` (60% of a price) or `lstm*0.5` (half a
  price). That was unreachable before F2, because Monte Carlo always produced
  output. Now, only when Monte Carlo is missing, it renormalizes:
  `(lstm*0.4 + arima*0.2)/0.6`, or `lstm` alone. The chain also falls back to
  Holt-Winters, then Prophet, before `current_price`. With Monte Carlo present
  the arithmetic is unchanged. The Holt-Winters step also closes the gap
  disclosed in `docs/known_issues/forecast_fallback_current_price_disclosure.md`.
* **All models dropped.** The horizon takes the existing no-output path:
  `Forecast_{h} = current_price`, `Forecast_{h}_Is_Fallback = True`, and no
  `blend` row. The brief said "NaN, exactly like today's no-forecast path". Today's
  path is `current_price` plus the flag, not NaN, and
  `pipeline/production_steps.py` fills a NaN `Forecast_30` with `0.0` (a "$0
  target"). I kept the existing path. Item 4 makes that fallback neutral in the
  signal.
* **Logging.** Each drop is logged at DEBUG with its measured value.
  `ForecastingEngine` keeps lock-protected counters: `dropped_clamp`,
  `dropped_input_price`, `all_dropped_horizons`, `sigma_unavailable`,
  `no_reference_close`. The lock is needed because one engine is shared across
  `ForecastingStep`'s thread pool. After the pool, `ForecastingStep` calls
  `pop_guard_stats()` and logs one INFO line per cycle.

### Recording decision

* **Clamped model: the raw row is still recorded.** It is a real forecast from
  real inputs. Scoring it is honest, and it is how the ledger measures how often
  each model blows up (`scripts/forecast_skill_report.py` will show it).
* **Input-price drop: the row is NOT recorded.** That output was computed from the
  wrong starting price, so its error measures the input bug, not the model. Those
  are exactly the rows F1's `scripts/clean_forecast_ledger.py` must delete as
  category (b). Recording them again would recreate the contamination.
* The `blend` row is the guarded blend. It is only recorded when at least one
  model survived.

### 3. Prophet horizon in trading days

`run_prophet_forecast` now calls `make_future_dataframe(periods=h, freq="B")`.
Prophet's own implementation takes the `periods` dates after the last history
date on that frequency, so `forecast.iloc[-1]` is the h-th business day. Before,
the default `freq="D"` meant "30" was 30 calendar days (about 21 trading days),
while the value was blended and scored as 30 trading days. US market holidays
are not excluded ("B" is Mon-Fri), which is off by at most a day or two over 30
bars.

### 4. `forecast_alignment`: missing or fallback → 0

Before F2 there were two bearish paths, in both the per-row `compute()` and the
vectorized `compute_vectorized()`:

* **Missing forecast.** The vectorized path does `Forecast_30.fillna(0.0)`, and
  `forecast_price = 0` fell into `forecast_price <= current_price`, scoring −10.
  (The per-row path already scored NaN as 0, but a literal 0.0 was −10 there too.)
* **Engine fallback.** `Forecast_30 == current_price` with
  `Forecast_30_Is_Fallback = True` scored −10.

Both now score 0, with the explanation "0pts: No usable forecast (missing or
fallback); neutral". The flag reaches the signal as a new optional row feature,
`forecast_is_fallback`:

* `StrategyEngine.evaluate_security(forecast_is_fallback=...)`, which puts it in
  the live `row`. It was added to `ml/meta_bootstrap.py::LIVE_ROW_FEATURE_WHITELIST`,
  which is pinned by a drift test.
* `pipeline/production_steps.py`: `vec_df['forecast_is_fallback']` from
  `Forecast_30_Is_Fallback` (only a real bool counts), plus the per-row call.
* `engine/advisory.py` passes its own `forecast_is_fallback`.

A NaN, None or absent flag means "unknown", which is not a fallback. A real blend
at or below the price still scores −10.

The `forecast_direction_arima_hw` validation adapter never reaches these branches.
It skips dates with no forecast and only scores `|gain| >= 1.5%`, so its recorded
validation numbers are unaffected.

## Verification

### New tests: `tests/test_forecast_rebuild_f2.py` (33 tests)

| Test class | What it covers |
|---|---|
| `TestApplyForecastGuards` | A 1.3e15-ratio ARIMA is dropped while the others are kept; the exact `k·σ·√h` edge in both directions; all dropped → empty; a missing, NaN, zero or negative σ skips the clamp; k <= 0 disables it; the $100-vs-$20 Monte Carlo anchor is dropped; the 5% tolerance edge; tolerance <= 0, no last close, or no anchor skips the check; the input dict is not mutated. |
| `TestBlendRenormalizesSurvivors` | Skill weights renormalize after a drop; the static blend without Monte Carlo renormalizes; with Monte Carlo present the static arithmetic is exactly the pre-F2 expressions. |
| `TestGenerateForecastGuards` | A blown-up ARIMA gives exactly the blend of the other models at all four horizons, with its raw row still recorded and the recorded `blend` equal to the published one. All dropped (tiny k) → `current_price` + `Is_Fallback`, no `blend` row, no Monte Carlo band. The GARCH flag off → no clamp and `sigma_unavailable` is counted. A wrong Monte Carlo anchor (5x) is dropped with its band and is not recorded. A blown-up CNN-LSTM is dropped. |
| `TestNoGuardTripIsByteIdentical` | With precomputed and with fitted GARCH, guards on vs guards off give identical results dicts, and nothing fired. |
| `TestSigmaSourceFlag` | `return_source` reports GARCH vs fallback, and the default call shape is unchanged. |
| `TestProphetTradingDayHorizon` | A fake Prophet asserts `periods=30, freq="B"` and a forecast date of last bar + 30 BDay. Real Prophet asserts the same forecast date, plus a zero-lookahead perturbation: tripling every value after the cutoff leaves the forecast made at the cutoff bit-identical. |
| `TestForecastAlignmentNeutralOnMissing` | NaN, None, 0 and −1 → 0 per-row; fallback → 0; real forecasts unchanged, including a real flat blend still at −10; an unknown flag is not a fallback. The vectorized path matches per-row (score and explanation) on a mixed frame, with and without the flag column. |
| `TestStrategyEngineThreadsFallbackFlag` | `evaluate_security(forecast_is_fallback=True)` → component 0; False with forecast == price → negative. |

**Mutation checks.** Each was run by hand and caught:

* Reverting `freq="B"` fails both Prophet tests.
* Not applying the guarded dict to the blend fails all 4 `TestGenerateForecastGuards` tests.

**Byte-identity against the real pre-F2 code.** `origin/main:forecasting_engine.py`
was loaded as a separate module and run side by side with the F2 engine on 144
synthetic cases (12 seeds × 3 price/vol regimes × 4 sectors, Prophet and TF off,
GARCH fitted). No guard fired in any case, and 144/144 results dicts were
identical.

### Suites

* Targeted existing suites: `test_forecasting_engine`, `test_forecasting_improvements`,
  `test_forecast_rebuild_f1`, `test_vectorized_signal_parity`,
  `test_train_meta_labelers` (whitelist drift test), `test_strategy_engine`,
  `test_signal_module_contracts`, `test_forecasting_step_fallback_disclosure`,
  `test_forecast_skill_uplift`, `test_forecast_parallel`. 455 passed.
* Full offline suite (`pytest -m "not network and not slow" -n auto --dist loadgroup -p no:randomly`): **11,725 passed, 18 skipped, 0 failed.** The first run had 1 failure, `tests/test_forecast_model_persistence.py::TestProphetPersistenceEnabled::test_cache_hit_skips_fit_and_uses_cached_model`. Its fake Prophet's `make_future_dataframe(periods)` did not accept `freq`, so the engine's new `freq="B"` call raised and fell back. The fix gives the fake the real Prophet signature `(periods, freq="D", include_history=True)`.
* `ruff check . --select=F821,F822,F823,E9`: clean.
* `scripts/settings_liveness.py --write` and `scripts/measure_settings_census.py --write` were regenerated. Both new settings are `live_safe` (read per call).

## Decision diff (frozen inputs)

**Method** (`f2_decision_diff.py`, run from the scratchpad, fully read-only):

* **Inputs.** The 2026-09-26 18:33 UTC `~/.stockpy_local/output/state_snapshot.json`
  (29 symbols), and `price_bars` read via sqlite `mode=ro` and truncated to the
  snapshot date. The live skill weights come from `ForecastTracker(readonly=True)`
  with the operator's settings (window 365, min_obs 30, weighting on, Prophet weight 0.25).
* **Engine run.** The F2 engine runs once per symbol with a capturing tracker, so
  nothing is written.
* **Pre-F2 blend.** Recomputed from the same raw model outputs with origin/main's
  `_blend_with_skill`, and with Prophet re-fit the pre-F2 way (calendar days).
  "Guards only" and "Prophet fix only" columns attribute each change.
* **Score and action.** Score = snapshot score components with
  `forecast_alignment` replaced by the recomputed points. It is 50 + the sum of
  components; that reconstruction reproduces all 29 snapshot actions. sellRange
  high = max(price + 3·ATR, Forecast_30), with ATR recovered from the snapshot's
  own Sell Zone.
* **Limits.** Model persistence was off, so CNN-LSTM and Prophet are fresh fits,
  not the persisted models the live cycle used, and CNN-LSTM varies between
  process runs. So the whole thing was run 3 times. The advisory path (Case A/C,
  ±3%) was not re-run; ±3% crossings are flagged instead.

**Baseline caveat.** In run 1, 11 of 29 symbols' recomputed pre-F2 alignment
points differ from the snapshot's own. That comes from the fresh fits and the
bar-source differences, not from F2. The diff below compares pre-F2 vs F2 on the
same recomputed inputs.

**Item 4 (neutral fallback):** 0 of 29 symbols had `forecast_is_fallback = 1` in
the snapshot, so it moves nothing in this cycle.

**Result: across all 3 runs, no symbol's action (BUY/HOLD/SELL tier) and no
sellRange moved.** The symbols whose `forecast_alignment` contribution or
advisory ±3% band moved:

| Symbol | Runs | F30 pre-F2 → F2 (implied %) | align pts | score (action unchanged) | cause |
|---|---|---|---|---|---|
| DIV | 3/3 | 19.30 → 19.32 (1.45 → 1.53%) | +5 → +10 | 76.2 → 81.2 (STRONG BUY) | Prophet fix |
| MPT | 3/3 | 3.58–3.62 → 3.52–3.56 (+0.3/+1.3% → −0.4/−1.4%) | +5 → −10 | 26.3 → 11.3 (RISK REDUCE) | Prophet fix |
| RWT | 3/3 | 3.84 → 3.75–3.76 (3.2 → 0.9%) | +10 → +5 | −52.7 → −57.7 (RISK REDUCE) | Prophet fix; **advisory +3% crossed** |
| MFA | 2/3 | 8.33–8.34 → 8.28 (2.0–2.1 → 1.3–1.45%) | +10 → +5 | 19.1 → 14.1 (RISK REDUCE) | Prophet fix |
| REFI | 1/3 | 10.85 → 10.81 (1.87 → 1.49%) | +10 → +5 | 5.3 → 0.3 (RISK REDUCE) | Prophet fix |
| KRO | 1/3 | 7.68 → 8.48 (−6.9 → +2.8%) | −10 → +10 | 9.5 → 29.5 (RISK REDUCE) | clamp: CNN-LSTM at 4.02σ (run-dependent fit); **advisory −3% crossed** |
| AM | 3/3 | 21.50–21.52 → 21.71–21.74 (2.7–2.8 → 3.7–3.9%) | +10 → +10 | unchanged | Prophet fix; **advisory +3% crossed** |

Guards fired but moved no decision threshold:

* UWMC ($1.22, the only near-sub-$1 name): CNN-LSTM (6.0σ) and Prophet (5.7σ)
  were clamped at h=30, and the forecast went −22.9% → −15.2%. Still −10.
* ET, ABR and PK had CNN-LSTM clamped at 4.0–4.9σ. There were 33 clamp drops in
  total over 3 runs × 29 symbols × 4 horizons.
* No input-price drop fired: the snapshot price equals the last close for every
  symbol.

**Reading.** The Prophet horizon fix, not the guards, moves most contributions.
Prophet now forecasts 30 trading days instead of about 21. The guards mostly trim
CNN-LSTM outliers. The advisory crossings (AM, RWT, KRO) could move advisory
conviction or Case C; that path was not recomputed here.

## Left undone / follow-ups

* The Monte Carlo 2026-08-14 root cause is not chased (out of scope per the plan).
  The input-price check catches the symptom only when the row price disagrees
  with the bar history.
* The advisory path was not re-run in the diff; ±3% crossings are listed instead.
* `run_prophet_forecast`'s exception fallback still returns the last price as
  "prophet", which enters the blend like a real forecast. This predates F2 and is
  left alone; F3/F4 decide Prophet's fate.
* ruff style lints (I001/UP006/DTZ001) in the new test file are not fixed. The
  PreToolUse hook blocks `ruff --fix`, and the repo gate is the
  F821/F822/F823/E9 set, which is clean.
