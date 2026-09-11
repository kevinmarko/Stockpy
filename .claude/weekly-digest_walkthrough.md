# Weekly Digest Walkthrough (8 Agents)

## What was built
Implemented the "Weekly Digest, Spotify-Style" feature utilizing the exact 8-agent specification requested. The feature surfaces 5 ticker symbols the operator hasn't recently viewed, ranked by Today's Radar score, along with missing sector-gap representation. It delivers this via an out-of-band alert on a weekly cadence and provides an in-app fallback panel.

### Components
1. **Scaffold (`pilots/digest_models.py`)**: Data contract defining `DigestPayload` and `DigestItem` with explicit confidence tiers.
2. **View Tracking Store (`data/symbol_view_store.py`)**: A durable SQLAlchemy table tracking which symbols have been recently viewed, strictly isolated from execution systems.
3. **Sector Gap Composition (`pilots/sector_gap.py`)**: Computes missing fundamental sectors from the currently open paper positions.
4. **Digest Composer (`pilots/weekly_digest.py`)**: Generates a 5-item, 3-tier fallback ladder based on the models scaffolded in WP-0.
5. **Daemon Integration (`desktop/daemon_runtime.py`)**: Incorporates the 168-hour interval dispatcher loop that tracks the last dispatch time.
6. **Delivery Wiring**: Calls `send_alert()` with a week-based `dedup_key` safely handling failures.
7. **Webapp & API (`api/pilots_api.py`, `webapp/`)**: Provides the API endpoint and the PWA fallback user interface under the Marketplace dashboard.
8. **Documentation**: Settings logged in `.env.example` and design synced across `CLAUDE.md` and `AGENTS.md`.

## What was tested
- **Data Models**: Rejection of lists exceeding 5 items tested.
- **View Store Isolation**: Verified isolation using an explicitly targeted memory-only AST test checking codebase rules.
- **Fallback Ladder Logic**: Tested graceful degradation across all 3 tiers.
- **Daemon Scheduling & Dispatch**: Assured correct throttling and swallowed exceptions without crashing.
- **API and UI**: Tested mock and live routing along with front-end React rendering logic.
