# Implementation Plan: Fundamentals-Refresh Cycle Deadline

**Slug:** `pipeline_fundamentals_deadline`
**Date:** 2026-08-28
**Author:** Claude Code (parallel implementation + documentation pass)

## Context

The orchestrator daemon's pipeline was observed hanging in production. A live debug
session watched a real cycle: the daemon logged
`"Routing data through Computational Core (Processing)..."` and then produced no
further output for roughly 15 minutes before the cycle failed. Cross-referencing the
`pipeline_runs` table (`desktop/run_history_store.py`) showed the identical stall
point and an identical ~1199-second stall duration, with zero variance, across all 5
of the prior failed runs on record — this was not a one-off, it was reproducing on
every single cycle.

The 2026-08-27 FRED-timeout fix round (see
`docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`) had already bounded
the earlier "data" fetch stage (`DATA_FETCH_TASK_TIMEOUT_SECONDS`, `FRED_REQUEST_TIMEOUT_SECONDS`)
and, in its follow-up sweep, several `subprocess`/thread-pool/LLM-client call sites
elsewhere in the codebase. Neither pass covered this call site: the "processing" stage
runs strictly after "data" completes (or dead-letters), so the earlier fix's
`MockDataEngine` fallback was working correctly and irrelevant to this hang.

Root cause: `ProcessingEngine.calculate_fundamental_metrics()` (`processing_engine.py`)
loops over every ticker in `fund_dtos` and, whenever `settings.HISTORICAL_STORE_ENABLED`
is `True`, calls `HistoricalStore.get_fundamentals(ticker, max_age_days=..., provider=...)`
for each one — a live network call with no per-call timeout and, more importantly, no
aggregate/cycle-wide deadline across the loop as a whole. A single slow or stalled
provider response for one ticker blocks every ticker queued behind it, and with enough
tickers in the universe the cumulative stall reproduces the observed ~1199s hang.

## Approach

Add a cycle-wide wall-clock deadline to the per-ticker loop, in the same spirit as
`FMP_MAX_SECONDS_PER_CYCLE` (the existing FMP-side per-cycle budget referenced in
`settings.py`'s `DATA_FETCH_TASK_TIMEOUT_SECONDS` docstring) rather than a per-call
timeout on `get_fundamentals()` itself — a per-call timeout alone would still let N
slow tickers compound into an N-times-larger stall; a single deadline checked once per
loop iteration bounds the whole stage regardless of how many tickers are queued.

New setting: `settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE` (float, default
`60.0`). `calculate_fundamental_metrics()` records `time.monotonic()` once, before
entering the per-ticker loop. On each iteration, before calling
`HistoricalStore.get_fundamentals()`, it checks whether the deadline has already passed
(a module-level/local flag, checked and set sticky — once tripped it stays tripped for
the rest of THIS call, it never re-arms mid-loop even if, hypothetically, time briefly
looked to un-trip a naive re-check). If tripped:
- skip the live `HistoricalStore.get_fundamentals()` call for that ticker entirely,
- fall through to the existing DTO-only fundamentals path (the same code path already
  used when `HISTORICAL_STORE_ENABLED=False` or when `HistoricalStore` construction
  itself failed) — CONSTRAINT #6 dead-letter resilience, no ticker is dropped and
  nothing raises,
- log exactly ONE `WARNING` the first time the deadline trips (not one per skipped
  ticker, to avoid log-spamming a wide universe), naming how many tickers remain
  unprocessed by HistoricalStore this cycle.

This mirrors the sticky/dead-letter conventions already established by
`PROCESSING_FUNDAMENTALS...`'s sibling settings (`DATA_FETCH_TASK_TIMEOUT_SECONDS`,
`GDELT` cooldown breaker, `ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE`) rather than inventing a
new pattern.

## Files changed (owned by the parallel implementation agent — listed here for
## traceability; I did not edit these myself)

- `settings.py` — new `PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE: float` field
  (default `60.0`), documented with a `Field(description=...)` matching the voice of
  `DATA_FETCH_TASK_TIMEOUT_SECONDS`/`PIPELINE_STEP_TIMEOUT_SECONDS`.
- `processing_engine.py` — `calculate_fundamental_metrics()`: wall-clock deadline
  computed once before the per-ticker loop; sticky skip-and-fallback of the
  `HistoricalStore.get_fundamentals()` call once the deadline passes; single WARNING
  log on first trip.
- `tests/test_processing_engine.py` — new `TestCalculateFundamentalMetrics` coverage:
  deadline not yet passed → live refresh still attempted; deadline passed before the
  loop starts → every ticker falls back to DTO-only fundamentals, zero
  `get_fundamentals()` calls, exactly one WARNING logged; deadline passes mid-loop →
  tickers before the trip get a live refresh, tickers after it (and all subsequent
  ones) do not, and the skip stays sticky for the rest of the call.
- `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md` — new dated
  2026-08-28 follow-up section: symptom (live-reproduction timing + `pipeline_runs`
  history cross-check), root cause, fix, and the disclosed
  `PIPELINE_STEP_TIMEOUT_SECONDS`-does-not-cover-inner-substeps gap.
- `docs/architecture/signal-engines.md` — extend the existing `processing_engine.py`
  bullet with the new deadline mechanism (mirrors how that bullet already documents
  the Tier 2.3 Phase 3 `HistoricalStore` wiring this deadline wraps).
- `docs/architecture/orchestration-entrypoints.md` — short cross-reference addition
  near the existing FRED-timeout / `PIPELINE_STEP_TIMEOUT_SECONDS` narrative in the
  `main_orchestrator.py` bullet, pointing at the new known-issues follow-up section
  (mirrors that bullet's existing cross-reference pattern for the two prior fixes).
- `CLAUDE.md` — one new dated changelog bullet (done by me; see the bullet immediately
  following "Comprehensive unbounded-timeout sweep" in the "## Project" section).

## Verification

- `pytest tests/test_processing_engine.py -k TestCalculateFundamentalMetrics -v` must
  show zero failures (per `CLAUDE.md`'s "Verification is mandatory, not advisory" rule
  — a targeted test file/class run, not just "should pass").
- Confirm `settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE` defaults to `60.0`
  and that flag-off-equivalent behavior (deadline far in the future, i.e. today's exact
  prior behavior) is unchanged for a normal-sized universe that completes well under
  60s — no regression for the common case.
- `.claude/hooks/verify_targeted_tests.sh` / `.claude/hooks/verify_before_stop.sh` (the
  repo's own PostToolUse/Stop gates) should be allowed to run and pass on the
  `processing_engine.py` edit, per this repo's standard workflow — not something I
  personally invoke, since I do not touch that file, but the parallel implementation
  agent is expected to satisfy it before this PR is considered complete.
- This is an "everything else" (engine/runtime logic) change per `CLAUDE.md`'s
  Start-of-session checklist — it belongs on a feature branch with a PR, not committed
  directly to `main`.
