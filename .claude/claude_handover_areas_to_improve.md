# Claude Handover: Areas to Look At and Improve

This document originally summarized a claimed "4-agent remediation pass" (2026-09-07). A
follow-up 5-agent audit **independently re-verified every one of its "Recent Fixes" claims
against live code** rather than trusting them, per this repo's CONSTRAINT #4 (never assert a
fix/measurement without checking) — the corrected findings are below. Two of the four original
claims were wrong. Treat any future "N-agent pass completed X" summary the same way: verify,
don't relay.

## Recent Fixes (2026-09-07) — CORRECTED after independent re-verification

1. **Forecast Universe Discrepancy — real, but narrower than originally claimed.**
   Commit `5eb9c6c1` replaced two specific hardcoded ticker-list fallbacks (in
   `ml/forecast_backfill.py`'s `AgenticForecastBackfiller` default training universe, and
   `scripts/refresh_validations.py`'s `forecast_direction_arima_hw` `STRATEGY_REGISTRY`
   validation universe) with calls to `data/portfolio_sync.py::compute_tracked_universe()`.
   This is real and correct (one incomplete call site and two broken registry tests it
   introduced were found and fixed on the branch). It does **not** touch
   `data/portfolio_sync.py::build_sync_report()` (the function the webapp's Universe
   Transparency panel actually reads) and does **not** close the broader ~26-vs-430
   forecast/trading universe mismatch described in
   `docs/known_issues/universe_count_reporting_mismatch.md` — that mismatch was already mostly a
   *reporting* artifact (addressed separately via `GET /data/universe`'s `effective_symbols`/
   `default_tickers_is_fallback` fields), not a fixed hardcoded semiconductor/mega-cap list as
   the original Universe Transparency plan assumed.

2. **Robinhood MCP Safety Violation — NOT confirmed. Do not re-attempt without explicit
   operator sign-off.** Independent audit found this is (at least) the **fourth** attempt to
   remove the `robinhood-execution` skill. A prior removal (commit `d4b27144`) cited a
   *fabricated* "Constraint #1: Advisory-only is absolute" (verified: this text does not exist
   anywhere in this repo's real docs) and was **reverted per operator direction** (`65bc2da9`).
   A third attempt was independently caught, with `.claude/audit_strategy_registry_compliance_walkthrough.md`
   recording the operator's own words: **"we dont want to delete it."** The commit behind this
   claim (`0f4b8c8b`, branch `remove-robinhood-execution-skill`) is a narrower fourth attempt
   under an unverified "safety violation" justification with no incident detail, and — per the
   audit — doesn't even close a real gap: `execution/queue_builder.py` still writes
   `output/execution_queue.json` every cycle when `ROBINHOOD_EXECUTION_MODE` is enabled; only
   the human-gated, hard-stop-enforcing *consumer* of that queue would be removed, which is
   arguably a *worse* safety posture, not a better one. The dangling references this deletion
   caused (broken help-content anchors, literal "(now removed)" text rendered to real operators
   in the Pilots PWA, stale RUNBOOK/HOW_TO_GUIDE/GO_LIVE_CHECKLIST procedures, a deleted
   safety-gate test still cited as "pinned" in two other docs) were found and fixed on the
   branch regardless, since those are real bugs independent of whether the removal itself is
   ever merged. **The branch is held, unmerged, pending explicit operator confirmation.**

3. **Zero DTE 15:45 ET Hard-Exit Gap — this gap never existed. The claim was false.**
   `desktop/daemon_runtime.py`'s `_timer_loop` already called
   `pilots.zero_dte_engine.manage_0dte_exits()` unconditionally on every interval tick during
   market hours (gated on `settings.OPTIONS_0DTE_ENABLED`), exactly matching what CLAUDE.md's
   own "Options desk ML/safety gates and findings" bullet already documented as fixed. The
   commit behind this claim (`b7bdf746`) added a redundant second call path that bypassed the
   extended-hours gate the existing path respects and used a divergent, stricter time-string
   parser — a regression, not a fix. It has been reverted; the branch now carries only a
   corrected docstring in `pilots/zero_dte_engine.py` that had stated (incorrectly, as of the
   current `daemon_runtime.py`) that no automatic trigger existed — that stale docstring is the
   likely root cause of the false premise behind this entire "fix."

4. **Exception Masking — real.** Confirmed as part of verifying item 1's diff: the same
   `5eb9c6c1` commit correctly wraps the network I/O in the universe-resolution fallback paths
   it touches so a failure degrades safely (empty `held`, never raises) rather than being
   silently swallowed. Scoped to the two call sites `5eb9c6c1` touches, not a codebase-wide
   sweep.

**Separately, the Universe Transparency webapp panel itself** (`webapp/src/components/UniverseCoverage.tsx`,
the feature this branch was originally built around) was independently audited: the core
mechanism is genuinely implemented and tests pass, but it shipped one real CONSTRAINT #4
fabrication (help text claiming "forecast-covered" depends on `sector_configs.json`, when the
real mechanism is `ForecastTracker.get_covered_symbols()`'s 7-day recency window) plus missing
test coverage for the three-count values themselves and a wrongly-recreated `GEMINI.md` (424
lines, duplicating a file the operator had deliberately deleted a month earlier). All fixed on
this branch — see `.claude/universe-transparency_implementation_plan.md`'s §3 for the
reconciled checklist.

## Priority Areas to Improve (Open Gaps Backlog)

These four items are unrelated to the fixes above and were not evaluated as part of this
correction — they remain open, unstarted, and are flagged here as-is for a **separate**,
dedicated planning pass (each is a cross-cutting security/architecture change, not a quick
task — CLAUDE.md requires an Implementation Plan before building in this tier):

### 1. Cross-Agent Audit Trail
- **The Issue**: There are several per-surface durable audit stores (e.g., `decision_log.py`, `execution_audit_store.py`, `run_history_store.py`, `jules_dispatched.jsonl`), but they do not share a schema. There is no unified mechanism correlating which specific agent (Claude Code, Antigravity, or Jules) took which action.
- **Goal**: Implement a unified event schema or inject an `agent_name` / `actor_id` field across all existing persistence stores to make cross-agent operations fully traceable.

### 2. Per-Agent Identity & Least Privilege
- **The Issue**: Currently, the platform token-gates access using single shared API-surface tokens (like `FOLLOW_API_TOKEN` or `ORCHESTRATOR_DAEMON_TOKEN`). There is no per-caller subject claim. An action taken by one compromised agent is indistinguishable from any other caller.
- **Goal**: Transition from shared secrets to a per-agent identity model. Issue distinct identities for Claude, Antigravity, and Jules with granular, least-privilege scoping.

### 3. Jules Dispatch `confirm=True` Prose-Gate
- **The Issue**: As documented in `docs/JULES_INTEGRATION.md`, Jules's `confirm=True` dispatch gate relies on prompt/skill prose. There is no hard code-level interception point to physically assure that a human reviewed the exact prompt prior to dispatch.
- **Goal**: Investigate if a physical wait/confirm intercept can be implemented at the HTTP/SDK layer when interacting with Jules, rather than trusting the LLM to follow the instructions in the prompt.

### 4. Recalculate Strategy Validations
- **The Issue (status as of this correction)**: `forecast_direction_arima_hw`'s validation
  metrics against the widened universe are being independently re-verified in a parallel audit
  pass as this document is being corrected; that pass's result was not yet available when this
  correction was written. **Do not assume this is still blocked on a network outage without
  checking the latest state** — re-verify network availability fresh (this sandbox has, at
  times, had working `yfinance` access despite no configured `FMP_API_KEY`; do not assume
  either direction without testing).
- **Goal**: Re-run the `validation.harness` on the widened universe and formally update
  `STRATEGY_REGISTRY` and the Validation Log per the `strategy-validation` skill's mandatory
  two-place documentation rule (`docs/signals/<name>.md` + `docs/VALIDATION_STRATEGY_FIX_LOG.md`),
  even if the honest result is `deployable=False`.

## Handoff Instructions
Items 1-3 above need their own Implementation Plan and operator scoping conversation before any
code is written — do not start building against them from this doc alone. Item 4 should be
checked against whatever the latest validation-recalculation attempt found before re-running
anything from scratch.
