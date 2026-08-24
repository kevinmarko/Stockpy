# Task tracker: Spot data download + honest "add to watchlist" flow

- [x] `POST /data/backfill/{symbol}` in `api/data_api.py`.
- [x] `webapp/src/api/client.ts`: `triggerSymbolBackfill` + `watchCandidate`
      structured-error fix.
- [x] `webapp/src/api/mock.ts`: `triggerSymbolBackfill` mock.
- [x] `webapp/src/api/types.ts`: `SymbolBackfillResult`.
- [x] `OptionsOrderTicket.tsx`: visible watchlist error, backfill trigger,
      not-tracked fill-time prompt.
- [x] New/extended tests: `tests/test_data_api.py` (backend),
      `OptionsOrderTicket.test.tsx` (frontend).
- [x] `npm run --prefix webapp typecheck` clean.
- [x] Full webapp vitest suite (1854 tests) — no regressions.
- [x] Live browser check against mock backend — confirmed both the
      watchlist-add confirmation and backfill note render.
- [x] `docs/architecture/webapp-and-gui.md` + `docs/architecture/data-layer.md`
      updated.
- [x] `CLAUDE.md` changelog bullet (auto-mirrored to `AGENTS.md`).
- [ ] Open PR, request review, merge.
- [ ] After merge: sync local `main` checkout per CLAUDE.md's start-of-session
      checklist step 6.
