# Forecast Itô Double Correction and Horizon Units

## 1. Issue Summary

A math/calibration audit of the forecasting and LLM subsystems found seven verified bugs (F1-F7)
plus a structural gap: nothing in the repo had ever measured whether any forecast was any good. This
document covers the forecast-math findings (F1, F2, F4, F5, F6, F7) and the measurement layer added to
catch this class of bug going forward (WP6). The LLM-layer findings (options meta-labeler calibration,
LLM sizing input, ResearchCopilot validation naming) are tracked separately in this same audit's PR
description and in `ml/options_meta_labeler.py`'s own docstrings — this file is scoped to the
forecast-math half plus one directly-coupled options-engine regression the fix surfaced.

- **F1 (Monte Carlo Itô Double Correction):** `run_monte_carlo` previously applied an Itô drift term
  `(μ − 0.5σ²)` to the simulated paths. Because `μ` (expected log-return) already incorporates the
  `−0.5σ²` drag relative to simple returns, subtracting it again resulted in a systematic downward
  bias (double correction) that artificially depressed long-horizon Monte Carlo bands. Fixed
  unconditionally (no settings flag — this is a bug fix, not a modelling choice).
- **F2 (drift is mostly noise):** the daily-return mean's standard error over a typical 504-day
  lookback at 30% annualized vol is ~7.6% at T=90, versus a realistic 10%/yr expected drift of only
  ~3.6% at T=90 — the measured mean is far noisier than the measured variance (Merton 1980). Addressed
  via a new opt-in `settings.FORECAST_DRIFT_SHRINKAGE` (default `0.0` = no shrinkage, today's measured
  drift used as-is) rather than an unconditional fix, since this is a modelling choice, not a bug.
- **F3 (Monte Carlo determinism):** `run_monte_carlo` used the unseeded global `np.random.normal`, so
  `MC_Target`/`MC_Lower`/`MC_Upper` wobbled run-to-run by roughly `sigma*sqrt(T)/sqrt(simulations)`
  even against identical input data. Fixed via a local `np.random.default_rng(seed)` seeded from
  `settings.FORECAST_MC_RANDOM_SEED` (default `42`, matching `CNN_LSTM_RANDOM_SEED`'s convention), and
  `simulations` raised `1000 → 4000` (halves sampling s.e.; the vectorized array cost is negligible).
- **F4 (purged train/val split leaked exactly `max_h` rows):** the CNN-LSTM's internal purge only
  removed input-window overlap, not the label-horizon overlap, leaking future information into the
  epoch-selection signal `EarlyStopping` uses.
- **F5 (horizon units & due-date scoring):** the actualization of forecasts in `ForecastTracker` was
  evaluated against calendar days instead of trading days, and a forecast that became overdue while a
  backlog built up was scored against whatever price happened to be current when `update_actuals`
  finally ran, not the price on that forecast's own due date.
- **F6 (Holt-Winters laundering a naive forecast):** `forecast_from_hw_fit` returned
  `float(history[-1])` (a random walk) on fit failure and this was recorded to the tracker under the
  `holt_winters` model name, inflating its measured skill relative to ARIMA (whose sibling correctly
  returned `nan` and was excluded).
- **F7 (transformer h=1 vol label is identically zero):** `np.std` over a single return is exactly
  `0.0` by construction, so the transformer's `1d` horizon was fit to an all-zero target.

## 2. Remediation

- **F1:** Removed the `(μ − 0.5σ²)` term from `run_monte_carlo` in `forecasting_engine.py`. The
  simulation now correctly scales with `μ * dt`. `E[S_T] = S_0 * exp((μ + ½σ²)T)` is a *consequence* of
  this formula (the lognormal mean), not a bias to subtract out of the simulated path.
- **F2:** `settings.FORECAST_DRIFT_SHRINKAGE` (default `0.0`, opt-in) applies
  `mu_used = mu * (1 - shrinkage)`. `1.0` reproduces a pure random walk (the defensible null given the
  s.e. numbers above).
- **F3:** `run_monte_carlo` gained a `seed: Optional[int] = None` parameter, sourced from
  `settings.FORECAST_MC_RANDOM_SEED` (default `42`) when not explicitly overridden, via a local
  `np.random.default_rng()` — never mutates global NumPy RNG state. `simulations` default raised
  `1000 → 4000`.
- **F4:** `ForecastingEngine.purged_train_val_split`/`cnn_lstm_worker.py::_purged_train_val_split`
  changed `embargo = max(0, lookback - 1)` → `embargo = max(0, lookback - 1 + max_h)`. The degenerate
  case (embargo exhausts the training set on a short history) now logs a WARNING naming the leak it is
  accepting instead of silently reverting to an unpurged split.
- **F5:** Two independent fixes in `ForecastTracker`. (a) The eligibility cutoff now offsets by
  `pd.offsets.BDay(horizon_days)` (trading days) instead of `timedelta(days=horizon_days)` (calendar
  days) — a 30-bar forecast now actualizes after 30 trading days, not ~21. (b) `update_actuals` now
  resolves EACH pending row's own due-date close (`forecast_ts + horizon_days` trading bars) via a
  lazily-imported `data.historical_store.HistoricalStore`, instead of stamping every overdue row with
  whatever price happened to be passed in when the call finally caught up with a backlog. A row whose
  own due-date close can't be resolved falls back to the legacy passed-in price for that row alone
  (CONSTRAINT #6 — never stranded pending forever). Gated by
  `settings.FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED` (default `True` in production; the test suite's
  own `conftest.py` autouse fixture disables it so a bare unit test's `ForecastTracker` never reaches a
  live `HistoricalStore`/network path). A historic-repair script
  (`scripts/repair_forecast_errors_horizon.py`) amends the `forecast_errors` table's `actual_price`/
  `squared_error` columns from real closes without discarding the 2.36M already-recorded rows.
- **F6:** `forecast_from_hw_fit` now returns `float('nan')` on fit failure, matching ARIMA's sibling.
  `generate_forecast`'s existing `if h_res > 0` admission check then excludes it from the blend and
  from `ForecastTracker.record_forecasts`.
- **F7:** `HORIZONS` in `api/pilots_api.py`'s transformer-vol-forecast endpoint dropped `1` (a 1-day
  realized-vol label from a single return is not a meaningful training target); the remaining labels'
  `ddof` was changed `0 → 1` to match the `ddof=1` rolling features it's regressed against.

### WP6 — measurement layer (partial; this is the reason none of the above was caught)

- **Naive baseline (shipped):** a zero-cost `"naive"` pseudo-model (`forecasting/forecast_tracker.py`'s
  `MODEL_NAIVE`) is recorded at every horizon alongside the real models, via the SAME
  `record_forecasts` call (added to a copy of `model_forecasts`, never to `model_forecasts` itself, so
  it can never enter the blend). This makes "does any model beat *price stays flat*?" answerable for
  the first time.
- **Interval coverage / CRPS / pinball loss on tracker output (deliberately deferred):** a genuine
  coverage report needs the published `Forecast_h_Lower/Upper` band persisted alongside each point
  forecast — an additive `forecast_errors` schema change (`forecast_lower`/`forecast_upper` columns)
  plus new call-site wiring in `forecasting_engine.py`'s per-horizon loop. `pinball_loss` and
  `interval_coverage` already exist as pure functions (`validation/forecast_accuracy_metrics.py`) but
  are not yet wired to `ForecastTracker`'s own realized rows or surfaced on Mission Control's Forecast
  Skill section. Left as an explicit, disclosed follow-up rather than a rushed, undertested schema
  migration in the same pass as the correctness fixes above.

### A regression this fix surfaced and also fixed: GARCH `None` cascading into unrelated indicators

`estimate_gjr_garch_volatility_term_structure`'s insufficient-history branch was changed from a
fabricated `{h: 0.20 for h in horizons}` to `None` (CONSTRAINT #4 — there isn't enough history below
22 rows to measure even a 20-day historical-stdev fallback, so there is no honest number to return
either). Two callers subscripted the result unconditionally:

- `estimate_gjr_garch_volatility()` (the horizon=1 scalar wrapper) did `...term_structure(...)[1]`,
  raising `TypeError` on `None`. Every production caller happens to wrap this in a broad
  `try/except Exception`, so it degraded to `nan` *by accident* — fixed to check for `None` explicitly
  and return `nan` directly, matching the function's own docstring instead of relying on an incidental
  exception to propagate correctly.
- `pipeline/production_steps.py::OptionsAnalysisStep._options_one` did `vol = garch_term_structure[1]`
  inside a per-ticker `try` block that ALSO computes Aroon/Coppock/Chandelier/`Realized_Vol_Rank`/
  `True_IVR`/`VRP`/`Option_Strategy_Matrix` — none of which depend on GARCH. The `TypeError` was caught
  by that same block's `except Exception`, which discarded the ENTIRE per-ticker result
  (`tech_opt_indicators[ticker]` simply didn't exist for the cycle), not just the GARCH-derived fields.
  Fixed with an explicit `None` check that degrades `vol` to `nan` and lets the rest of the computation
  proceed — `vrp = current_iv - vol` propagates the NaN and correctly gates the VRP leg closed
  (the existing, unrelated contract this repo already documents for VRP), and
  `generate_option_strategy_matrix`'s own `if not math.isfinite(current_iv) or not math.isfinite(true_ivr)`
  guard degrades cleanly to `Cash (Wait)` when both inputs end up NaN. Regression test:
  `tests/test_options_analysis_step_garch_none.py`.

### A second regression this fix surfaced and also fixed: options meta-labeler NaN sizing

`ml/options_meta_labeler.py`'s `predict_probability` was independently changed (same audit, WP4) from
a fabricated `0.65` fallback to `nan` when scoring is declined. `get_sizing_multiplier(nan, ...)` was
NOT updated to match: `nan < min_confidence` is `False` in Python (every comparison against NaN is),
so the function fell through past the low-confidence gate into `edge = nan - 0.50` → `nan` →
`np.clip(nan, ...)` → `nan`, and `score_option_directive`'s `approved = sizing_mult > 0.0` then
evaluated to `False` — an unresolved ML score was silently treated as a REJECTION, not the intended
neutral "no opinion" (previously, the `0.65` fallback always resolved to exactly `1.0x`/approved).
Fixed: `get_sizing_multiplier` now explicitly checks `np.isfinite(prob)` first and returns the neutral
`1.0x` when it's `False`, and `score_option_directive` reports `probability_available: False` /
`prob_win: None` (never a raw NaN float) alongside that neutral multiplier.

## 3. Numeric Impact

### DB Repair Record
Live database state (`~/.stockpy_local/quant_platform.db`, read-only inspection):
`forecast_errors` holds 2,362,035 rows, 379,606 actualized, spanning 2026-07-10 → 2026-09-06.
h=60 and h=90 have zero actualized rows across every model, so those horizons have never had skill data at all.
See `scripts/repair_forecast_errors_horizon.py --dry-run` for the current-state repair report; the
operator (not the agent — matching the precedent in
`docs/known_issues/pr872_live_db_test_contamination_2026.md`) runs `--apply` after a `sqlite3
".backup"` snapshot.

### Bug Specifics
| # | Finding | Evidence |
|---|---|---|
| **F1** | **Itô correction applied to a drift that already contains it.** `mu = log_returns.mean()` (`forecasting_engine.py:1397-1398`) is already a log drift; `daily_drift = (mu - 0.5*sigma**2)*dt` (`:187`) subtracts ½σ² a second time. | Verified with 400k paths: every output (`MC_Target`, `MC_Lower`, `MC_Upper`) shifted down by exactly `exp(−½σ_d²T) − 1`. |
| **F2** | **The drift is 2-6x more noise than signal.** s.e. of `mu` over 504d at 30% vol = ±7.6% at T=90; true 10%/yr drift over 90d = 3.6%. | Merton (1980): variance is estimable from a short sample, the mean is not. |
| **F3** | **`np.random.normal` unseeded**, `simulations=1000`. `MC_Target` wobbles run-to-run. | Computed from `σ√T/√1000`. |
| **F4** | **Purged train/val split leaks exactly `max_h` rows.** `embargo = max(0, lookback - 1)` (`:571`) purges input overlap only; training labels reach `end−1+max_h`. | With lookback=60, max_h=90, n=1000: last training label is row **889**, first validation *input* is row **800** — 90 rows of overlap. Correct embargo is `lookback − 1 + max_h = 149`. |
| **F5** | **Tracker horizon is calendar days; forecasts are trading bars — and a backlog is scored against today's price, not the due-date's.** `cutoff_dt = as_of - timedelta(days=horizon_days)` (`forecast_tracker.py:369`) vs `run_monte_carlo`'s `dt = 1 trading day`, `ARIMA.forecast(steps=h)`, CNN-LSTM target `close[last+h]`. A 30-bar forecast is scored after 30 calendar days ≈ 21 bars — **30% short**. | `docs/known_issues/forecast_tracker_early_actualization.md` fixed a different bug (a `tolerance_days` subtraction) and never mentions calendar-vs-trading days. |
| **F6** | **Holt-Winters launders a naive last-price forecast under its own name**, inflating its measured skill. | `forecast_from_hw_fit` returned `float(history[-1])` on fit failure; ARIMA's sibling correctly returns `nan`. |
| **F7** | **Transformer `h=1` vol label is identically zero.** | `np.std(future_rets, ddof=0)` over a single return is exactly `0.0` by construction. |

## 4. Verification

- `pytest tests/test_forecasting_engine.py -k monte_carlo` — the rewritten Monte Carlo tests recover a
  known GBM's median (`S₀·exp(mT)`) and mean (`S₀·exp((m+½σ²)T)`) from simulation.
- `pytest tests/test_forecasting_lookahead.py -k Purged` — asserts the real property (max training
  label index < min validation input index), not just the input-overlap condition.
- `pytest tests/test_forecast_tracker.py::TestUpdateActualsDueDateLookup` — the F5 due-date-close
  lookup, its fallback-on-failure path, and the nearest-prior-bar tie-break.
- `pytest tests/test_options_analysis_step_garch_none.py` — the GARCH-`None`-cascading regression.
- `pytest tests/test_options_meta_labeler.py tests/test_options_paper_executor.py` — the NaN-sizing
  regression and the honest-degradation contract generally.
- `pytest tests/test_technical_options_engine.py tests/test_forecasting_improvements.py
  tests/test_forecast_skill_uplift.py tests/test_cnn_lstm_worker.py tests/test_forecast_accuracy_metrics.py
  tests/test_pilots_api.py` — full regression sweep of every touched module.
