# Sync report reads stored fundamentals — walkthrough (2026-10-03)

**Problem.** After #1101, `GET /data/sync-report` was fast only once warm. Following a daemon/API restart, the first call took about 95 s. The data API log showed the time going into live `provider.get_fundamentals()` calls, about 10–25 s per symbol (DIV, SDIV, SRET). The in-process fundamentals cache is empty after a restart.

**Change.**
- `data/portfolio_sync.py`:
  - `build_sync_report(..., fundamentals_from_store=False)` is a new opt-in.
  - When it is on, `_probe_symbol_coverage` gets a `fundamentals_lookup` built by `_stored_fundamentals_lookup()`. That lookup reads the newest `fundamentals_history` row through a read-only `HistoricalStore` and never calls a provider.
  - The rules:
    - A row with at least one real typed value, or a non-empty raw payload, counts as covered.
    - A missing row is `fundamentals:not_in_store`.
    - An all-NaN row is `fundamentals:empty`.
    - A store that will not open, or a row read that fails, is `fundamentals:store_unavailable`.
  - Nothing is ever assumed covered.
- `api/data_api.py`: `GET /data/sync-report` passes `fundamentals_from_store=True`.
- Other callers keep the live probe: `async_sync_now` (`POST /data/sync`), the MCP and Gravity.

**Verification.**
- `pytest tests/test_portfolio_sync.py tests/test_data_api.py tests/test_universe_retention.py tests/test_investyo_mcp_server.py tests/test_m3_explain_adversarial_stress.py tests/test_forecast_tracker.py`: 589 passed.
- New tests:
  - store mode makes zero provider fundamentals calls and classifies stored, missing and all-NaN rows honestly
  - an unreadable store degrades without a live fetch
  - default mode still probes live
  - the endpoint passes the flag
- Live, real database, fresh process: 27 symbols in 1.2 s (was about 95 s cold), and every symbol still has stored fundamentals.
