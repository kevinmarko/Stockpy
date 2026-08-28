# Walkthrough: Fundamentals-Refresh Cycle Deadline

**Slug:** `pipeline_fundamentals_deadline`
**Audience:** PR reviewer

## What was broken

The orchestrator daemon's pipeline hung every single cycle. A live debug session
watching the daemon process in real time observed the following sequence:

1. The daemon started a cycle normally.
2. It logged `"Routing data through Computational Core (Processing)..."`.
3. It then went **completely silent** — no further log lines, no progress — for
   roughly **15 minutes**.
4. The cycle then failed.

This was not a one-off fluke. Cross-referencing the `pipeline_runs` table
(`desktop/run_history_store.py`) — the durable run-history store described in
`CLAUDE.md`'s "Durable pipeline run history" bullet — showed the **exact same stall
point and an exact ~1199-second stall duration, with zero variance**, across all 5 of
the prior failed runs on record. Same stage, same call site, same timing, every time.

## Why it wasn't the already-fixed "data" stage

The 2026-08-27 FRED-timeout incident (`docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`)
already bounded the earlier "data" fetch stage: `DATA_FETCH_TASK_TIMEOUT_SECONDS` (180s)
correctly dead-letters a stuck sub-fetch and falls back to `MockDataEngine`. That fix
round's own follow-up sweep audited `subprocess`/thread-pool/LLM-client call sites
elsewhere in the codebase. Neither pass touched the LATER "processing" stage, which
runs only after "data" has completed (or dead-lettered) — confirmed by the log line
itself ("...Processing...") and by the stall happening well after any 180s data-stage
bound would have already resolved.

## Root cause

`ProcessingEngine.calculate_fundamental_metrics()` (`processing_engine.py`) loops over
every ticker in `fund_dtos` and, when `settings.HISTORICAL_STORE_ENABLED=True`, calls:

```python
_hist_store.get_fundamentals(
    ticker,
    max_age_days=settings.FUNDAMENTALS_REFRESH_DAYS,
    provider=_provider,
)
```

— a live network call with **no timeout on the call itself, and no aggregate deadline
across the loop as a whole**. If the provider stalls (or is simply slow) on one ticker,
every ticker queued behind it in the same cycle is blocked too. With enough tickers in
the universe, the cumulative wait reproduces the observed ~1199s stall.

## What changed

New setting `settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE` (float, default
`60.0`). `calculate_fundamental_metrics()` now:

1. Records a wall-clock deadline once, before entering the per-ticker loop.
2. On each iteration, checks whether that deadline has already passed. Once it has,
   the check is **sticky** — it never re-arms for the remainder of that call, even for
   the tickers still left in the loop.
3. Once tripped, skips the live `HistoricalStore.get_fundamentals()` call for that
   ticker (and every remaining one) and falls through to the existing DTO-only
   fundamentals path — the same path already used when `HISTORICAL_STORE_ENABLED` is
   `False`. This is dead-letter resilience per CONSTRAINT #6: no ticker is dropped and
   nothing raises, it just doesn't get the DB-cached/refreshed fundamentals overlay for
   the rest of that cycle.
4. Logs exactly **one** WARNING the first time the deadline trips — not one per skipped
   ticker — so a wide universe doesn't spam the log.

This mirrors the sticky, cycle-wide-budget pattern already established elsewhere in
this codebase (`DATA_FETCH_TASK_TIMEOUT_SECONDS`'s per-sub-fetch bound, the GDELT
cooldown circuit breaker, `ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE`) rather than a fresh
one-off pattern, and a cycle-wide deadline (not a per-call timeout on
`get_fundamentals()` alone) is the right shape here specifically because a per-call
timeout would still let N slow tickers compound into an N-times-larger total stall —
only a shared budget across the whole loop actually bounds the stage.

## Disclosed, NOT fixed in this PR

`settings.PIPELINE_STEP_TIMEOUT_SECONDS` (the 2026-08-27 follow-up's structural
backstop) only wraps `AsyncPipelineRunner.run()`'s dispatch of an entire synchronous
pipeline step. `RunPipelineStep`'s own inner, separate synchronous `PipelineRunner` —
which drives `OptionsAnalysisStep → ProcessingStep → ForecastingStep → StrategyEvalStep`
— has no per-substep timeout mechanism of its own. A future unbounded call inside any
of those four substeps would reproduce this same class of hang with nothing to catch
it. This PR closes the one concrete, live-reproduced cause found in this incident; it
does not close that broader architectural gap. See
`docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`'s 2026-08-28
follow-up section for the full write-up.

## How to verify

```bash
pytest tests/test_processing_engine.py -k TestCalculateFundamentalMetrics -v
```

Expect coverage for at least these three states, all passing:

- Deadline not yet passed when a ticker is processed → the live
  `HistoricalStore.get_fundamentals()` refresh is still attempted for that ticker,
  exactly as before this change (no regression for the common, fast-cycle case).
- Deadline already passed before the per-ticker loop starts → every ticker falls back
  to the DTO-only fundamentals path, `get_fundamentals()` is never called, and exactly
  one WARNING is logged (not one per ticker).
- Deadline passes partway through the loop → tickers processed before the trip still
  got a live refresh; the ticker that trips the deadline and every ticker after it in
  iteration order fall back to DTO-only fundamentals; the skip stays sticky for the
  rest of the call (does not re-arm).

Also confirm no result is ever dropped or an exception raised for a skipped ticker —
CONSTRAINT #6 dead-letter resilience — and that `settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE`
defaults to `60.0`.
