# Fix GARCH horizon=1 hardcoding in multi-day Monte Carlo forecasts — Implementation Plan

## Context

`technical_options_engine.py::estimate_gjr_garch_volatility()` fits a real
GJR-GARCH(1,1)-with-Student-t model but always calls `res.forecast(horizon=1)`
and returns a single annualized vol derived from that one-day-ahead variance.
`forecasting_engine.py::generate_forecast()` converts that ONE number to a
daily sigma and reuses it, unscaled, for every one of the four forecast
horizons (10/30/60/90 days) via `run_monte_carlo`'s `sigma * sqrt(T)` GBM
scaling. That scaling is only correct for i.i.d. shocks; it throws away
GARCH's actual value — conditional-variance mean-reversion toward the
long-run unconditional level over the forecast window.

Measured impact (synthetic GARCH fit on a series with a recent vol shock,
naive `sigma_1*sqrt(T)` vs. `res.forecast(horizon=T)`'s real multi-step
path): naive scaling overstated the true T-day sigma by ~38% at T=10,
growing to ~122% at T=90. I reproduced the same shape directly against the
installed `arch` library (16%→122% on my own synthetic shocked series) before
touching production code, and confirmed empirically that `res.forecast(horizon=1)`
and `res.forecast(horizon=90)` return an identical first-step variance (the
analytic recursion's `h.1` is invariant to how many further steps are
requested), and that `arch`'s analytic method works fine for GJR-GARCH at
horizon>1 with `dist='t'` — so horizon=1 callers (`GARCH_Vol` column, VRP,
IVR, sizing) stay byte-identical while horizon>1 callers get the real fix.

This is a signal-engines change → feature branch + PR per CLAUDE.md.

## Fix design

### 1. `technical_options_engine.py`

Added `estimate_gjr_garch_volatility_term_structure(self, df, horizons=(1,)) -> Dict[int, float]`:
- Same fit as before (scaled returns ×100, `arch_model(vol='GARCH', p=1,o=1,q=1, dist='t')`).
- ONE `res.forecast(horizon=max(horizons))` call (not one fit per horizon).
- For each requested horizon `T`: cumulative variance = `sum(forecast.variance.iloc[-1].values[:T])`;
  effective annualized vol = `sqrt(cum_var / T) * sqrt(252) / 100`, bounded to `[0.02, 3.0]`.
- Same fallback behavior as before (ARCH unavailable / fit exception / insufficient
  history → 20-day historical annualized stdev, or the neutral 0.20 default),
  applied flatly to every requested horizon.
- `estimate_gjr_garch_volatility(self, df)` is now a thin wrapper:
  `return self.estimate_gjr_garch_volatility_term_structure(df, horizons=(1,))[1]`.
  Verified numerically identical to the pre-refactor implementation for horizon=1.

### 2. `forecasting_engine.py`

- Added `_estimate_daily_sigma_multi_horizon(self, history_df, fallback_daily_sigma, horizons, precomputed_garch_term_structure=None) -> Dict[int, float]`:
  precedence per horizon: (a) caller-supplied `precomputed_garch_term_structure`
  when it covers every requested horizon with finite positive values; else
  (b) a fresh `TechnicalOptionsEngine().estimate_gjr_garch_volatility_term_structure(history_df, horizons=horizons)`
  fit (ONE fit for all needed horizons); else (c) `fallback_daily_sigma` for
  every horizon. Never raises.
  `_estimate_daily_sigma` (singular, pre-existing) is left completely
  untouched — it's independently exercised by `Gravity AI Review Suite.py`'s
  own check.
- `generate_forecast(..., precomputed_garch_term_structure: Optional[Dict[int, float]] = None)`
  (new parameter, kept alongside the existing `precomputed_garch_annual_vol`,
  which is superseded for the horizon-loop math — a single scalar can no
  longer supply a genuine multi-day answer, so it no longer short-circuits
  the estimator the way it used to).
- Replaced the single `mc_sigma = self._estimate_daily_sigma(...)` used for
  both the primary `target_days` MC call and every iteration of the
  `for h in horizons` loop with `sigma_by_horizon = self._estimate_daily_sigma_multi_horizon(...)`,
  looked up per horizon at each of the two call sites.

### 3. Production callers — avoid a double fit, thread the term structure through

- `pipeline/production_steps.py::OptionsAnalysisStep._options_one`: fits
  `garch_term_structure = toe.estimate_gjr_garch_volatility_term_structure(df_hist, horizons=(1,10,30,60,90))`
  ONCE; `vol = garch_term_structure[1]` (unchanged for `GARCH_Vol`/VRP/IVR).
  The structure is threaded through as a 4th tuple element into
  `ctx.context_extras["garch_term_structures"][ticker]` (mirrors the existing
  `tech_opt_indicators`/`fund_dtos` context_extras pattern — not a new
  dashboard column, no `config.COLUMN_SCHEMA`/Pandera changes needed).
- `pipeline/production_steps.py::ForecastingStep._forecast_one`: reads that
  dict and passes it as `precomputed_garch_term_structure=...` to
  `fe.generate_forecast(...)` (dropped the now-superseded
  `precomputed_garch_annual_vol=precomputed_garch` from this call site).
- `engine/advisory.py`: Step 5 now fits the full term structure once
  (`garch_term_structure = toe.estimate_gjr_garch_volatility_term_structure(bars_df.copy(), horizons=(1,10,30,60,90))`),
  keeps `garch_vol = garch_term_structure[1]` for sizing (unchanged), and
  Step 6's `fe.generate_forecast(...)` call additionally receives
  `precomputed_garch_term_structure=fresh_garch_term_structure`.

## Tests

- New `tests/test_technical_options_engine.py::TestGarchTermStructure`:
  horizon=1 byte-identical to the scalar method; mean-reversion regression
  check on a synthetic post-shock series (monotonically decreasing vol,
  strictly below the flat sigma_1 broadcast); ARCH-unavailable / insufficient
  -history flat-fallback contract; single-fit-covers-every-horizon
  efficiency check.
- New `tests/test_forecasting_engine.py::TestGarchHorizonScaling`: end-to-end
  proof each horizon gets its own distinct, genuine sigma (not a shared
  value), matching a hand-picked mean-reverting term structure exactly.
- Rewrote `tests/test_forecasting_engine.py::TestPrecomputedGarchSigma` for
  the new contract (term structure short-circuits the estimator; a bare
  legacy scalar no longer does); updated `TestFitOnceRefactor` and
  `TestForecastConeBands` to pass a flat term structure instead of a scalar
  (unrelated to GARCH behavior, just needed to keep skipping the real fit
  for speed).
- Updated every test file mocking `engine.advisory.TechnicalOptionsEngine`'s
  `estimate_gjr_garch_volatility` (advisory.py no longer calls it) to also
  stub `estimate_gjr_garch_volatility_term_structure`: `tests/test_advisory.py`,
  `tests/test_advisory_pause_gate.py`, `tests/test_advisory_double_fetch_caching.py`,
  `tests/test_advisory_dedup_wiring.py`, `tests/test_rationale_verbosity.py`,
  `tests/test_dead_letter_resilience.py`, `tests/test_pipeline_smoke.py`.
- Updated `tests/test_forecasting_improvements.py::TestGarchSigma::test_annualized_garch_vol_is_converted_to_daily_for_monte_carlo`
  to patch the term-structure method (the one `generate_forecast`'s
  multi-horizon loop now actually calls) instead of the scalar one.

## Docs

Updated `docs/architecture/signal-engines.md`'s `forecasting_engine.py` entry
with a dated addendum describing the bug, the fix, and the byte-identical
horizon=1 guarantee.

## Verification

- `pytest tests/test_forecasting_engine.py tests/test_technical_options_engine.py -q` — 50 + 78 passed.
- `pytest tests/test_advisory.py tests/test_pipeline_smoke.py tests/test_state_snapshot_parity.py tests/test_sf_garch_lstm.py -q` — all passed.
- `pytest tests/test_advisory_pause_gate.py tests/test_advisory_double_fetch_caching.py tests/test_advisory_dedup_wiring.py tests/test_rationale_verbosity.py tests/test_dead_letter_resilience.py -q` — all passed.
- `pytest tests/test_forecasting_improvements.py tests/test_quantitative_models.py tests/test_options_selling_backtest_stress.py -q` — all passed (one pre-existing, unrelated ordering-dependent flake in `test_technical_options_engine_indicators` confirmed present on `main` too, via `git stash`, before any of this branch's changes).
