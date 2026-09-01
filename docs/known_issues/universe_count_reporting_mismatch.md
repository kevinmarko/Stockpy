# "430-symbol active universe" vs "26 symbols actually forecasted"

**Status: root cause found and fixed for the confirmable half; a secondary,
plausible contributor is documented but not confirmed, and permanent
diagnostic instrumentation now exists so a repeat can be diagnosed in minutes
instead of re-investigated from scratch.**

## The original report

An operator reported that the Universe Manager screen (`GET /data/universe`,
backed by `settings.DEFAULT_TICKERS`) showed a ~430-symbol "active universe,"
while the daemon's actual per-cycle output only ever forecasted ~26 symbols.
The natural reading — "the pipeline is silently dropping 94% of my universe
every cycle" — is not what is actually happening, and this document exists so
the next investigation starts from a confirmed baseline instead of repeating
this one.

## What was checked, in the real per-cycle order

The real production path is `main_orchestrator.py` →
`pipeline/production_steps.py::AsyncDataFetchStep` → `ProcessingStep` →
`ForecastingStep`. Each stage was read end to end and evaluated for whether it
could plausibly narrow ~430 symbols down to ~26 (a ~94% drop) in a real run,
versus only occasionally dropping a handful:

1. **`data.portfolio_sync.compute_tracked_universe()`** (used by BOTH
   `main.py::_build_universe()` and `AsyncDataFetchStep`) computes
   `held ∪ watchlist ∪ discovered`, falling back to `default_tickers`
   (`settings.DEFAULT_TICKERS`) **only when that whole union is empty** — a
   deliberate, documented, tested design (see CLAUDE.md's "Daemon
   universe-divergence fix" bullet, and
   `tests/test_portfolio_sync.py::TestComputeTrackedUniverse::test_default_tickers_used_only_when_union_empty`).
   **This is a plausible, precisely-matching, and now CONFIRMED mechanism**:
   see "Confirmed root cause" below.

2. **`main_orchestrator.fetch_all_data_async()`**'s per-sub-fetch
   `asyncio.wait_for(..., timeout=DATA_FETCH_TASK_TIMEOUT_SECONDS)` wraps the
   ENTIRE macro/fundamentals/technical-bars fetch for the whole universe in
   one call each. On a timeout, `asyncio.wait_for` cancels the *awaiting*
   coroutine while the underlying `asyncio.to_thread` OS thread keeps running
   to completion in the background (Python threads cannot be forcibly
   interrupted) — the caller dead-letters that WHOLE sub-fetch to `{}`. This
   mechanism can only produce a clean **zero**, never a partial 26. **Ruled
   out** as the explanation for a nonzero-but-narrowed count.

3. **`DataEngine.fetch_technical_raw_cached()` → `HistoricalStore.get_bars_bulk()`**
   isolates each symbol's fetch in its own try/except (`ThreadPoolExecutor`,
   `settings.DATA_FETCH_MAX_CONCURRENCY` workers) and returns whatever subset
   succeeded — a genuine per-symbol narrowing point. A live FMP/yfinance rate
   limit or circuit breaker tripping mid-batch could organically leave only
   the first N symbols with real data. **Plausible but NOT confirmed or
   ruled out** — reproducing it needs live network access and a real
   provider outage/rate-limit event, neither available in this sandbox. This
   is the "secondary, independently plausible narrowing point" the
   permanent instrumentation below is aimed at catching if it recurs.

4. **`processing_engine.compile_dashboard()`** takes
   `set(tech_data.keys()) | set(fund_data.keys())` — a **union**, not an
   intersection. This step can only ADD rows relative to either input
   individually; it narrows the tracked universe only if a symbol is dropped
   from BOTH `tech_raw` AND `fund_raw` in the same cycle. Not a meaningful
   narrowing point on its own.

5. **`ForecastingStep._forecast_one`** skips a row (returns `None`, so it
   never enters `forecast_results`) only when that row's `Price` is
   falsy/zero. This determines which rows **already in `dashboard_df`** get a
   real vs. fallback-Monte-Carlo forecast — it does not remove rows from
   `dashboard_df` itself, and does not change how many symbols made it into
   the universe in the first place. Not a bulk-narrowing mechanism by itself
   (a data outage would show up as many symbols with `Price == 0`, which
   *would* trip this, but that is downstream of stage 3 above, not a separate
   cause).

## Confirmed root cause (the reporting half)

Reproduced directly against the real functions with a synthetic 430/26 split:

```python
from data.portfolio_sync import compute_tracked_universe

default_430 = [f"SYM{i}" for i in range(430)]
watchlist_26 = [f"WL{i}" for i in range(26)]

compute_tracked_universe(watchlist=watchlist_26, default_tickers=default_430)
# -> 26 symbols (the watchlist) -- DEFAULT_TICKERS is NEVER consulted,
#    because the held ∪ watchlist ∪ discovered union is already non-empty.

len(default_430)  # 430 -- exactly what GET /data/universe used to report
```

This reproduces the operator's exact numbers precisely. **Nothing was ever
"losing" symbols.** `GET /data/universe`'s `count` field
(`len(settings.DEFAULT_TICKERS)`) and `compute_tracked_universe()`'s real
per-cycle output were always answering two different questions — "how many
tickers are configured as the fallback list" vs. "how many tickers does the
daemon actually evaluate this cycle" — and nothing surfaced that they could
diverge. An operator who widened `DEFAULT_TICKERS` to ~430 (e.g. an
S&P-widening exercise) while also running a narrow `watchlist.txt`/`WATCHLIST`
env var of ~26 symbols would see exactly this split, every cycle, with no
error and no warning anywhere.

The webapp's own `SettingsUniverse.tsx` screen made this worse by actively
asserting the wrong thing: "Manage the active symbols that the pipeline
processes on each run" / "Changes take effect on the next pipeline run" — both
false whenever `watchlist`/`discovered` are non-empty, which per
`compute_tracked_universe()`'s fallback-only design is the common case for any
operator who also uses a watchlist.

## Fix shipped

1. **`GET /data/universe`** (`api/data_api.py`) now also returns
   `effective_symbols` / `effective_count` (a cheap, network-free preview of
   `compute_tracked_universe()`'s real fallback decision, using
   `load_env_watchlist()` and the cached `pilots.discovery.discovery()`
   candidates — no live broker/provider call), `default_tickers_is_fallback`
   (whether DEFAULT_TICKERS is actually the effective universe right now),
   and a plain-English `note`. `symbols`/`count` (DEFAULT_TICKERS itself) are
   unchanged for backward compatibility.
   - **Disclosed limitation**: held Robinhood positions are NOT included in
     this cheap check (this endpoint deliberately never touches the broker),
     so `default_tickers_is_fallback: true` is a conservative "very likely"
     signal, not an absolute guarantee — held positions could still be
     unioned in at run time without suppressing the endpoint's own fallback
     read. `default_tickers_is_fallback: false`, however, is unconditionally
     correct: watchlist/discovery being non-empty always means DEFAULT_TICKERS
     is not consulted, regardless of held positions.
2. **`webapp/src/screens/SettingsUniverse.tsx`** copy was corrected to state
   plainly that this list is a fallback, not "the active symbols the pipeline
   processes."
3. **`webapp/src/components/UniverseManager.tsx`** now renders a visible
   warning `Notice` when `default_tickers_is_fallback` is `false`, quoting
   the backend's `note`.
4. Regression tests: `tests/test_data_api.py` (backend, including the exact
   430/26 reproduction), `webapp/src/components/UniverseManager.test.tsx`
   (frontend notice).

`data.portfolio_sync.compute_tracked_universe()` itself was **not** changed —
its fallback-only semantics are correct, intentional, already tested, and
documented in CLAUDE.md; the bug was entirely on the reporting side.

## Permanent instrumentation added (for the secondary, unconfirmed mechanism)

Because stage 3 above (a live provider rate-limit/circuit-breaker organically
narrowing a large batch) could not be confirmed or ruled out without live
network access, `pipeline/production_steps.py` now builds a
`universe_funnel` diagnostic dict across the real per-cycle path, threaded
into `state_snapshot.json`:

```json
"universe_funnel": {
  "configured_default_tickers": 430,
  "watchlist_count": 26,
  "discovered_count": 0,
  "default_tickers_is_fallback": false,
  "tracked_universe_before_held": 26,
  "held_positions_added": 2,
  "tracked_universe_total": 28,
  "tech_raw_count": 28,
  "fund_raw_count": 27,
  "dashboard_rows": 28,
  "forecasted_count": 26,
  "skipped_missing_price_count": 2
}
```

A `WARNING`-level log line (`pipeline/production_steps.py::_warn_on_universe_funnel_drop`)
fires the moment any stage drops more than 50% of its input — loose enough
that a routine handful of dead-lettered symbols never spams the log, but
tight enough to flag a repeat of this exact symptom in real time, in the
daemon's own logs, without needing to open `state_snapshot.json` at all.

**What this will reveal next time**: if the 430-vs-26 split (or anything like
it) recurs, `universe_funnel.default_tickers_is_fallback` tells you
immediately whether it's the reporting mechanism above (fixed) or a genuine
mid-pipeline drop; if it's the latter, the per-stage counts pinpoint exactly
which stage (raw data fetch vs. dashboard compilation vs. forecasting)
dropped the symbols, closing the gap this investigation could not close from
a sandbox with no live network access.

## What remains genuinely unknown

Stage 3 (a live provider rate-limit or circuit breaker organically narrowing
a large fetch batch) is a real, code-confirmed mechanism that has NOT been
observed in practice in this pass — it requires live network access and a
real large-universe operator run to confirm or rule out. The instrumentation
above is deliberately aimed at closing exactly this gap the next time it can
be observed live, rather than re-guessing at it from static analysis.
