# Task Tracker: forecast-fallback disclosure

- [x] Fetch `origin/main` fresh; read `_blend_with_skill()`/`generate_forecast()` in full.
- [x] Read the existing `test_no_models_returns_current_price_never_zero` test and its
      surrounding class for the original design reasoning.
- [x] Grep every caller of `generate_forecast()` (`pipeline/production_steps.py`,
      `engine/advisory.py`, `api/metrics_api.py`) and trace how the result is consumed.
- [x] Determine whether `Forecast_30` is distinguishable from a fallback downstream — it is
      not, anywhere.
- [x] Determine how narrow the all-models-failed scenario actually is (Monte Carlo's
      near-universal success given a positive `current_price`) and reproduce it directly in a
      test.
- [x] Determine what changing the value to `NaN` would actually do at both real consumer
      sites (`.fillna(0.0)` in the vectorized path; raw `NaN` passthrough in the per-ticker
      path) — confirmed `NaN` would be strictly worse.
- [x] Decision: keep `current_price` (path a); add a disclosure flag.
- [x] Implement `Forecast_{10,30,60,90}_Is_Fallback` in `forecasting_engine.py::generate_forecast()`.
- [x] Wire into `pipeline/production_steps.py::ForecastingStep` (forecast_cols + the exception
      recovery branch).
- [x] Wire into `engine/advisory.py::evaluate()` (`key_indicators["forecast_is_fallback"]` +
      `precomputed_forecast_is_fallback` kwarg) and its `_eval_one` caller in
      `pipeline/production_steps.py`.
- [x] Wire into both `state_snapshot.json` writers (`reporting/state_snapshot.py`,
      `main_orchestrator.py::_write_state_snapshot`).
- [x] Tests: new engine-level tests, new `tests/test_forecasting_step_fallback_disclosure.py`,
      extended `tests/test_advisory.py`, `tests/test_advisory_dedup_wiring.py`,
      `tests/test_state_snapshot_parity.py`.
- [x] Docs: `docs/known_issues/forecast_fallback_current_price_disclosure.md` (new),
      `docs/known_issues/README.md` index row, `docs/architecture/signal-engines.md` addendum.
- [x] Lint: `ruff check . --select=F821,F822,F823,E9` clean.
- [x] Full offline test suite (`pytest -m "not network and not slow"`) — run and recorded in
      the walkthrough.
- [x] Push branch + open PR: https://github.com/kevinmarko/Stockpy/pull/1003 (left open for
      review — this is a judgment call, not merged by the agent).
