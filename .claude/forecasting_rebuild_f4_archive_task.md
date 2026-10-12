# Task: Forecasting Rebuild F4 Part 1 (Archive)

**Objective**: Archive the BERT-LLA forecaster, the LSTM-attention endpoint, the sector static-preference blend artifact loader (`SECTOR_FORECAST_CONFIGS`), and the `FORECAST_DRIFT_SHRINKAGE` setting to `legacy/`.

## Changes
- **Archive**: Moved the `forecasting/bert_lla.py`, `pilots/lstm_diagnostic.py`, `ml/asvi_feature_engineering.py`, and `scripts/backtest_sector_configs.py` code and tests to `legacy/`.
- **Constants**: The dynamic `forecasting/sector_configs.json` was converted into the in-code frozen constant `SECTOR_FORECAST_CONFIGS_FROZEN` in `forecasting_engine.py`. This guarantees byte-identical behavior while dropping CWD dependency and JSON I/O.
- **Webapp/GUI**: Removed `attention` from the forecast responses and UI heatmap, and retired the `BERT_LLA_*` and `SECTOR_FORECAST_CONFIG*` tunables.
- **Documentation**: Updated `AGENTS.md`, `CLAUDE.md`, `docs/architecture/signal-engines.md`, `docs/known_issues/forecast_ito_double_correction_and_horizon_units.md`, and `docs/FEATURE_TIER_HISTORY.md` to reflect the archival.

## Verification
- Target tests pass: `test_forecasting_engine`, `test_forecast_rebuild_f2`, `test_forecast_rebuild_f3`, `test_forecast_tracker`, `test_metrics_api`, `test_pilots_api_tunables`, `test_pilots_strategy_matrix`, `test_sector_descriptions`, `test_build_ticker_sector_map`, `test_settings_keysets`, `test_settings_liveness`, `test_measure_settings_census`, and env/docs sync tests.
- `make ci` and `verify-webapp` passed.
- **Frozen-Input Diff**: Re-running the forecast pipeline on the latest `state_snapshot.json` produced exactly **zero moves** against the `origin/main` engine.
