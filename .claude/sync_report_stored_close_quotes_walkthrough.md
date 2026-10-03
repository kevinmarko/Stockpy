# Sync report prices from stored closes — walkthrough (2026-10-03)

**Problem.** After #1103 moved fundamentals to the store, the first `GET /data/sync-report` after a restart still took 23 s on the live stack. This FMP plan has no bulk quote endpoint (`/batch-quote` is 402), so the quote leg made one throttled `/quote` call per symbol (`FMP_MIN_REQUEST_INTERVAL_SECONDS=0.25`), plus one bars call each.

**Change.**
- `data/portfolio_sync.py`:
  - `build_sync_report(..., prices_from_store=False)` is a new opt-in.
  - When on, `_probe_symbol_coverage` gets a `price_lookup` built by `_stored_price_lookup()`, which reads the newest non-null `price_bars` close through a read-only `HistoricalStore`. It never calls a provider.
  - A stored close answers both the quote and bars legs. The row reports `is_stale_quote=True` and `quote_source="price_bars (stored close)"`, because it is not a live price.
  - Coverage is `stale` only when the bar is older than 2 business days (US/Eastern); otherwise `full`. Two days so the session after a holiday is not flagged.
  - No stored bar, a zero/NaN/unparseable close, or an unreadable store returns `None`, and that symbol falls back to the live probes. A price is never fabricated.
- `api/data_api.py`: `GET /data/sync-report` passes `prices_from_store=True`.
- `POST /data/sync`, the MCP and Gravity keep live probes.

**Verification.**
- `pytest tests/test_portfolio_sync.py tests/test_data_api.py`: 124 passed. New tests cover outdated-bar flagging, the Thanksgiving gap, zero/missing closes, live fallback for gaps, an unreadable store, and default mode staying live.
- Real database, fresh process: 27 symbols in 0.03 s, all `full`, all priced from stored closes.
