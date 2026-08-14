# Task Tracker: FMP Pipeline Optimization Post-PR Fixes

## Phase A — Critical Bug Fixes (Tier 1) <!-- id: 0 -->
- [x] 1.1 Graceful fallback in `CompositeProvider` when `FMP_API_KEY` is missing ([`data/market_data.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/data/market_data.py)) <!-- id: 1 -->
- [x] 1.2 Persist "no-data" sentinels in `HistoricalStore` for empty analyst/earnings/insider fetches ([`data/historical_store.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/data/historical_store.py)) <!-- id: 2 -->
- [x] 1.3 Timezone fix in `_apply_fmp_econ_calendar` using `ZoneInfo("America/New_York")` ([`pipeline/production_steps.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/pipeline/production_steps.py)) <!-- id: 3 -->
- [x] 1.4 Economics calendar daily caching in `data/fmp_feeds_market.py` ([`data/fmp_feeds_market.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/data/fmp_feeds_market.py)) <!-- id: 4 -->
- [x] Run Phase A test suite to verify zero regressions <!-- id: 5 -->

## Phase B — Design Hardening (Tier 2) <!-- id: 6 -->
- [x] 2.1 Implement adaptive minimum-reservation deadline splitting in `production_steps.py` ([`pipeline/production_steps.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/pipeline/production_steps.py)) <!-- id: 7 -->
- [x] 2.2 Strengthen `TestSharedDeadline` tests in `tests/test_production_steps_fmp_stubs.py` ([`tests/test_production_steps_fmp_stubs.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/tests/test_production_steps_fmp_stubs.py)) <!-- id: 8 -->
- [x] 2.3 Add malformed payload tests for economics calendar in `tests/test_fmp_feeds_market.py` ([`tests/test_fmp_feeds_market.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/tests/test_fmp_feeds_market.py)) <!-- id: 9 -->
- [x] 2.4 Add `FMP_PAPER_STARTING_CASH` to `FmpSettings.tsx` in webapp ([`webapp/src/screens/FmpSettings.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/webapp/src/screens/FmpSettings.tsx)) <!-- id: 10 -->
- [x] Run full regression suite across all touched files (797 passed) <!-- id: 11 -->

## Phase C — Documentation & Config Cleanup (Tier 3) <!-- id: 12 -->
- [x] 3.1 Fix stale block comments and `FMP_QUOTES_REALTIME` in `.env.example` ([`.env.example`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/.env.example)) <!-- id: 13 -->
- [x] 3.2 Add missing capability keys to `.env.example` (`FMP_ECON_CALENDAR_ENABLED`, `FMP_OPTIONS_CONTEXT_ENABLED`, `FMP_PEERS_ENABLED`, `FMP_PAPER_STARTING_CASH`) ([`.env.example`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/.env.example)) <!-- id: 14 -->
- [x] 3.3 Update `AGENTS.md` and `CLAUDE.md` historical default annotations for `FMP_NEWS_ENABLED` and `FMP_UNIVERSE_ENABLED` ([`AGENTS.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/AGENTS.md), [`CLAUDE.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/CLAUDE.md)) <!-- id: 15 -->
- [x] 3.4 Update `docs/HOW_TO_GUIDE.md` for FMP fundamentals & defaults ([`docs/HOW_TO_GUIDE.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/docs/HOW_TO_GUIDE.md)) <!-- id: 16 -->
- [x] 3.5 Add FMP troubleshooting, rate limits & circuit-breaker section to `docs/RUNBOOK.md` ([`docs/RUNBOOK.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/docs/RUNBOOK.md)) <!-- id: 17 -->
- [x] 3.6 Update data layer reference in `docs/architecture/signal-engines.md` ([`docs/architecture/signal-engines.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/multi_agent_phased_build/docs/architecture/signal-engines.md)) <!-- id: 18 -->

## Verification & PR Sync <!-- id: 19 -->
- [x] Run 797 targeted pytest tests across data, pipeline, and feeds <!-- id: 20 -->
- [x] Sync brain artifacts (`implementation_plan.md`, `task.md`, `walkthrough.md`) to `.claude/` <!-- id: 21 -->
- [x] Add unique plan artifact naming rule to `AGENTS.md` / `CLAUDE.md` (Rule 5) <!-- id: 22 -->
- [x] Push all commits to PR #737 branch (`multi_agent_phased_build`) <!-- id: 23 -->
