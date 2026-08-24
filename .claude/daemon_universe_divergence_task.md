# Task tracker: Fix daemon universe-divergence bug

- [x] Extract `compute_tracked_universe()`/`load_env_watchlist()` into
      `data/portfolio_sync.py`.
- [x] Refactor `main.py::_build_universe()`/`_load_watchlist()` to delegate.
- [x] Fix `pipeline/production_steps.py::AsyncDataFetchStep.run()` to read
      `WATCHLIST`/`watchlist.txt` via the shared function and no longer drop
      `DEFAULT_TICKERS` when discovery has candidates.
- [x] New regression tests: `tests/test_production_steps_universe.py`.
- [x] Unit tests for the two new functions: `tests/test_portfolio_sync.py`.
- [x] Re-run full pre-existing coverage (401 tests) to confirm no regression.
- [x] `docs/known_issues/daemon_universe_watchlist_divergence.md` +
      `docs/known_issues/README.md` index entry.
- [x] `docs/architecture/data-layer.md` + `docs/architecture/orchestration-entrypoints.md`
      updated (including fixing pre-existing stale `_build_universe` docs).
- [x] `CLAUDE.md` changelog bullet (auto-mirrored to `AGENTS.md`).
- [ ] Open PR, request review, merge.
- [ ] After merge: sync local `main` checkout per CLAUDE.md's start-of-session
      checklist step 6.
