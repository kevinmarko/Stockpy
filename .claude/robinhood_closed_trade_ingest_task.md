# Robinhood Closed-Trade Ingest — Task Tracker

- [x] 1. `data/broker_fills_store.py` (durable store) + `tests/test_broker_fills_store.py` + `conftest.py` isolation fixture
- [x] 2. Settings (`settings.py`, `.env.example`, `gui/env_io.py` ALLOWED_KEYS) — 6 new fields
- [x] 3. `data/robinhood_orders.py` — dead `_login` import fix, worker/non-worker fetcher dispatch, resolver seed/budget
- [x] 4. `data/robinhood_login_worker.py` orders ingest (`_ingest_orders_best_effort`) + `LoginPhase`/webapp phase label
- [x] 5. Universe retention — `main.py::_build_universe`, `data/portfolio_sync.py` (build_sync_report/resolve_universe/async_sync_now leak fix) + `tests/test_universe_retention.py`
- [x] 6. `pilots/trade_history.py` + `GET /portfolio/trade-history` + strategy-matrix allowlist override + backend tests
- [x] 7. Webapp: types → client → mock → `TradeHistory.tsx` → route → nav → Marketplace tile → Portfolio "See all" link → helpContent → test
- [x] 8. `evaluation_engine.py` opt-in broker-trade MAE/MFE/Edge Ratio fallback + tests
- [x] 9. Docs: CLAUDE.md/AGENTS.md, `docs/architecture/data-layer.md`, `orchestration-entrypoints.md`, `webapp-and-gui.md`, known-issues (update + new)
- [x] 10. `/verify` — ruff genuine-bug gate + full offline pytest suite (12,195 passed) + webapp typecheck/test/build (170 files, 1868 tests, build clean)

## Verification evidence

- Backend: `python3 -m ruff check . --select=F821,F822,F823,E9` → all checks passed
- Backend: `python3 -m pytest -m "not network and not slow" -n auto --dist loadgroup` → 12,195 passed, 31 skipped (8 pre-existing failures confirmed unrelated via `git stash` diff against unmodified branch tip)
- Webapp: `npm run typecheck` → clean
- Webapp: `npx vitest run` → 170 files, 1868 tests passed
- Webapp: `npm run build` → clean production build

## Real-account facts this feature addresses (verified via read-only Robinhood MCP calls)

4 real sells in the last ~6 months, none previously visible anywhere in the platform:
CMCL (+$251.97, 2026-08-20), ARCC (-$24.40, 2026-06-29), PBF (+$858.80, 2026-06-22),
IVR (+$54.97, 2026-01-30). 17 round-trips total back to 2024.
