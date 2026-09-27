# Options desk cut — step 3e walkthrough (API surface)

Part of the shrink-in-place plan, step 3 (PR 0 → 3a → 3b → 3c → 3d → 3d′ → **3e** → 3f).

## What changed
- Removed every options-desk HTTP route from `api/pilots_api.py`, `api/data_api.py`,
  `api/metrics_api.py` and `api/ws_api.py`:
  - `/pilots/options/*`
  - `/pilots/paper-broker/{strategy-options/*, greeks, manage-exits, roll, delta-hedge/*, scenario-matrix, settle-expired}`
  - `/pilots/ai/*`, `/pilots/portfolio/optimize/hrp-cvar`
  - `/pilots/execution/{optimize/almgren-chriss, fix/*, brokers/*}`
  - `/brokerage/options/order`, `GET /options`, `GET /symbols/{ticker}/options`
  - `/data/options/{recompute, chain/{symbol}}`, `/metrics/options/*`
  - the `/ws/risk/portfolio` websocket
- Request models, helpers and imports used only by those routes were removed with them.
- **Kept:** `GET /pilots/execution/pending`, `POST /pilots/execution/{token}/{approve,reject}`
  (agentic Robinhood live-trade approval) and the equity `POST /pilots/paper-broker/order`.
- Retired two settings that had no remaining reader: `FIX_GATEWAY_ENABLED` (dropped from
  `pilots/feature_flags.py`) and `WS_RISK_STREAM_INTERVAL_SECONDS` (dropped from
  `pilots/settings_domains.py`). Both fields stay in `settings.py`, with their descriptions
  marked retired, until the settings trim in step 4.
- `webapp/src/api/mock.ts`: the FIX feature-flag mock was removed and `MOCK_CAPTURE_SITES` was re-synced.
  The rest of the webapp surface is removed in step 3f.
- Regenerated `docs/settings_liveness.json` and `docs/settings_field_census.{json,md}`.
- Tests:
  - Deleted 4 files that only exercised removed routes.
  - Removed the route-level cases from 8 others.
  - Kept the pure-module unit tests for the underlying modules, which stay until step 4.

## Verification
- Offline suite: 13023 passed, 15 skipped, 0 failed.
- `ruff --select=F821,F822,F823,E9`: clean.
- Webapp `tsc --noEmit`: clean. `vitest src/api`: 98 passed.
