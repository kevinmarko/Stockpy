# Fix GARCH horizon=1 hardcoding — Task Tracker

- [x] Reproduce/validate the bug against the installed `arch` library (synthetic shocked series; naive `sigma_1*sqrt(T)` vs. genuine multi-step cumulative variance).
- [x] `technical_options_engine.py`: add `estimate_gjr_garch_volatility_term_structure`; refactor `estimate_gjr_garch_volatility` to delegate (byte-identical horizon=1).
- [x] `forecasting_engine.py`: add `_estimate_daily_sigma_multi_horizon`; wire `generate_forecast`'s two `run_monte_carlo` call sites to per-horizon sigma; add `precomputed_garch_term_structure` param.
- [x] `pipeline/production_steps.py`: `OptionsAnalysisStep._options_one` fits the full term structure once, threads it via `ctx.context_extras["garch_term_structures"]`; `ForecastingStep._forecast_one` consumes it.
- [x] `engine/advisory.py`: Step 5 fits the full term structure once, threads it into Step 6's `generate_forecast` call.
- [x] New tests: `TestGarchTermStructure` (technical_options_engine), `TestGarchHorizonScaling` (forecasting_engine).
- [x] Updated tests that encoded the old (broken) single-scalar-short-circuits-everything contract: `TestPrecomputedGarchSigma`, `TestFitOnceRefactor`, `TestForecastConeBands`, `TestGarchSigma::test_annualized_garch_vol_is_converted_to_daily_for_monte_carlo`.
- [x] Updated every advisory-path test mocking `TechnicalOptionsEngine.estimate_gjr_garch_volatility` to also stub the term-structure method (7 test files).
- [x] Verified `_estimate_daily_sigma` (singular) and its Gravity AI Review Suite check are untouched.
- [x] Ran full targeted test sweep — all green except one confirmed pre-existing, unrelated flaky test (`test_technical_options_engine_indicators` under `pytest-randomly` ordering with `test_options_selling_backtest_stress.py`), reproduced on `main` via `git stash`.
- [x] Updated `docs/architecture/signal-engines.md`.
- [x] Committed PR artifacts under `.claude/fix_garch_horizon_scaling_*.md`.
- [ ] Open PR against `main`.
