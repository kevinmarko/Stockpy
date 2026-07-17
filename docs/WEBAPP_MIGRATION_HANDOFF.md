# Web App Data Migration — AI Handover Document

## Current Status
We are in the middle of a major effort to migrate the InvestYo Quant Platform's primary operator UI from Streamlit to a React PWA (`webapp/`). Specifically, we are exposing the Python backend engines via FastAPI so the React frontend can consume them.

## What has been accomplished so far:
1. **Repo Cleanup (Part 1):** We removed various debug files, corpus dumps, and one-off scripts from the root directory to clean up the repository. Legacy Google Apps Script files (`StrategyEngine`, `Necessary Imports & Variable Declarations`) were explicitly kept.
2. **Phase 1 (Data Ingestion API):** Created `api/data_api.py` to expose market data, fundamentals, and portfolio sync status.
3. **Phase 2 (Metrics & Signals API):** Created `api/metrics_api.py` to expose indicator calculations, forecasts, signal module scores, and options analysis from the backend engines.
4. **Phase 3 (Pipeline Control Endpoints):** Updated the core `OrchestratorDaemon` (`desktop/daemon_runtime.py`) and `main_orchestrator.py` to accept a `mode` parameter (`"full"`, `"data"`, `"metrics"`). Added `POST /pipeline/data` and `POST /pipeline/metrics` to `api/control_api.py` to allow triggering specific pipeline subsets.
5. **Phase 4.1 (Pipeline Dashboard UI):** Created the `PipelineDashboard.tsx` screen in the React webapp to monitor background run status and trigger the new pipeline modes. Added mocked API endpoints (`webapp/src/api/mock.ts`) for offline frontend development. All TypeScript compilation and build steps currently pass.

## Where we left off:
We just finished implementing Phase 4.1 (Pipeline Dashboard).

## What the NEXT AI should do:
You are continuing Phase 4 of the "Web App Data Migration" plan.

### Immediate Next Steps:
1. **Review `api/client.ts` and `api/mock.ts`:** Familiarize yourself with the endpoints that have been wired up so far.
2. **Build the next WebApp Screens (Phase 4):**
   * **Data Explorer:** A UI screen to replace Streamlit's market data diagnostics, utilizing the `data_api` endpoints.
   * **Signal Breakdown:** A UI screen to display individual signal scores per symbol, utilizing the `metrics_api` endpoints.
   * **Forecast Viewer:** A UI screen to view multi-horizon forecasts and skill tracker metrics.
   * **Validation Center:** A UI screen for strategy PBO/DSR results.

### Important Architectural Rules to Remember:
- **`api/state_api.py` and `api/control_api.py` have strict AST-based import guards.** Do NOT import heavy engine code into these files.
- **The Streamlit GUI (`gui/`) will be KEPT.** Do not delete it.
- **The SQLite Database will be kept.** Do not attempt a Postgres migration yet (Phase 5 is deferred).
- **Keep everything local.** Do not connect to remote cloud services (unless running standard data fetches).
- Read the main `AGENTS.md` and `GEMINI.md` files for core repository rules.

Good luck!
