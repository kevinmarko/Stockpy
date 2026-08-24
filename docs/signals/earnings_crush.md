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

`POST /pilots/options/earnings-crush/execute`'s response body now includes a `gate_status` field
(sourced from `OPTIONS_DESK_DEPLOYABILITY_GATES["earnings_crush"]` in `api/pilots_api.py`) —
`"UNGATEABLE_DATA_GAP"` — echoing this doc's `deployable=False` verdict inline on every execution
attempt, so an operator hitting the live endpoint sees the same honest gate status documented
here without cross-referencing this file.

## Defects found while analysing this pilot

1. **FIXED (2026-08-24) — the BMO/AMC bar-alignment blind spot.**
   `get_historical_earnings_moves()` always computed the realized post-earnings gap as
   `|Open[event_date] - Close[event_date-1]|` — correct only when a company reports
   **before market open (BMO)**. For an **after-market-close (AMC)** report — the majority
   case for large-cap tech (NVDA/AAPL/MSFT/META/GOOGL/AMZN, this module's own default
   universe) — the real reaction shows up one day later, as `Open[event_date+1] -
   Close[event_date]`, which the old code never looked at. Reproduced with a synthetic
   14.66% AMC overnight reaction: the function reported `median_move_pct ≈ 0.0`. Since
   `crush_edge_ratio = expected_move_pct / realized_move_pct`, understating the realized-move
   denominator inflates the edge ratio and can flip `is_recommended=True` for a candidate
   with no genuine edge — which then fires a real `dispatch_earnings_crush_alert`. The
   identical root cause let `evaluate_earnings_crush_candidates`'s front-week expiration
   picker choose an expiration dated exactly `event_date`, which for an AMC reporter expires
   *before* the reaction happens.

   Verified (via FMP's own published API docs) that FMP's `/earnings` calendar — this
   codebase's sole earnings-events source — carries no reporting-time/session field
   (`symbol`, `date`, `epsActual`, `epsEstimated`, `revenueActual`, `revenueEstimated`,
   `lastUpdated` only), so there is no real BMO/AMC label available to thread through
   instead. Fixed by computing BOTH candidate gaps from real bar data — the existing BMO
   hypothesis and a new AMC hypothesis (`|Open[event_date+1] - Close[event_date]| /
   Close[event_date]`) — and taking whichever is larger (a genuine reaction dominates
   ordinary single-day noise; this is also the conservative direction for the bug, since it
   can only increase the realized-move denominator, never decrease it further). Each
   quarter's `moves` entry now carries `reaction_session_inferred: "bmo" | "amc"` —
   explicitly labeled *inferred*, not source-confirmed — and the function's top-level return
   dict carries `timing_data_available: False` (self-documenting; forward-compatible if a
   real timing field is ever added). The expiration picker now requires `ed > event_date`
   (was `ed >= event_date`), so the chosen expiration always clears the earnings date
   entirely regardless of session.

   Regression-tested by
   `tests/test_earnings_crush.py::TestHistoricalEarningsMoves::test_amc_reaction_captured_via_next_day_open`
   (the direct AMC reproduction), `::test_bmo_reaction_still_captured_correctly` (proves no
   regression to the common case), and
   `TestEvaluateEarningsCrushCandidates::test_same_day_expiration_rejected_in_favor_of_later_one`.
   Full incident write-up:
   [`docs/known_issues/earnings_crush_bmo_amc_bar_alignment.md`](../known_issues/earnings_crush_bmo_amc_bar_alignment.md).
