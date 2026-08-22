# Fix: FMP `historical_eod` silently caps history at 5,000 rows

## Context

Investigating why `sortino_drawdown`/`timeseries_momentum`/`vrp_premium_selling`/`vol_mispricing`
intermittently errored in `scripts/refresh_validations.py`, we confirmed (live, against real FMP
data) that `data/fmp_client.py::historical_eod()` — which wraps FMP's
`/historical-price-eod/{variant}` endpoint — silently caps a single request's response at exactly
5,000 rows and returns the MOST RECENT rows within the requested window, silently dropping the
OLDEST portion when the span exceeds the cap:

- `historical_eod('SPY', ..., from_date='2005-01-01', to_date='2026-08-21')` → exactly 5000 rows
  spanning `2006-10-05`→`2026-08-21` (drops the requested `2005-01-01`→`2006-10-04` window, ~22
  months).
- A bounded follow-up call for exactly that missing window returns the real data (443 rows,
  `2005-01-03`→`2006-10-04`) — the data exists on FMP's side; this is a client-side fetch gap.
- `page`/`limit` params are silently ignored by this endpoint (confirmed live) — unlike
  `stock_news()`, which genuinely paginates via those params.

**Affected**: `scripts/refresh_validations.py::_fetch_fmp_ohlcv_batch()` (used by
`_download_closes`/`_download_ohlcv`), whose default `--start 2005-01-01` spans ~21.6 years —
well past the cap. Every `STRATEGY_REGISTRY` strategy's backtest has therefore silently run on a
truncated ~19.8-year window, not the full 2005-present window the CLI default implies. This
already produced one measured discrepancy: `timeseries_momentum`'s Sharpe moved from a
previously-recorded 0.523 (`deployable=True`) to 0.477 (`deployable=False`) — plausibly (not yet
confirmed) a truncation artifact rather than genuine decay. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s "2026-08-21 (cont.)" entries and
`docs/signals/timeseries_momentum.md`'s "2026-08-21 re-verification" section, both of which
explicitly flagged this as a disclosed-but-unresolved follow-up.

**Confirmed NOT affected** (checked directly — do not touch): `data/market_data.py`'s two
`historical_eod` callers (`get_intraday_bars`'s `1d` path, `_fetch_daily_returns` for beta) —
both bounded by short `lookback_days`/`BETA_LOOKBACK_DAYS` (default 504) windows, far under the
cap. `scripts/verify_fmp_bars.py` — bounded to a 2-year window. `ml/forecast_backfill.py` —
default 4-year lookback (`FORECAST_BACKFILL_LOOKBACK_YEARS`); only at risk with a non-default,
explicitly-passed `start_date` >~19.8 years back, not exercised today.

Goal: fix the fetch gap for the one caller that needs long windows, confirm live whether
`timeseries_momentum`'s regression was a truncation artifact or genuine, and document honestly
either way.

## Approach

Add a new function rather than modifying `historical_eod()` itself — every other caller of
`historical_eod()` expects single-request, non-looping semantics (this codebase's established
"pagination-looping is the caller's job" convention, e.g. `stock_news`'s own docstring).

### 1. `data/fmp_client.py` — new `historical_eod_full_range()`

Insert immediately after `historical_eod` (`data/fmp_client.py:545-575`).

```python
def historical_eod_full_range(
    symbol: str,
    *,
    variant: str,
    from_date: str,
    to_date: str,
    max_requests: int = 10,
) -> List[Dict[str, Any]]:
```

`from_date`/`to_date` required (unlike `historical_eod`'s optional ones) — this function's whole
purpose is honoring a bounded window.

**Algorithm**: call `historical_eod()` once. If the response is empty, return `[]` immediately (no
follow-up — this is "nothing in range," not truncation; only the FIRST call's exception is left
to propagate unchanged, matching `historical_eod`'s own no-swallow contract). Otherwise, track the
earliest returned `date` string (ISO `"YYYY-MM-DD"` — lexicographic comparison equals chronological
order, worth a one-line comment). While that earliest date is still later than `from_date` and the
request budget (`max_requests`) isn't exhausted: issue a follow-up call bounded to
`[from_date, earliest_date - 1 day]`, merge into a `date -> row` dict via `setdefault` (dedup,
first-seen wins on any overlap), and update `earliest` to the new minimum. Stop when any of:

1. `earliest <= from_date` — clean, complete, no warning logged.
2. A follow-up call raises, returns empty, or returns rows containing no date earlier than what's
   already collected — logs a WARNING (naming the symbol, rows collected, and what's still
   missing) and returns the partial result. This covers "exhausted the symbol's real history
   (e.g. hit its IPO)" and "a degenerate/anomalous API response" identically — both are legitimate
   reasons to stop, and the caller is told via the log rather than a silent claim of completeness.
3. `max_requests` is exhausted — logs a WARNING naming the cap, returns the partial result.
   Default `10` is a safety valve (10 requests ≈ 198 years of coverage), not an expected budget —
   hitting it in practice would itself be a signal something's wrong.

Termination is guaranteed independent of `max_requests`: each iteration issues exactly one request
and then requires the merged earliest date to have strictly decreased, or it stops — never spins
indefinitely even on a pathological response. Returns the same raw list-of-dicts shape as
`historical_eod`, **sorted by `date` ascending** (a guarantee `historical_eod` itself doesn't make,
but this function must impose since it merges multiple pages).

Never raises due to truncation, never silently claims completeness it doesn't have — any caller
that must know for certain can check `result[0]["date"] <= from_date` itself.

Add `timedelta` to the existing `from datetime import ...` line if not already imported.

### 2. `scripts/refresh_validations.py` — one call-site change

In `_fetch_fmp_ohlcv_batch`'s `_fetch_one` (~line 1932-1934):

```python
# before
payload = fmp_client.historical_eod(ticker, variant=variant, from_date=start_date, to_date=end_date)
# after
payload = fmp_client.historical_eod_full_range(ticker, variant=variant, from_date=start_date, to_date=end_date)
```

Confirm via `grep -n "fmp_client.historical_eod(" scripts/refresh_validations.py` that this is the
only call site in the file before editing (both `_download_closes`/`_download_ohlcv` route through
`_fetch_fmp_ohlcv_batch`, neither calls `historical_eod` directly). Update the surrounding
docstring line that names `historical_eod` to name the new function instead.

No other production call site changes (per "confirmed NOT affected" above).

## Tests

**`tests/test_fmp_client.py`** — new class `TestHistoricalEodFullRange`, mocking at the
`historical_eod` boundary (`patch("data.fmp_client.historical_eod", side_effect=[...])` —
matches `tests/test_fmp_news.py::TestFMPNewsSource`'s pagination-test convention, not the
lower-level `requests.get` mocking `test_fmp_client.py` uses for testing `historical_eod` itself).
Cover: single-request-suffices (no follow-up issued), one round of truncation (follow-up's
`to_date` kwarg is exactly `earliest - 1 day`), multiple rounds (each follow-up's bound derives
from the prior round, not the original request), exhausted-history stop (empty follow-up → clean
stop + WARNING), no-new-dates stop (follow-up returns only already-seen dates → stop + WARNING,
not an infinite loop), `max_requests` hard cap (pathological always-truncated `side_effect` never
exceeds the configured call count), dedup on overlapping dates between pages, output always sorted
ascending regardless of input order, first-call exception propagates unchanged (matches
`historical_eod`'s contract), follow-up exception returns the partial first-page result with a
WARNING rather than discarding it.

**`tests/test_refresh_validations.py::TestFmpBackedDownloadFunctions`** — run the existing class
as-is after the call-site change and confirm every test still passes unmodified (its fixtures'
`from_date` already matches each payload's earliest date, so the truncation loop shouldn't trigger
for any of them — verify this holds rather than assuming it). Add one new integration test,
`test_fetch_fmp_ohlcv_batch_pages_past_the_five_thousand_row_cap`, driving a simulated
truncation+follow-up through the real `_fetch_fmp_ohlcv_batch`/`_download_closes` path (not just
the isolated function) to prove the two layers compose correctly.

## Documentation

- **`docs/architecture/data-layer.md`** — new bolded sub-clause inside the existing single giant
  FMP bullet (line 13), matching the style of its existing "Bars adjustment convention"/"Rate-limit
  budget" sub-clauses: names the cap, the fix function, and that only `refresh_validations.py` was
  migrated (every other call site's window is already bounded).
- **`docs/FMP_INTEGRATION.md`** — add to the "Known risks → Could silently corrupt results" bucket
  (~line 170-197), and add a caveat to the "EOD charts (confirmed back to 2008)" line (~line 24)
  noting that's per-request, not a single-call guarantee.
- **`docs/architecture/validation-and-signals.md`** — one-sentence addition to the
  `scripts/refresh_validations.py` bullet's existing "Self-diagnosing empty feature/return frame"
  entry, cross-referencing that the underlying data gap it was diagnosing is now fixed.
- **`docs/VALIDATION_STRATEGY_FIX_LOG.md`** — new dated entry closing the "2026-08-21 (cont.)"
  follow-up's disclosed gap: what was fixed, the live-verified row count/date range before vs.
  after, and `timeseries_momentum`'s real re-measured Sharpe/PBO/DSR/MaxDD/`deployable` — filled
  in with **actual** numbers from the verification run below, never guessed ahead of time. States
  plainly that every other `STRATEGY_REGISTRY` entry was silently validated on the same truncated
  window until now, and flags a full-registry re-validation as an explicit, separate follow-up
  (out of scope for this PR).
- **`docs/signals/timeseries_momentum.md`** — new dated section resolving the "two open questions"
  the 2026-08-21 entry left open, with the real before/after table and an honest verdict (genuine
  decay vs. truncation artifact) based on whichever the re-run actually shows.

## Verification (must actually run, not simulated)

1. `pytest tests/test_fmp_client.py -k HistoricalEodFullRange -v` and
   `pytest tests/test_refresh_validations.py -k TestFmpBackedDownloadFunctions -v` — both green,
   offline, before spending any live API budget.
2. Live: `fmp_client.historical_eod_full_range("SPY", variant="dividend-adjusted", from_date="2005-01-01", to_date="2026-08-21")`
   → expect `len(rows) > 5000`, earliest date at/near `2005-01-01`.
3. Live: `_download_closes(["SPY"], "2005-01-01", "2026-08-21")` → same expectation, through the
   actual caller.
4. Live: `python -m scripts.refresh_validations --strategies timeseries_momentum --start 2005-01-01`
   → record the real Sharpe/PBO/DSR/MaxDD/`deployable`, compare against the truncated-window
   numbers already on record (Sharpe 0.477, `deployable=False`).
5. Write the real step-4 numbers into the two doc entries above — whatever they actually are.
6. Run the full offline test suite for touched files
   (`pytest tests/test_fmp_client.py tests/test_refresh_validations.py -q -m "not network and not slow"`)
   to confirm no regression elsewhere.
7. `ruff check` the two changed source files, consistent with prior PRs in this session.

## Branch & PR

New branch off `origin/main` (this is validation/data-layer territory, not stackable on the
already-open, unrelated #850): `fmp-historical-eod-pagination-fix`. Per this repo's PR-artifact
convention, copy this plan into `.claude/fmp_historical_eod_pagination_fix_implementation_plan.md`
on that branch (and a matching walkthrough) before opening the PR.

## Critical files

- `data/fmp_client.py`
- `scripts/refresh_validations.py`
- `tests/test_fmp_client.py`
- `tests/test_refresh_validations.py`
- `docs/architecture/data-layer.md`
- `docs/FMP_INTEGRATION.md`
- `docs/architecture/validation-and-signals.md`
- `docs/VALIDATION_STRATEGY_FIX_LOG.md`
- `docs/signals/timeseries_momentum.md`
