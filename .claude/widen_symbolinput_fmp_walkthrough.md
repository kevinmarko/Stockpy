# Walkthrough: Widen SymbolInput's dropdown to FMP's full symbol universe

## What changed and why

The third of the operator's three original asks: any symbol-entry field
should suggest any FMP-known symbol, not just tracked ones. The shared
`SymbolInput` component reaches 9 screens, so one change was enough — plus
a real bug found in the process (`UniverseManager` suggesting from the
wrong universe).

## The fix, in order

1. **`SymbolInput.tsx`** gained a debounced `getSymbolSearch` query merged
   into the existing tracked-suggestion list as a flat array (`SuggestionRow[]`
   with a `tracked: boolean` flag) so keyboard nav (`activeIndex`) stays a
   single index across both visual sections — a section-header row is
   inserted at render time right before the first untracked entry, not
   stored as a separate list.
2. Two new optional props default to today's exact prior behavior at every
   call site that doesn't pass them: `enableFmpSuggestions` (opt-out) and
   `trackedSymbols` (override the shared cache).
3. **`UniverseManager.tsx`**: passing `trackedSymbols={list}` fixes a real,
   previously-undetected bug — this screen's own tracked set is
   `DEFAULT_TICKERS` (`GET/PUT /data/universe`), but its `SymbolInput`
   instance was suggesting from `GET /universe` (the pipeline-snapshot
   universe via the shared `universeCache.ts`) — a different list entirely.
4. **`SectorSelection.tsx`**: explicit `enableFmpSuggestions={false}`, per
   the approved plan's exclusion (that screen's backend only reads
   persisted DB state).
5. The other 7 call sites (Data Explorer, Signal Breakdown, Forecast
   Viewer, Sentiment Dynamics, both Pairs Radar instances, Cache Long/Short's
   ConfiguratorWizard, Paper Broker's Quick Trade) needed ZERO code changes
   — the new props' defaults mean they get the widened dropdown for free.

## What I verified before calling this done

- Ran the FULL webapp vitest suite (not just the files I touched) after the
  `SymbolInput.tsx` change specifically, since it's shared by 9 consumers —
  found 2 real regressions this way: `CacheLongShort.test.tsx` and
  `PaperBroker.test.tsx` both had narrow `vi.mock("../api/client")`
  factories missing `getSymbolSearch`, so `SymbolInput`'s new effect threw
  a synchronous `TypeError` inside `useEffect` (a call on `undefined`, which
  happens BEFORE the `.then()/.catch()` chain can absorb it). Fixed both
  mocks properly (adding the missing method + a default resolved value)
  rather than defensively try/catching a call that only fails in
  incomplete test mocks — production `api.getSymbolSearch` always exists.
- Re-ran the full suite again after the fix (1860 passed) to confirm those
  were the only two gaps.
- Considered, then deliberately did NOT add, an `onSelectUntracked` callback
  prop the original plan sketched for "the full add+download flow" on Quick
  Trade/Universe Manager — Quick Trade's untracked-symbol handling is
  already delivered by Phase 1's post-fill prompt in `OptionsOrderTicket.tsx`
  (independent of how the symbol was entered), and Universe Manager's own
  add button already IS the "add" action this screen exists for; I judged
  a mere dropdown SELECTION silently kicking off a watchlist-add + backfill
  network call would violate the "explicit user action, never a silent
  side effect" principle Phase 1 already established. Instead wired
  Universe Manager's existing `addSymbol` to also fire the Phase 1 backfill
  endpoint on success — same outcome, no new component API surface, no
  side effect on mere selection/browsing.
- Live-verified in an actual browser against the mock backend: typed "XO"
  into Universe Manager's field, confirmed the "NOT YET TRACKED" header +
  XOM suggestion rendered, clicked it, confirmed the add succeeded (toast +
  navigation to the symbol detail page) rather than just trusting the unit
  tests for this end-to-end path.

## Disclosed, not hidden

- I simplified the plan's Phase 2 design (dropped the sketched
  `onSelectUntracked` callback) rather than implementing it as originally
  scoped — reasoning above. The observable outcome (widened dropdown +
  visible "not tracked" signal + an actual add+download path for the two
  screens named) is delivered either way; the mechanism differs from what
  the plan document literally described.
