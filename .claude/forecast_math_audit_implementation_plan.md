# Forecast & LLM math audit — remediation plan

## §0 Dependency Check
- Live schema verified for `forecast_errors` (requires ADD COLUMN for interval bands if not already present).
- Module interfaces (`forecasting_engine.py`, `cnn_lstm_worker.py`, `forecasting/forecast_tracker.py`, `ml/options_meta_labeler.py`, `ml/transformer_vol_forecaster.py`, `technical_options_engine.py`, `pipeline/production_steps.py`) matched with current `main` base.
- Feature flags: `CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED`, `FORECAST_SKILL_WEIGHTING_ENABLED`, `SENTIMENT_LLM_VERIFICATION_ENABLED`.

## WP1 — Monte Carlo drift and determinism
- Fix run_monte_carlo drift by using mu * dt (no Ito double correction).
- Add FORECAST_MC_RANDOM_SEED (default 42).
- Raise simulations to 4000.
- Fix the annualisation guard.
- Add FORECAST_DRIFT_SHRINKAGE (default 0.0).
- Rewrite `tests/test_forecasting_engine.py`.

## WP2 — Purged split
- Fix `embargo = max(0, lookback - 1 + max_h)` in `forecasting_engine.py` and `cnn_lstm_worker.py`.
- Guard the degenerate case with a logged warning.
- Extend `tests/test_forecasting_lookahead.py::TestPurgedTrainValSplit` to assert max training label index < min validation input index.

## WP3 — Tracker horizon units and historic repair
- Update `update_actuals` to score against trading bars close.
- Add `scripts/repair_forecast_errors_horizon.py` to fix historical data.
- Add test-isolation fixture for `ForecastTracker` in root `conftest.py`.

## WP4 — Honest degradation
- `forecast_from_hw_fit` returns `nan` instead of last price on failure.
- `options_meta_labeler.py` returns `nan` on predict_proba exception.
- `options_meta_labeler.py` drops fabricated `acc`/`auc`.
- `options_meta_labeler.py` linear_fallback removes sigmoid or deleted entirely.
- `technical_options_engine.py` returns `None` for fabricated 0.20 vol.
- `pipeline/production_steps.py` returns NaN forecasts on no history.
- Fix 0.30 dead floor in `get_sizing_multiplier`.
- Wrap the meta-labeler in `CalibratedClassifierCV`.

## WP5 — Transformer volatility labels
- Drop `h=1` from HORIZONS.
- Use `ddof=1` for std target.
- Use `None` instead of fabricated band for quantile spread.
- Change `== 0` check to `< 1e-12` for degenerate range guard.

## WP6 — The measurement layer
- Register `naive` pseudo-model in `ForecastTracker`.
- Add `ForecastTracker.coverage_report`.
- Add proper scoring (pinball loss, CRPS).
- Make `tests/test_forecast_skill_uplift.py` assert instead of print.

## WP7 — Blend weighting
- Weight by `1/MSE`, not `1/RMSE`.
- Document independence assumption.
- Filter corrupt negative MSE.
- Renormalise the static blend in `forecasting_engine.py`.
- Use explicit business-day frequency for Prophet.
- Match MC percentiles to point forecast naming or label as MC-only.

## WP8 — Documentation and labels
- `docs/architecture/signal-engines.md`: remove the `(μ − ½σ²)` claim, correct the `CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED` default (`True`) and verification status.
- `docs/signals/forecast_alignment.md`: state that the static blend is the default and skill weighting is opt-in.
- `config.py`: `"MC 90% band"`.
- `pipeline/production_steps.py`: stop writing MC percentiles into Prophet-named columns.
- New `docs/known_issues/forecast_ito_double_correction_and_horizon_units.md`.
- `CLAUDE.md`: unconditional F1 fix and numeric effect.

## AGENT HANDOFF NOTES
- Documentation updates to `docs/architecture/signal-engines.md`, `docs/signals/forecast_alignment.md`, `config.py`, `pipeline/production_steps.py`, and `CLAUDE.md` must be completed.
- Create `docs/known_issues/forecast_ito_double_correction_and_horizon_units.md`.
