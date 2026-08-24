# Implementation Plan: Widen SymbolInput's dropdown to FMP's full symbol universe

Phase 2 of a 4-phase body of work scoping "Symbol Screener → universe
integration" (Phase 0 shipped as PR #885, Phase 1 as PR #886; this branch
is stacked on Phase 1's since it depends on `api.triggerSymbolBackfill`).

## Context

The user asked that "the Symbol Screener and really for any symbol should
at least show a list or dropdown of any symbol that is in FMP." The
platform's shared symbol-entry combobox (`SymbolInput.tsx`) is used at 9
call sites and only ever suggested from the tracked pipeline universe.

## Approach

- `SymbolInput.tsx`: added a debounced `GET /data/symbol-search` query
  merged with the existing tracked-universe suggestions, rendered under a
  "Not yet tracked" section, deduped against the tracked set. Two new
  optional props, both defaulting to today's exact prior behavior:
  `enableFmpSuggestions` (opt-out, used by `SectorSelection.tsx`) and
  `trackedSymbols` (override the shared universe cache with a caller-owned
  list, used by `UniverseManager.tsx` to fix a real bug — it was suggesting
  from `GET /universe`, a different list than the `DEFAULT_TICKERS` it
  actually edits).
- `UniverseManager.tsx`: `trackedSymbols={list}` fixes the wrong-universe
  bug; `addSymbol` also now fires Phase 1's `POST /data/backfill/{symbol}`
  non-blockingly after a successful add, since this screen's whole purpose
  is bringing an untracked symbol into scope.
- The other 7 call sites needed zero code changes — the new props default
  to today's behavior, so they get the widened dropdown for free.
- `SectorSelection.tsx`: explicit `enableFmpSuggestions={false}` (excluded
  per the approved plan — `GET /sector/selection` is DB-state-only).

## Documentation

- `docs/architecture/webapp-and-gui.md` — new `SymbolInput.tsx`/`universeCache.ts`
  entry.
- `CLAUDE.md` changelog bullet (auto-mirrored to `AGENTS.md`).

## Verification

- New tests in `SymbolInput.test.tsx` (5): FMP section rendering, dedup
  against tracked duplicates, `enableFmpSuggestions={false}` suppression,
  `trackedSymbols` override.
- New tests in `UniverseManager.test.tsx` (2): backfill-trigger-on-add,
  suggestion-source-fix regression.
- Fixed two pre-existing test files whose narrow `vi.mock("../api/client")`
  factories didn't include `getSymbolSearch`/`getUniverse`/`triggerSymbolBackfill`
  (`CacheLongShort.test.tsx`, `PaperBroker.test.tsx`) — surfaced by a full
  suite run, not discovered later via CI.
- `npm run --prefix webapp typecheck` clean.
- Full webapp vitest suite (1860 tests) — no regressions.
- Live browser check against the mock backend: typed "XO" into Universe
  Manager's "Add a stock" field, confirmed the "NOT YET TRACKED" section
  header + XOM suggestion rendered, clicked it, confirmed it was added and
  the app navigated to its detail page.
