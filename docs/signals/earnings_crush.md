# Earnings Volatility Crush Scanner (`pilots/earnings_crush.py`)

## Rationale

Compares straddle-implied expected move (`0.80 × IV_ATM × √T`) against the historical median
realized earnings-day price gap for a given ticker. When implied IV overstates the historically
realized move by enough margin (`edge_ratio ≥ MIN_EDGE_RATIO`, default 1.25), the scanner
recommends a delta-neutral Iron Condor to capture the post-earnings IV crush.

## Backtest Validation — NOT GATEABLE (measured reason)

**Not registered in `STRATEGY_REGISTRY`.** Following the precedent already documented in
`pilots/catalog.py` (an entry explicitly carrying `validation_strategy_id=None` — "does NOT
unblock a backtest today") — this pilot's alpha term cannot be honestly measured with any data
reachable in this repository, and registering a proxy would produce plausible-looking numbers
that measure the proxy, not the pilot.

**The blocking gap**: `edge_ratio`'s numerator is single-name, pre-earnings, ATM implied
volatility. No historical single-name IV series exists anywhere in this codebase — this repo's
`iv_history` table (`volatility/iv_engine.py`) has only 660 rows, all dated 2026-08-12, with
degenerate (0.0001) values (forward-accumulating only from whenever the live pipeline first
records a real reading); `data/fmp_client.py` has no options endpoints on the current plan tier;
`YFinanceOptionsProvider`/`CompositeOptionsProvider` serve a live chain snapshot only, never
history.

**Measured, not asserted**: ran the pilot's own `calculate_expected_earnings_move` /
`get_historical_earnings_moves` against the real local `earnings_events` database (45,147 real
FMP-sourced rows, 1985–2027) and real price history, substituting a trailing-realized-vol proxy
for the missing pre-earnings IV, across 10 mega-cap tickers × DTE ∈ {3, 5, 7}:

- The gate (`edge_ratio ≥ 1.25`) fired on 14 of 30 cases — **but for the wrong reason**: 8 of the
  10 test symbols returned `median_gap == FALLBACK_MEDIAN_MOVE_PCT` (the pilot's own hardcoded
  fallback constant, 5.20%), meaning `hist["fallback"] is True` and the pilot's own code already
  sets `is_recommended = False` for those cases — the gate opening was an artifact of insufficient
  real earnings-gap history per symbol, not a genuine edge reading.
- The IV level needed to reach `edge_ratio = 1.25` at DTE=5 against a 5% median gap is **66.8%
  annualized**. A real single-name pre-earnings ATM IV for a mega-cap is routinely 50–80%; a
  realized-vol/GARCH forecast (the only proxy available) gives 25–40%. Any number this backtest
  reported would be a direct function of which proxy was chosen, not of market information.

**Forward unblock path**: `iv_history` already accumulates forward from live pipeline runs.
Roughly 252 trading days (one year) of real recorded single-name ATM IV would make this pilot
genuinely gateable without any proxy.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) and
`.claude/giant_master_plan_audit.md`'s finding F4.
