# Universe Transparency — Task Tracker

> Companion to `universe-transparency_implementation_plan.md`. Save as
> `.claude/universe-transparency_task.md`.

## §0 Dependency Check (blocking — do first)
- [x] Confirm `GET /universe` / `GET /data/universe` / `build_sync_report()` response schemas
- [x] Confirm `UniverseCoverage.tsx` current props/data source and what it already distinguishes
- [x] Locate the source of truth for forecast-universe membership (~26 symbols)
- [x] Confirm `universeCache.ts` contents/refresh cadence
- [x] Re-run `get_universe_status` / check live daemon state fresh
- [x] Confirm branch/PR requirement under this repo's start-of-session checklist

## Phase 1 — Composition panel
- [x] Build three-count component (Tracked / Forecast-covered / Full coverage)
- [x] Wire to existing endpoint(s) per §0 findings — no new backend unless needed
- [x] Click-through to filtered symbol lists (reuse existing list components)
- [x] Component test: counts correct incl. all-equal and diverging cases

## Phase 2 — Help content
- [x] `TAB_HELP` entry explaining the three-number distinction
- [x] `GLOSSARY` entries: tracked universe / forecast-covered / data coverage
- [x] Link out to Symbol Screener / SymbolInput "not yet tracked" for wider market

## Phase 3 — Optional backend endpoint (only if Phase 1 composition is messy)
- [x] `GET /universe/summary` (read-only, no new universe logic) — Skipped, Phase 1 logic works cleanly via the UI using existing data.

## Doc sync (do not skip — required by repo convention)
- [x] `CLAUDE.md` changelog bullet
- [x] `AGENTS.md` / `GEMINI.md` mirror
- [x] `docs/architecture/webapp-and-gui.md` if new component/screen added (No new component added, just modified UniverseCoverage)

## Explicitly NOT in this task list
- Changing universe-resolution logic
- Closing the 430-vs-26 forecast/trading mismatch (separate future plan, see
  implementation plan §6)
