# Original User Request

## 2026-09-07T19:06:49Z

# Teamwork Project Prompt — Draft

> Status: Ready for launch — awaiting user approval
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Use a very large team of agents (team of 8 agents).

Build the "Explain This Ticker" feature (a reusable UI slide-over panel summarizing a company's profile, universe tracking status, factor breakdown, and recent price action) and the "Universe Transparency" feature (a global panel showing coverage status from sync-reports) into the Stockpy Pilots PWA. 

Working directory: /Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/bright_quasar_rises_14h56
Integrity mode: benchmark

## Requirements

### R1. FMP Company Profile Wrapper
Implement the `company_profile` wrapper in `data/fmp_client.py` using FMP's `/profile` endpoint, gated by the `FMP_PROFILE_ENABLED` setting. Create a manual verification script `scripts/verify_fmp_profile.py` mirroring `verify_fmp_bars.py`.

### R2. Explain This Ticker API Endpoint
Create `GET /data/explain/{symbol}` returning:
- Company description (from the FMP wrapper).
- Why it's tracked (reusing `build_sync_report()` logic).
- Factor breakdown (read-only adapter of DailySignals).
- Price history status (for recent price action chart).

### R3. Explain This Ticker UI Panel
Create a reusable slide-over panel in the PWA that displays the four sections honestly without fabricating data. Add a small info icon (ⓘ) next to ticker symbols across the app that triggers this panel. 

### R4. Universe Transparency Panel
Build a global panel in the PWA that relies on the existing `GET /data/sync-report` endpoint data. Infer the best UI layout to show transparency into symbol coverage status.

### R5. Documentation and Tests
Keep agent-context docs in sync (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `Gravity AI Review Suite.py`). Write Python and React tests verifying the new components.

## Verification Resources
- Existing `make verify` and `Gravity AI Review Suite.py` scripts must be used to validate the backend integrity.
- Automated tests (`pytest` for Python, `npm run test` for React) must be added or extended to cover the new endpoints and UI components.

## Acceptance Criteria

### API Contracts
- [ ] `company_profile` fails cleanly (no fabricated data) when disabled or unavailable, logging an honest missing-data status.
- [ ] `GET /data/explain/{symbol}` returns accurate, non-fabricated data for tracked and untracked symbols.

### UI & UX
- [ ] "Explain This Ticker" panel gracefully handles missing sections (e.g., "description unavailable").
- [ ] Panel does not invent composite scores or fabricate missing price lines.
- [ ] Clicking the (ⓘ) info icon correctly opens the panel for the specified symbol from multiple screens.

### Automated Testing
- [ ] `make verify` and `Gravity AI Review Suite.py` pass without failing constraints.
- [ ] `pytest` tests pass for the FMP profile wrapper and the `/data/explain` endpoint.
- [ ] Webapp typecheck (`npm run --prefix webapp typecheck`) passes.
