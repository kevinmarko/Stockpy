# Remove dead webapp tabs — walkthrough (2026-10-02)

**Why.** The shrink and vendor-removal PRs archived backend code but left the Pilots PWA screens that called it. A read-only audit hit every tab against the running backend. No tab 404s, but three were permanently empty or demo-only:

- **Pairs radar** (`/pairs`): switched on in `.env`, yet `output/pairs.json` held 0 pairs.
- **Cache L/S** (`/cache-long-short` plus its Settings page): switched on, yet `cache_ls_positions` and `cache_ls_tax_lots` were empty.
- **SVI Stitching Algorithm Demo** (`/research/trends-stitcher`): a demo showing stale Trends data.

The operator chose to remove all three. Removing code is allowed during the step-7 feature freeze.

## What changed

**Backend.**
- Cache L/S is archived to `legacy/`: engine, store, pilots helper and tests. Also removed:
  - the `main_orchestrator.main()` background worker
  - `POST /data/cache-long-short/simulate`
  - the `/pilots/cache-long-short/*` and `/settings/cache-long-short` routes
  - the six `CACHE_LONG_SHORT_*` settings, their `env_io` entries and the `DANGEROUS_KEYS` entry
- The Pairs radar is archived: `pilots/pairs.py` and `reporting/pairs_snapshot.py`. Also removed:
  - the `StateSnapshotStep` writer call
  - `GET /pairs`
  - `POST /data/pairs/{analyze,scan}`
  - `PAIRS_SNAPSHOT_*`
- What stays:
  - the `pairs_trading` signal
  - `pairs_ondemand.py`, which the MCP `analyze/scan_pairs_arbitrage` tools use
- The SVI demo: `GET /data/trends/stitch-demo` and `TrendsStore.get_query_terms_with_raw_windows` (only the demo used it) were removed. `data/trends_stitcher.py` stays for the ASVI pipeline.

**Other cleanup.**
- **Strategy Health's validation trend** hid nothing, so it listed 33 archived options strategies, e.g. "Bear Put Spread_ARR" and `covered_call`. `pilots/validation_trend.py` now filters them through an explicit archived list. A test fails if any id on that list is still registered in `scripts/refresh_validations.py::STRATEGY_REGISTRY`. The files and DB rows are kept.
- **The MCP `audit_all_pwa_screens` tool** checked 7 routes that no longer exist. It now uses the real list of 19.

**Webapp.**
- The three screens, their components, routes, nav entries, help entries and API methods are removed (types, `client.ts`, `mock.ts`).
- Also removed:
  - `ModelComparisonChart`, whose endpoint always returned an empty synthetic payload
  - the options-desk remnants in Commands (`options_strategy_registry`)
- The Model Health panel's no-symbol fallback now shows an honest empty state.

**Data left untouched.**
- The `cache_ls_*` DB tables.
- `output/pairs.json`.
- The old `.env` keys, which are now ignored (`extra="ignore"`).

## Verification
- **Full offline suite:** `pytest -m "not network and not slow" -n auto` gives 11593 passed, 24 skipped.
- **Ruff F821/F822/F823/E9:** clean on the touched files.
- **Webapp:**
  - `npm run typecheck`: clean.
  - `vitest`: 146 files and 1730 tests passed.
  - Mock/live method parity checked by script.
- **Browser (mock mode, port 5181):**
  - Models, Commands, Research, Marketplace, Settings > Modules, Agentic, Strategy Health and Help all render.
  - The four removed routes fall back to the dashboard.
  - The sidebar no longer lists the three tabs.
  - The only console errors are pre-existing WebSocket retries to the real backend.
