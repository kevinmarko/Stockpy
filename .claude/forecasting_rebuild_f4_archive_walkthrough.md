# Walkthrough: Forecasting Rebuild F4 Part 1

This PR executes the first half of the F4 step in the forecasting rebuild plan, achieving the structural cleanup and archival without altering live model behavior.

## What was Archived
All files were moved to `legacy/` verbatim, keeping git history intact. Nothing active imports from them.
*   **BERT-LLA**: The `forecasting/bert_lla.py` model, its ablation rows, and its tests. The live `forecast_errors` ledger never had a single row for `bert_lla`, `lstm_attention`, or `lstm_baseline`, so this removes dead weight.
*   **Attention Diagnostic**: `pilots/lstm_diagnostic.py` and its API endpoints, which were tightly coupled to the NLP model.
*   **ASVI Feature Engineering**: `ml/asvi_feature_engineering.py`, unused since the ML pipeline detachment.
*   **Drift Shrinkage**: The `FORECAST_DRIFT_SHRINKAGE` setting (never enabled live, default 0.0) was removed from `settings.py` and `run_monte_carlo`.
*   **Sector Config Loader**: The `forecasting/sector_configs.json` dynamic loader, backtest script, and related validation files.

## Frozen Inputs & D1=(b) Decision
The `forecasting/sector_configs.json` file is now the frozen dictionary `SECTOR_FORECAST_CONFIGS_FROZEN` inside `forecasting_engine.py`.
*   **Zero Moves**: A frozen-input diff script ran `generate_forecast` against the latest 63 symbols in `state_snapshot.json` using the baseline `origin/main` engine and this branch's engine. The results are byte-identical.
*   **CWD Independence**: The previous JSON loader incorrectly resolved the file relative to the process CWD, rather than the module directory. Freezing it as code removes this vulnerability entirely.
*   **Remaining F4 parts**: The `_blend_with_skill` preference logic and `cnn_lstm_worker.py::fit_predict_lstm_attention` remain active, pending the F3 naive gate flip (F4 part 2).

## API & Webapp Changes
*   Removed `attention` from `api/metrics_api.py` (`GET /metrics/forecast/{symbol}`) and `webapp/src/api/types.ts`.
*   Removed `AttentionHeatmapStrip` from the Pilots PWA.
*   Retired the 5 `BERT_LLA_*` and 2 `SECTOR_FORECAST_CONFIG*` settings from `api/pilots_api.py` and the `GenericSettingsEditor` GUI.
*   **Coverage Note**: Deleting the `SECTOR_FORECAST_CONFIGS` fixture meant losing the only test case for a `dict`-shaped JSON PUT in `test_pilots_api_tunables.py`. The remaining fields (like `CORS_ALLOWED_ORIGINS`) are lists.

## Stale `.env` Keys
The operator can safely delete the following keys from their live `.env` file, as they are now ignored via pydantic's `extra="ignore"`:
*   `BERT_LLA_ENABLED`
*   `BERT_LLA_WINDOW_SIZE`
*   `BERT_LLA_MIN_SENTIMENT_COVERAGE`
*   `BERT_LLA_BLEND_ENABLED`
*   `BERT_LLA_ABLATION_ENABLED`
*   `SECTOR_FORECAST_CONFIG_PATH`
*   `SECTOR_FORECAST_CONFIGS`

## Verification
*   Archive import smoke test: added `tests/test_forecasting_f4_archive_import_smoke.py` verifying that all 12 production entry points import cleanly when the 8 archived forecasting modules are poisoned via `sys.modules[name]=None`, and that none of the 8 archived modules are importable from their old paths (mutation-verified).
*   Targeted tests: zero failures.
*   Webapp tests: clean typecheck, zero vitest failures.
*   Webapp UI: verified no console errors on `/forecast` and `/symbol/AAPL`.
*   Settings census: 392 fields remain.
