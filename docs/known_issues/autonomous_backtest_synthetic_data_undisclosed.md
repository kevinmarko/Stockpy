# Autonomous Backtest Synthetic Data Undisclosed

**Date:** 2026-08-27
**Status:** Resolved

## Issue Summary
The `validation/autonomous_backtest_runner.py` autonomous backtest runner was falling back to generated synthetic OHLCV data when real historical data was unavailable or insufficient (length < 50). This synthetic data run was incorrectly allowed to report `is_deployable: true` if it passed the quantitative gates (PBO, DSR, Sharpe, MaxDD). This exposed a severe risk where paper-broker code could be deployed based entirely on a backtest run on hallucinated data.

## Root Cause
The fallback mechanism to `generate_synthetic_ohlcv` in `api/pilots_api.py` generated data and passed it to the runner without marking it as synthetic. The runner evaluated the performance over this fake data and, since it has no way to know it wasn't real market data, set `is_deployable = True` when the strategy accidentally performed well. The frontend also did not surface this state to the user, who would see a "🚀 DEPLOYABLE" badge and a "Deploy to Paper Broker" button.

## Resolution
1. **Runner Updates**: Added `data_source` and `is_synthetic_data` to `AutonomousBacktestResult`. In `run()`, we now check if `data_source != "real_historical_bars"`. If so, and the strategy would otherwise pass the gates, we force `is_deployable = False` with an explicit failure reason: `"NOT DEPLOYABLE: backtest ran on data_source='synthetic_demo_data' -- a synthetic-data run can never certify real-market deployability."`
2. **API Updates**: Threaded `data_source="real_historical_bars"` vs `"synthetic_demo_data"` from `pilots_api.py` into the runner.
3. **Frontend Updates**: Threaded the new fields through `webapp/src/api/types.ts` and `webapp/src/api/mock.ts`. Updated `ResearchCopilotView.tsx` to display a visible warning banner (`⚠️ SYNTHETIC DATA FALLBACK`) and to explicitly hide the "Deploy to Paper Broker" button when `is_synthetic_data` is true, ensuring operators cannot click deploy even if a bug in the backend incorrectly reported `is_deployable: true`.
