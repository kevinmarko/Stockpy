# Walkthrough: FMP `historical_eod` 5,000-row pagination fix

## What was broken

`data/fmp_client.py::historical_eod()` — the wrapper for FMP's
`/historical-price-eod/{variant}` endpoint — silently caps a single request's response at
exactly 5,000 rows and returns the **most recent** rows within the requested window, silently
dropping the **oldest** portion when the span exceeds the cap. No error, no truncation flag.

Live-confirmed: `historical_eod('SPY', variant='dividend-adjusted', from_date='2005-01-01',
to_date='2026-08-21')` returned exactly 5,000 rows spanning `2006-10-05`→`2026-08-21` —
silently dropping the requested `2005-01-01`→`2006-10-04` window (~22 months). A bounded
follow-up call for exactly that missing window returned the real 443 missing rows — the data
exists on FMP's side; this was purely a client-side fetch gap. `page`/`limit` query params are
silently ignored by this endpoint (confirmed live), unlike `stock_news()`, which genuinely
paginates via those params.

**Impact**: `scripts/refresh_validations.py`'s default `--start 2005-01-01` spans ~21.6 years —
past the cap. Every `STRATEGY_REGISTRY` strategy's backtest was silently running on a truncated
~19.8-year window, not the full 2005-present window the CLI default implies. This had already
produced one measured discrepancy: `timeseries_momentum`'s Sharpe had moved from a
previously-recorded 0.523 (`deployable=True`) to a freshly-measured 0.477 (`deployable=False`).

## The fix

Added `data/fmp_client.py::historical_eod_full_range(symbol, *, variant, from_date, to_date,
max_requests=10)` — a new function, not a modification to `historical_eod()` itself (every
other caller of `historical_eod()` expects single-request, non-looping semantics, matching this
codebase's established "pagination-looping is the caller's job" convention).

**Algorithm**: call `historical_eod()` once. If the response is empty, return `[]` immediately.
Otherwise track the earliest returned date. While that date is still later than the requested
`from_date` and the request budget isn't exhausted: issue a follow-up call bounded to
`[from_date, earliest_date - 1 day]`, merge (dedup by date, first-seen wins), and repeat. Stops
cleanly when `earliest <= from_date`; stops with a WARNING (returning the partial result, never
raising) when a follow-up is empty/exceptional/makes-no-progress (exhausted history, e.g. hit
the symbol's IPO) or when `max_requests` is exhausted. Termination is guaranteed independent of
`max_requests` — every iteration requires the merged earliest date to have strictly decreased,
or it stops.

`scripts/refresh_validations.py::_fetch_fmp_ohlcv_batch`'s `_fetch_one` is the one call site
migrated to it. Every other `historical_eod` call site was independently re-confirmed to already
use a window well under the cap and was left unchanged: `data/market_data.py`'s two callers
(bounded by `lookback_days`/`settings.BETA_LOOKBACK_DAYS`, both defaulting to 504 days),
`scripts/verify_fmp_bars.py` (2-year window), `ml/forecast_backfill.py` (4-year default
lookback via `settings.FORECAST_BACKFILL_LOOKBACK_YEARS`).

## A related gap found and closed in the same PR

The re-verification pass (see Process notes below) found `GET /data/bars/{symbol}`
(`api/data_api.py`) had **no upper bound** on its `lookback_days` query parameter, unlike its
two siblings (`/data/macro/{series_id}`, `/data/sentiment/history/{symbol}`), which already use
`Query(180, ge=1, le=3650)`. Not currently exercised (the shipped webapp caller only ever sends
21/63/120/126/252), but closed defensively to match the established sibling convention:
`lookback_days: int = Query(252, ge=1, le=3650)`.

## Live verification (real FMP data, not simulated)

```
historical_eod_full_range('SPY', from_date='2005-01-01', to_date='2026-08-21')
-> 5,443 rows, 2005-01-03 -> 2026-08-21   (was: 5,000 rows, 2006-10-05 -> 2026-08-21)
```

All 4 originally-affected strategies re-run through the real harness with the fixed fetch:

| Strategy | Before (truncated) | After (full range) | Verdict |
|---|---|---|---|
| `sortino_drawdown` | Sharpe 0.801, `deployable=True` | Sharpe 0.706, `deployable=True` | Unchanged conclusion |
| `timeseries_momentum` | Sharpe 0.477, `deployable=False` | **Sharpe 0.524, `deployable=True`** | **Recovers — confirms truncation artifact, not decay** |
| `vrp_premium_selling` | Sharpe 0.189, `deployable=False` | Sharpe 0.217, `deployable=False` | Unchanged conclusion |
| `vol_mispricing` | Sharpe -0.140, `deployable=False`, stress-gate FAIL | Sharpe -0.033, `deployable=False`, stress-gate FAIL (bit-identical OCT_2008 blow-up) | Unchanged conclusion — 5th independent confirmation |

`timeseries_momentum`'s recovery is the headline result: nothing about the gate or the adapter
changed between the two runs — only the input data did (443 more real trading days the fetch
was silently dropping). Per this repo's rule that gates are never loosened to force a pass, this
is a legitimate correction of a measurement artifact, not a loosened threshold.

## Test coverage

- `tests/test_fmp_client.py::TestHistoricalEodFullRange` — 11 tests: single-request-suffices,
  one/multiple rounds of truncation, exhausted-history stop, no-new-earlier-dates stop,
  `max_requests` hard cap, dedup with first-seen-wins, first-call-exception propagation,
  follow-up-exception partial-result-with-warning, sorted-output invariant. Full file: 63 passed.
- `tests/test_refresh_validations.py::TestFmpBackedDownloadFunctions` — all 16 pre-existing
  tests pass unmodified (their fixtures already reach `from_date` on the first call, so the
  pagination loop never engages — degrades to the exact prior single-call behavior); one new
  integration test, `test_fetch_fmp_ohlcv_batch_pages_past_the_five_thousand_row_cap`, driving a
  simulated truncation+follow-up through the real `_download_closes` path end-to-end. Full file:
  140 passed, 1 deselected.
- `tests/test_data_api.py::test_bars_lookback_days_is_bounded` — new, covering the related fix.
  Full file: 52 passed.
- Combined regression sweep (`tests/test_fmp_client.py`, `tests/test_refresh_validations.py`,
  `tests/test_data_api.py`, `tests/test_market_data.py`): 376 passed, 1 deselected, 0 failures.

## Process notes (for whoever reviews this)

This PR was built with the plan-then-execute workflow: `EnterPlanMode` → two research passes
(an Explore agent for test/doc conventions, a Plan agent for the concrete algorithm/test/doc
design) → user-approved plan → implementation. The implementation phase parallelized 6
background agents across independent, non-overlapping files (test-fixing for
`tests/test_fmp_client.py`, test-writing for `tests/test_refresh_validations.py`, three
documentation agents, and one read-only re-verification agent double-checking every "confirmed
not affected" call site claim) while the live-data verification (requiring careful, non-printed
credential handling) was kept under direct control rather than delegated. The re-verification
agent is what surfaced the `GET /data/bars/{symbol}` gap closed above — an example of the
parallel-agent pattern finding something a purely sequential pass might have missed.

One transient false alarm during the parallel phase: a live-verification script hit
`AttributeError: module 'data.fmp_client' has no attribute 'historical_eod_full_range'` — a
genuine race condition against a concurrent agent's in-flight edit to the same file, not data
loss (confirmed via `git status`/`git diff` immediately after). Resolved itself once that
agent's edit completed; no code was actually lost or needed recovery.
