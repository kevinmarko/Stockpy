# Task tracker: Widen SymbolInput's dropdown to FMP's full symbol universe

- [x] `SymbolInput.tsx`: debounced FMP suggestions, "Not yet tracked"
      section, `enableFmpSuggestions`/`trackedSymbols` props.
- [x] `webapp/src/index.css`: `.combobox-section-header` style.
- [x] `UniverseManager.tsx`: `trackedSymbols={list}` fix + backfill trigger
      on add.
- [x] `SectorSelection.tsx`: `enableFmpSuggestions={false}`.
- [x] New tests: `SymbolInput.test.tsx` (5), `UniverseManager.test.tsx` (2).
- [x] Fixed two pre-existing test files with incomplete `vi.mock` factories
      (`CacheLongShort.test.tsx`, `PaperBroker.test.tsx`).
- [x] `npm run --prefix webapp typecheck` clean.
- [x] Full webapp vitest suite (1860 tests) — no regressions.
- [x] Live browser check against mock backend — confirmed the "Not yet
      tracked" section renders and selecting it adds the symbol.
- [x] `docs/architecture/webapp-and-gui.md` updated.
- [x] `CLAUDE.md` changelog bullet (auto-mirrored to `AGENTS.md`).
- [ ] Open PR (stacked on Phase 1's branch), request review, merge.
- [ ] After merge: sync local `main` checkout per CLAUDE.md's start-of-session
      checklist step 6.
