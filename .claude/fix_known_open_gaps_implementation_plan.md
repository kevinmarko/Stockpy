# Goal Description
Fix remaining Master Session open gaps (Section 7), specifically the active trading universe disconnect (Gap 3) and the missing deployability gate enforcement for options pilot execution (Gap 2).

## Proposed Changes
- `forecasting/forecast_tracker.py`: Add `get_covered_symbols()` to fetch the real forecasted universe.
- `api/data_api.py` and `data/portfolio_sync.py`: Pipe `forecast_symbols` into `build_sync_report`.
- `api/pilots_api.py`: Add `override_deployability_gate` flag for `EarningsCrushExecuteRequest`, `DispersionExecuteRequest`, and `ZeroDteExecuteRequest`, forcing a fail-closed response for these `UNGATEABLE_DATA_GAP` strategies unless overridden.
- `tests/test_data_api.py`: Fix mocked `build_sync_report` lambdas to accept kwargs (for `forecast_symbols`).

## Verification Plan
Run `pytest tests/test_data_api.py tests/test_pilots_api.py`
