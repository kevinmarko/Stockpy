# freeze_status_quality_report - walkthrough

## What changed
`scripts/feature_freeze_status.py` gained an informational `quality` block (JSON key and a text section):
hold-time distribution of closed pipeline trades, close-reason counts, distinct entry dates and the largest
single-day clump, open positions with days held / mark / unrealized P&L / sector, and sector concentration by
cost basis. `--live-quotes` is an opt-in network marking mode.

## What did not change
The gate (`closed_pipeline_trades >= --min`), the exit codes (0 / 2) and every existing JSON key. The quality
block is computed in its own try/except; on failure it becomes `{"error": ...}` and the exit code is untouched.

## Data sources (all read-only, no network by default)
- Closed trades: `store.get_full_closed_trades` (`holding_period_days`, falling back to `exit_ts - entry_ts`).
- Open rows: `PaperPosition` via the read-only store session.
- Marks: latest `price_bars.close` per symbol through the read-only engine (not `get_open_positions`, which hits
  the network and falls back to cost basis; not `HistoricalStore.get_bars`, which tops up from the provider).
- Sector: latest `fundamentals_history.raw_json["sector"]`.
Unmeasurable values are `None` / `unavailable`, never 0 or cost basis (CONSTRAINT #4).

## Verification
See the PR description for the test, lint, `make ci` and live-run results.
