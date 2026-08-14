# Walkthrough: FMP Pipeline Optimization Post-PR Review & Fixes

## Overview
Following the 6-phase FMP pipeline rollout (PR #737), a systematic code-review audit identified correctness bugs, design gaps, and documentation inconsistencies. All items across Phase A (Tier 1 bugs), Phase B (Tier 2 hardening), and Phase C (Tier 3 documentation) have been resolved and verified with automated tests.

---

## Changes Implemented

### 1. Phase A — Critical Bug Fixes (Tier 1)

#### 1.1 Graceful Fallback on Missing `FMP_API_KEY`
- **Files Modified:** [`data/market_data.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/data/market_data.py), [`tests/test_market_data.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/tests/test_market_data.py)
- **Fix:** Converted hard `RuntimeError` crashes in `_select_quote_provider` and `_fundamentals_provider` selection to a single warning log per cycle and graceful automatic fallback to `yfinance`/Alpaca.

#### 1.2 "No-Data" Sentinel Persistence
- **Files Modified:** [`data/historical_store.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/data/historical_store.py), [`pipeline/production_steps.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/pipeline/production_steps.py)
- **Fix:** Added `mark_earnings_fetched` and `mark_insider_fetched` methods in `HistoricalStore` and sentinel persistence (`event_date='1900-01-01'`, `year=0, quarter=0`, `source='fmp-no-data'`). This prevents endless re-fetching of symbols with no analyst, earnings, or insider coverage on every cycle.

#### 1.3 Timezone Rollover Fix for Economics Calendar
- **Files Modified:** [`pipeline/production_steps.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/pipeline/production_steps.py)
- **Fix:** Derived `today_str` using `ZoneInfo("America/New_York")` instead of `timezone.utc`, matching FMP's US Eastern date convention and preventing same-evening events from being dropped between 8 PM and midnight ET.

#### 1.4 Economics Calendar Caching
- **Files Modified:** [`data/fmp_feeds_market.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/data/fmp_feeds_market.py), [`tests/test_fmp_feeds_market.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/tests/test_fmp_feeds_market.py)
- **Fix:** Added in-memory date-keyed caching in `fetch_economics_calendar` with a `reset_econ_calendar_cache()` hook, reducing API load from 1,440 requests/day to ≤1–4 requests/day.

---

### 2. Phase B — Design Hardening (Tier 2)

#### 2.1 Adaptive Minimum-Reservation Deadline Splitting
- **Files Modified:** [`pipeline/production_steps.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/pipeline/production_steps.py)
- **Fix:** Split `_fmp_deadline` into adaptive reservations (`max(remaining / feeds_left, 15.0)`), guaranteeing earnings and insider feeds receive time budget even when earlier feeds process large universes.

#### 2.2 Shared Deadline Verification Tests
- **Files Modified:** [`tests/test_production_steps_fmp_stubs.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/tests/test_production_steps_fmp_stubs.py)
- **Fix:** Added `test_time_consumed_by_analyst_reduces_budget_for_subsequent_feeds` to verify that monotonic time progression during early feeds properly bounds subsequent feeds.

#### 2.3 Malformed Payload Tests
- **Files Modified:** [`tests/test_fmp_feeds_market.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/tests/test_fmp_feeds_market.py)
- **Fix:** Added tests for malformed economics calendar API rows (missing keys, bad data types) and verified safe fallback to NaN without exception.

#### 2.4 Webapp Setting Exposure
- **Files Modified:** [`webapp/src/screens/FmpSettings.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/webapp/src/screens/FmpSettings.tsx)
- **Fix:** Added `FMP_PAPER_STARTING_CASH: "Paper Account Starting Cash ($)"` to `FMP_LABEL_MAP`.

---

### 3. Phase C — Documentation & Config Consistency (Tier 3)

- **`.env.example`:** Updated comments to reflect `True` defaults and graceful fallback, and added 4 missing FMP capability keys.
- **`AGENTS.md` / `CLAUDE.md`:** Updated historical default annotations for `FMP_NEWS_ENABLED` and `FMP_UNIVERSE_ENABLED`.
- **`docs/HOW_TO_GUIDE.md`:** Documented FMP as primary fundamentals provider with Yahoo fallback.
- **`docs/RUNBOOK.md`:** Added FMP rate limiting, circuit breaker cooldown, and troubleshooting guide.
- **`docs/architecture/signal-engines.md`:** Updated data layer references for FMP.

---

## Verification Results

- **Python Tests:** 794 passed across all touched modules (`test_market_data*.py`, `test_historical_store*.py`, `test_production_steps*.py`, `test_fmp_*.py`).
- **Webapp Tests & Typecheck:** Clean typecheck and unit test run.
