# `dividend-income` scan config filter fix — walkthrough

## Problem

`pilots/scan_config_store.py`'s seeded default `dividend-income` scan row used filters
(`dividend_yield_min: 0.03`, `payout_ratio_max: 0.6`) that don't correspond to anything in
Robinhood's scanner API. Confirmed live against `get_scanner_filter_specs` on the connected
Robinhood Trading MCP: the FUNDAMENTAL filter group supports `FILTER_TYPE_EPS`,
`FILTER_TYPE_EARNINGS_DATE`, `FILTER_TYPE_EX_DIVIDEND_DATE`, `FILTER_TYPE_SHARES_FLOAT`,
`FILTER_TYPE_FORWARD_PE`, `FILTER_TYPE_GROSS_MARGIN`, `FILTER_TYPE_MARKET_CAP`,
`FILTER_TYPE_NET_PROFIT_MARGIN`, `FILTER_TYPE_OPERATING_MARGIN`, `FILTER_TYPE_PE`,
`FILTER_TYPE_PEG`, `FILTER_TYPE_RETURN_ON_ASSETS`, `FILTER_TYPE_RETURN_ON_EQUITY`,
`FILTER_TYPE_SECTOR`, `FILTER_TYPE_SHARES_OUTSTANDING` — no dividend-yield or payout-ratio
filter type exists at all. This row has been silently unrunnable as configured since it was
first seeded.

Running the `agentic-discovery` skill for this scan required a broad, unfiltered market-cap>$2B
scan followed by post-filtering through the platform's own `fundamentals_history` cache — which
only covers the platform's own tracked universe (68 of ~400 hits had any data at all), yielding
exactly one confirmed candidate (`T`/AT&T). That's a workable one-off, but leaves the stored
config itself still broken for the next run.

## Fix

Changed the seeded default filters to a **sector-tilt proxy** expressible in Robinhood's real
filter vocabulary — `FILTER_TYPE_SECTOR ANY_OF [Utilities, Real Estate, Energy, Financial
Services]` + `FILTER_TYPE_MARKET_CAP > $1B` — using the store's existing plain-key convention
(`sector`, `market_cap_min`, matching e.g. `multifactor`'s `min_market_cap`/`roe_min`) rather than
raw `FILTER_TYPE_*` enum strings; the `agentic-discovery` skill is what translates these into real
scanner filter objects at scan time, same as it already does for every other seeded row.

Added an inline comment above the entry documenting *why* this is a proxy rather than a real
yield/payout screen, so a future reader doesn't reintroduce the original unrunnable filters.

The live, already-running operator instance's `~/.stockpy_local/output/scan_configs.json` was
separately updated in-place via `ScanConfigStore().upsert("dividend-income", ...)` (not part of
this PR's diff — that file isn't git-tracked; it lives under `settings.LOCAL_DATA_ROOT`, outside
every git checkout by design) so the fix took effect immediately without waiting on this PR to
merge.

## Scope check

- `tests/test_pilots_scan_config_store.py` only asserts on `name` set membership, row count
  (10), and `enabled` — never on filter *values* — so no test changes were needed. Confirmed by
  running the full file: 19 passed.
- No other doc references the literal `dividend_yield_min`/`payout_ratio_max` keys or describes
  this scan's semantics. The only other `dividend-income` hits in the repo are an **unrelated**
  namespace collision: `pilots/catalog.py`'s `Pilot(id="dividend-income", ...)` — the separate
  "Dividend Income" copyable-strategy Pilot backed by `signals/dividend_quality.py` and a real SEC
  EDGAR PIT backtest. That's a different subsystem with no code path joining it to this scan
  config; this PR does not touch it.
- The webapp's `AgenticTrading.tsx` scan-config-creation modal and `ScanConfigCard.tsx` both
  handle `filters` opaquely (no per-scan-name special-casing), so no webapp changes were needed
  either.

## Verification

```
/Users/kevinlee/Stockpy-live/.venv/bin/python3 -m pytest tests/test_pilots_scan_config_store.py -q
# 19 passed
```
