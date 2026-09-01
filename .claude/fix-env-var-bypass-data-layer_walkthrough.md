# Walkthrough

## Changes made
- **`data/market_data.py`**:
  - Replaced manual `os.environ.get()` bypasses with configuration reads sourced from `settings`:
    - `FUNDAMENTALS_CACHE_TTL_SECONDS`
    - `FUNDAMENTALS_NEG_CACHE_TTL_SECONDS`
    - `FINNHUB_RATE_LIMIT_PER_MIN`
  - Removed obsolete type casts (`int(...)`) as `settings` natively handles type coercion via pydantic.
  - Removed unused `import os`.
- **`data/market_data_ws.py`**:
  - Replaced `os.environ.get("WATCHLIST")` with `settings.WATCHLIST`.
  - Added `from settings import settings` at the top of the module.
  - Removed the inline `import os` statement since it was no longer needed.

## What was tested
- Validated `test_market_data.py` and `test_market_data_ws.py` suites to ensure backward compatibility and confirm environment variable mocks properly intercept the settings reads in a test context.

## Validation results
- All tests in `pytest tests/test_market_data.py tests/test_market_data_ws.py -q` passed cleanly without any functional regressions. 152 tests passed.
