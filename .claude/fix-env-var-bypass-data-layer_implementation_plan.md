# Implementation Plan
Goal: Fix env-var bypass in data layer

## Proposed Changes
- `data/market_data.py`:
  - Change `os.environ.get("X", default)` to `settings.settings.X` for:
    - `FUNDAMENTALS_CACHE_TTL_SECONDS`
    - `FUNDAMENTALS_NEG_CACHE_TTL_SECONDS`
    - `FINNHUB_RATE_LIMIT_PER_MIN`
  - Remove `int(...)` casts since settings already casts them.
  - Remove unused `import os` if applicable.
- `data/market_data_ws.py`:
  - Change `os.environ.get("WATCHLIST")` to `settings.settings.WATCHLIST`.
  - Add `from settings import settings` at module top.

## Verification Plan
- Run `pytest tests/test_market_data.py tests/test_market_data_ws.py -q`.
