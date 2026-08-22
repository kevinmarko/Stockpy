# Fix GARCH horizon=1 hardcoding — Walkthrough

## The bug

`technical_options_engine.py::estimate_gjr_garch_volatility()` fit a real
GJR-GARCH(1,1)-with-Student-t model but always called `res.forecast(horizon=1)`,
returning a single 1-day-ahead annualized vol. `forecasting_engine.py::generate_forecast()`
converted that one number into a daily sigma and reused it, unscaled, for
every one of the four forecast horizons (10/30/60/90 days) via
`run_monte_carlo`'s `sigma * sqrt(T)` GBM scaling — mathematically equivalent
to treating daily shocks as i.i.d., which throws away GARCH's actual
value: conditional-variance mean-reversion toward the long-run unconditional
level over the forecast window.

Every multi-day Monte Carlo confidence band (`MC_Lower`/`MC_Upper`,
`Forecast_10/30/60/90_Lower/Upper`) was affected whenever current realized
vol deviated from its long-run level — i.e. most of the time, and worst
exactly in post-shock, elevated-vol regimes, which is also when accurate
sizing/risk bands matter most. `settings.FORECAST_USE_GARCH_SIGMA` defaults
`True`, so this was live production behavior.

## The fix

`estimate_gjr_garch_volatility_term_structure(df, horizons)` fits GJR-GARCH
ONCE and calls `res.forecast(horizon=max(horizons))` ONCE, producing the
model's own per-step variance path for every day out to the longest
requested horizon. For each horizon `T`, the cumulative variance over the
first `T` steps is averaged and re-annualized:

```
cumulative_variance_T = sum(res.forecast(horizon=max_h).variance.iloc[-1].values[:T])
annualized_vol_T = sqrt(cumulative_variance_T / T) * sqrt(252) / 100
```

This is the value that, fed back through the existing
`sigma_daily = sigma_annual / sqrt(252)` conversion and `run_monte_carlo`'s
`sigma * sqrt(T)` scaling, reproduces GARCH's own cumulative T-day variance
forecast — i.e. it captures mean-reversion instead of ignoring it.

`estimate_gjr_garch_volatility()` (the original scalar method) is now a
thin `horizons=(1,)` wrapper around this, verified numerically byte-identical
to the pre-fix implementation — every existing horizon=1 caller (the
`GARCH_Vol` dashboard column, the VRP gate, True IVR, position sizing) is
unaffected.

`forecasting_engine.py::generate_forecast()` now computes
`sigma_by_horizon = self._estimate_daily_sigma_multi_horizon(history_df, sigma, needed_horizons, precomputed_garch_term_structure)`
once, and looks up `sigma_by_horizon[target_days]` / `sigma_by_horizon[h]`
at its two `run_monte_carlo` call sites — each horizon now gets its own
mean-reversion-aware sigma instead of one flat value scaled by `sqrt(T)`.

Both production callers (`pipeline/production_steps.py`'s options-analysis
step, `engine/advisory.py`'s Step 5) were updated to fit the full term
structure ONCE per ticker/call (reusing it for both their existing
horizon=1 uses — `GARCH_Vol`, VRP, True IVR, sizing — and the new
per-horizon forecast sigma), so no ticker is GARCH-fit twice per cycle,
preserving the redundant-fit-avoidance the pre-existing
`precomputed_garch_annual_vol` plumbing was there for.

## Validation before touching production code

Before writing any code, I validated the fix formula against the installed
`arch` library directly: fit a GJR-GARCH on a synthetic series with a
recent volatility shock, then compared naive `sigma_1*sqrt(T)` against the
genuine multi-step cumulative-variance calculation. Naive scaling overstated
the true T-day sigma by 16% at T=10, growing to 122% at T=90 — the same
shape and rough magnitude as the bug report's own measured numbers. I also
confirmed `res.forecast(horizon=1)` and `res.forecast(horizon=90)` return an
identical first-step variance (the analytic recursion's `h.1` doesn't depend
on how many further steps are requested), and that `arch`'s analytic method
(no simulation needed) works fine for GJR-GARCH at horizon>1 with `dist='t'`.

## Test changes

Several existing tests encoded the OLD (broken) contract — a single
`precomputed_garch_annual_vol` scalar "short-circuiting" the GARCH estimator
for every horizon. That's no longer possible: a 1-day-ahead point estimate
cannot supply a genuine multi-day answer, so the fix necessarily changes
what a bare scalar does (it now falls through to a fresh multi-horizon fit
instead of being naively broadcast). Per the task's own explicit
instruction, these tests were rewritten against the new, correct contract
rather than preserved as-is:

- `TestPrecomputedGarchSigma` (test_forecasting_engine.py) — rewritten
  around the new `precomputed_garch_term_structure` dict param, plus a new
  test proving a bare legacy scalar no longer short-circuits.
- `TestFitOnceRefactor`, `TestForecastConeBands` — swapped
  `precomputed_garch_annual_vol=0.30` for an equivalent flat
  `precomputed_garch_term_structure` dict (these tests are about ARIMA/HW
  fit-counting and MC band widening, not GARCH behavior — they just needed
  to keep skipping the real fit for speed/determinism).
- `test_forecasting_improvements.py::TestGarchSigma::test_annualized_garch_vol_is_converted_to_daily_for_monte_carlo`
  — updated to patch the term-structure method (the one `generate_forecast`'s
  multi-horizon loop actually calls now), not the scalar one.
- Seven test files patching `engine.advisory.TechnicalOptionsEngine.estimate_gjr_garch_volatility`
  (`test_advisory.py`, `test_advisory_pause_gate.py`,
  `test_advisory_double_fetch_caching.py`, `test_advisory_dedup_wiring.py`,
  `test_rationale_verbosity.py`, `test_dead_letter_resilience.py`,
  `test_pipeline_smoke.py`) — added a companion
  `estimate_gjr_garch_volatility_term_structure.return_value = {h: X for h in (1,10,30,60,90)}`
  stub next to every existing scalar stub, since `engine/advisory.py` no
  longer calls the scalar method at all.

New tests add the actual regression coverage for the fix itself:

- `tests/test_technical_options_engine.py::TestGarchTermStructure` — proves
  horizon=1 is byte-identical to the pre-refactor scalar method; builds a
  synthetic post-shock series and asserts the per-horizon vol is
  monotonically decreasing and strictly below the flat sigma_1 broadcast
  (the actual mean-reversion regression check); confirms the ARCH-unavailable
  / insufficient-history fallback stays flat across every horizon; confirms
  exactly one `arch_model.fit()` call regardless of how many horizons are
  requested.
- `tests/test_forecasting_engine.py::TestGarchHorizonScaling` — end-to-end
  through `generate_forecast()`: with a hand-picked mean-reverting term
  structure, every captured `run_monte_carlo` sigma differs by horizon (the
  old bug shared ONE value) and matches `term_structure[h]/sqrt(252)`
  exactly — plus an explicit check that each horizon's genuine sigma sits
  below what the old naive `sigma_10*sqrt(T/10)` extrapolation would have
  produced, reproducing the bug's own overstatement direction.

## Verification

All targeted suites pass:
`test_forecasting_engine.py` (50), `test_technical_options_engine.py` (78),
`test_advisory.py` (53), `test_pipeline_smoke.py` (12),
`test_advisory_pause_gate.py`/`test_advisory_double_fetch_caching.py`/
`test_advisory_dedup_wiring.py`/`test_rationale_verbosity.py`/
`test_dead_letter_resilience.py` (111 combined),
`test_forecasting_improvements.py` (15), `test_state_snapshot_parity.py`,
`test_sf_garch_lstm.py`, `test_quantitative_models.py`,
`test_options_selling_backtest_stress.py`.

One combined-order failure (`test_technical_options_engine_indicators` under
`pytest-randomly` when run alongside `test_options_selling_backtest_stress.py`)
was investigated and confirmed to be a **pre-existing, unrelated** flake —
reproduced identically on `main` via `git stash` before any of this
branch's changes, and passes cleanly both in isolation and with
`-p no:randomly`. Not touched by this PR.
