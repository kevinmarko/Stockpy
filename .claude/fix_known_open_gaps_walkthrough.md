# Walkthrough
- Modified `ForecastTracker` to query and return covered symbols.
- Updated `api/data_api.py` to use `ForecastTracker().get_covered_symbols(horizon_days=30)` and pass it into `build_sync_report`.
- Added `override_deployability_gate` field and condition to options execute endpoints in `api/pilots_api.py`, aligning with `vol_mispricing`.
- Verified all tests pass via `pytest tests/test_data_api.py tests/test_pilots_api.py`.
