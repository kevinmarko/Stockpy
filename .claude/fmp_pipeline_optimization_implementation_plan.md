# FMP Pipeline Optimization — Post-PR Review & Fix Plan

Four parallel code-review subagents (Pipeline, Settings/API, Tests, Documentation) audited
every file in PR #737. Below is the updated consolidated findings list and execution plan
incorporating the operator's design and sequencing decisions.

---

## Decisions on Review Questions

### Q1: Unsafe Defaults Without `FMP_API_KEY`
**Decision:** Option (a) — Keep `MARKET_DATA_PROVIDER="fmp"` and `FUNDAMENTALS_SOURCE="fmp"` as the default providers with all capability switches `True`. When `FMP_API_KEY` is missing/empty, `CompositeProvider` performs a graceful silent fallback to `yfinance`/Alpaca with a single log warning per cycle instead of crashing with `RuntimeError` or spamming `FMPUnavailable` errors per symbol.
**Degraded State Visibility:** A single log line at cycle start can be missed. In addition to logging, the degraded state ("FMP inactive, running on yfinance fallback") is tracked in provider serve counts / state snapshot so downstream surfaces (Pilots PWA, Command Center, Google Sheets) can visibly surface this degradation rather than silently clamping.

### Q2: Shared Deadline Starvation
**Decision:** Option (b) — Adaptive minimum-reservation budget splitting (`max(remaining / feeds_left, 15s)`). Equal fixed splits waste budget when a feed finishes early, while leaving it unsegmented starves downstream feeds. The adaptive reservation guarantees each feed gets at least 15s (or its proportional remaining share) while allowing unused budget from fast feeds to roll over to subsequent feeds.

### Sequencing & Delivery Strategy
- **Phase A (Correctness Fixes 1.1–1.4):** Implemented and verified as a standalone unit first.
- **Phase B (Design Hardening 2.1–2.4):** Follow-up hardening (adaptive deadline splitting, malformed data testing, webapp UI setting exposure).
- **Phase C (Documentation & Config 3.1–3.6):** Configuration comment fixes and operational runbook updates.

---

## Findings & Action Items

### 🔴 Phase A — Critical Bug Fixes (Tier 1) [COMPLETED]

#### 1.1 Graceful Fallback Without `FMP_API_KEY`
- **Issue:** Missing `FMP_API_KEY` raised hard `RuntimeError` in `CompositeProvider` on startup or spammed errors per ticker.
- **Fix:** Converted `RuntimeError` in `_select_quote_provider` and `__init__` (fundamentals) to a single log warning and automatic fallback to `yfinance`/Alpaca.
- **Status:** ✅ Fixed & verified in `data/market_data.py` and `tests/test_market_data.py`.

#### 1.2 Endless Re-Fetching for Symbols with No Coverage
- **Issue:** Empty responses (`{}`) from analyst/earnings/insider feeds were not persisted, leaving timestamps `None` and triggering API re-fetches every cycle forever.
- **Fix:** Persisted no-data sentinels (`source='fmp-no-data'`, `event_date='1900-01-01'`, `year=0, quarter=0`) via new store methods `mark_earnings_fetched` and `mark_insider_fetched` and `upsert_analyst_snapshot`.
- **Status:** ✅ Fixed & verified in `data/historical_store.py` and `pipeline/production_steps.py`.

#### 1.3 Timezone Rollover Bug in Economics Calendar
- **Issue:** Derived `today_str` using UTC, which rolls over to "tomorrow" between 8 PM and midnight ET, dropping same-evening events.
- **Fix:** Changed date derivation to use `ZoneInfo("America/New_York")`.
- **Status:** ✅ Fixed & verified in `pipeline/production_steps.py`.

#### 1.4 Economics Calendar Caching
- **Issue:** Economics calendar made live API requests on every cycle (1,440 calls/day at 60s cadence).
- **Fix:** Added in-memory date-keyed caching in `data/fmp_feeds_market.py::fetch_economics_calendar` with `reset_econ_calendar_cache()` for test isolation.
- **Status:** ✅ Fixed & verified in `data/fmp_feeds_market.py` and `pipeline/production_steps.py`.

---

### 🟡 Phase B — Design Hardening (Tier 2) [IN PROGRESS]

#### 2.1 Adaptive Minimum-Reservation Deadline Splitting
- **Scope:** Ensure analyst, earnings, and insider feeds share `_fmp_deadline` using `max(remaining / feeds_left, 15.0)` so no feed is starved.
- **Status:** ✅ Implemented in `pipeline/production_steps.py`.

#### 2.2 Shared Deadline Verification Tests
- **Scope:** Update `tests/test_production_steps_fmp_stubs.py` to test adaptive time allocation and verify earnings/insider see reduced remaining time when analyst consumes budget.

#### 2.3 Malformed-Data Tests for Economics Calendar
- **Scope:** Add unit test coverage in `tests/test_fmp_feeds_market.py` for malformed API payloads (missing `event`/`date` keys, bad types).

#### 2.4 Expose `FMP_PAPER_STARTING_CASH` in Webapp Settings
- **Scope:** Add `FMP_PAPER_STARTING_CASH` to `FMP_LABEL_MAP` in `webapp/src/screens/FmpSettings.tsx`.

---

### 🟢 Phase C — Documentation & Config Consistency (Tier 3) [COMPLETED]

- **3.1 `.env.example` Comments:** Updated lines ~260 to reflect `True` defaults and graceful fallback.
- **3.2 `.env.example` Missing Keys:** Added `FMP_ECON_CALENDAR_ENABLED`, `FMP_OPTIONS_CONTEXT_ENABLED`, `FMP_PEERS_ENABLED`, `FMP_PAPER_STARTING_CASH`.
- **3.3 `AGENTS.md` / `CLAUDE.md` Annotations:** Updated `FMP_NEWS_ENABLED` and `FMP_UNIVERSE_ENABLED` historical bullets to `default True (flipped from False, 2026-08 PR #737)`.
- **3.4 `docs/HOW_TO_GUIDE.md`:** Updated fundamentals provider documentation to cite FMP primary with Yahoo fallback.
- **3.5 `docs/RUNBOOK.md`:** Added FMP rate-limiting, circuit-breaker, and troubleshooting guide.
- **3.6 `docs/architecture/signal-engines.md`:** Updated data layer references for FMP fundamentals.

---

## Verification Plan

### Automated Tests
- Full targeted suite: `uv run pytest tests/test_market_data*.py tests/test_historical_store*.py tests/test_production_steps*.py tests/test_fmp_*.py`
- Webapp typecheck: `npm run --prefix webapp typecheck`

### Manual Verification Checklist
1. **Empty-Key Run:** Run orchestrator with `FMP_API_KEY=""` and verify zero crashes and single info/warning fallback log.
2. **Econ Calendar Cache:** Verify subsequent cycles in the same day do not re-request the calendar.
3. **Timezone Check:** Verify event filtering with mock ET timestamp after 8 PM ET.
