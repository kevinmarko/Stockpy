# Walkthrough: Settings & Flags Audit → Fix Drift, Build Settings Reference Explainer, Expose Promoted Subset

## What Changed and Why

This change addresses a platform-wide settings audit finding: while `settings.py` (464 fields) is 100% governed across `ALLOWED_KEYS`, `SECRET_KEYS`, and `EXCLUDED_FROM_GUI`, only ~229 fields were accessible via the Pilots PWA, and there was no centralized directory to search, inspect descriptions, or understand runtime liveness for the remaining ~235 non-secret fields. Additionally, several mock drift and UI help gaps were found and resolved.

## Work Packages Completed

1. **WP1: Fix Feature Flags mock/live parity bug**:
   - `webapp/src/api/mock.ts`: Added 11 missing feature flags (`OFI_SHIELD_ENABLED`, `BROKER_BACKEND`, `LIVE_TRADE_EXECUTION_ENABLED`, `LIVE_TRADE_APPROVAL_ENABLED`, `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`, `MCP_OAUTH_MULTI_USER_ENABLED`, `FORECAST_BACKFILL_ENABLED`, `JULES_ENABLED`, `PAPER_BROKER_WRITES_ENABLED`, `FIX_GATEWAY_ENABLED`, `PIPELINE_STALL_ALERT_ENABLED`) plus `MULTI_BROKER_GATEWAY_ENABLED` to `FEATURE_FLAGS_TUNABLE_DEFS`.
   - `tests/test_feature_flags_registry.py`: Added `test_mock_ts_feature_flags_parity` asserting `webapp/src/api/mock.ts` contains every flag registered in `pilots/feature_flags.py`.

2. **WP2: Contextual Help & TabGuide Integration**:
   - `webapp/src/components/GenericSettingsEditor.tsx`: Added `tabGuideKey?: string` prop rendering `<TabGuide tabKey={tabGuideKey} />` above settings form sections.
   - Wired `tabGuideKey` across all 8 settings wrapper screens (`FeatureFlagsScreen`, `SettingsManager`, `SettingsCacheLongShort`, `SettingsPaperBroker`, `SentimentSettings`, `SectorSelectionSettings`, `FmpSettings`, `EtfTransmissionSettings`).
   - `webapp/src/help/helpContent.ts`: Added glossary definitions for `"liveness"` and `"settings reference"`, and authored 8 `TAB_HELP` entries with relevant glossary term chips.

3. **WP3: Backend `GET /settings/reference` Endpoint**:
   - `pilots/settings_domains.py`: Created domain mapper grouping all 464 fields into 14 distinct functional domains (Core Runtime, Financial Constants, Risk & Circuit Breaker, Execution & Brokers, Market Data, Options Desk, Forecasting & ML, Valuation, Sentiment & News, ETF Transmission, AI & LLM, Alerting & Observability, Orchestration & Jobs, Filesystem & Bootstrap).
   - `settings.py`: Backfilled 22 missing `Field(description=...)` entries so 100% of the 464 fields carry human-readable explanations.
   - `api/pilots_api.py`: Implemented `_build_editable_at_index()` and `GET /settings/reference`. Masks secrets as `"•••• (set)"` or `"(not set)"` (fail-closed, never leaked), surfaces liveness metadata (`applies`, `restart_required`, `capture_sites`) from `pilots/settings_meta.py`, and maps `editable_at` routes.

4. **WP4: "Options Desk Automation" Tunables Group**:
   - Promoted 13 actively-used paper automation toggles to `_TUNABLE_GROUPS` in `api/pilots_api.py` (`PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`, `OPTIONS_AUTO_EXIT_ENABLED`, `OPTIONS_PROFIT_TARGET_PCT`, `OPTIONS_STOP_LOSS_MULTIPLE`, `OPTIONS_MANAGE_DTE_THRESHOLD`, `OPTIONS_DELTA_HEDGE_ENABLED`, `OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES`, `OPTIONS_0DTE_ENABLED`, `OPTIONS_0DTE_PROFIT_TARGET_PCT`, `OPTIONS_0DTE_STOP_LOSS_PCT`, `OPTIONS_0DTE_HARD_EXIT_TIME`, `MAX_OPTION_NOTIONAL_PER_TRADE`, `MAX_CONCURRENT_OPTION_POSITIONS`).
   - Excluded `OPTIONS_EARNINGS_CRUSH_ENABLED` (confirmed dead `no_op` in `docs/settings_liveness.json`).

5. **WP5: "Circuit Breaker" Tunables Group & Fold-Ins**:
   - Added "Circuit Breaker" group (6 fields) to `_TUNABLE_GROUPS` (`CIRCUIT_BREAKER_ENABLED`, `CIRCUIT_BREAKER_VOLATILITY_Z_THRESHOLD`, `CIRCUIT_BREAKER_VPIN_THRESHOLD`, `CIRCUIT_BREAKER_OFI_THRESHOLD`, `CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS`, `CIRCUIT_BREAKER_REFERENCE_SYMBOL`).
   - Folded 6 fields into "Financial Constants" and "Runtime & Ops" groups (`MULTIFACTOR_MICROCAP_THRESHOLD`, `CORRELATION_CLUSTER_LOOKBACK_DAYS`, `CORRELATION_CLUSTER_THRESHOLD`, `FEATURE_DRIFT_PSI_ENABLED`, `DAEMON_SHUTDOWN_TIMEOUT_SECONDS`, `PIPELINE_STALL_ALERT_SECONDS`).
   - Added `MULTI_BROKER_GATEWAY_ENABLED` to `pilots/feature_flags.py::WRITE_GATE_REASONS`.

6. **WP6: Frontend Settings Reference Screen**:
   - `webapp/src/api/types.ts`: Added `SettingsReferenceField` and `SettingsReferenceResponse`.
   - `webapp/src/api/client.ts`: Added `getSettingsReference()` in `liveApi` and `mockApi`.
   - `webapp/src/screens/SettingsReference.tsx`: Built searchable, domain-filtered UI at `/settings/reference` with secret masking, liveness badges, domain counts, and "Edit here →" navigation.
   - `webapp/src/screens/SettingsModules.tsx`: Added link card with total count and domain metrics.
   - `webapp/src/App.tsx`: Registered `/settings/reference` route.

7. **WP7: Frontend Parity & Component Tests**:
   - `webapp/src/api/mock.ts`: Added representative multi-domain fixture `mockSettingsReference()` and updated `TUNABLE_DEFS` for promoted settings.
   - `webapp/src/screens/SettingsReference.test.tsx`: 3 unit tests verifying rendering, search filtering, domain filtering, secret masking, and navigation links.
   - Fixed mocks in `SettingsModules.test.tsx` and `SettingsPaperBroker.test.tsx`.

8. **WP8: Documentation & Backend Tests**:
   - `tests/test_settings_reference.py`: 7 tests verifying fail-open when token unset, 401 on bad token, 464-field completeness, secret masking, `editable_at` routing, 100% description presence, and a guardrail asserting no `no_op` field is present in `_TUNABLE_GROUPS`.
   - Updated `tests/test_pilots_api_tunables.py` and `tests/test_feature_flags_registry.py`.
   - Updated `docs/architecture/webapp-and-gui.md`, `docs/HOW_TO_GUIDE.md`, `CLAUDE.md`, and synced `AGENTS.md`.

## Verification Results

### Backend Python Suite
```
pytest tests/test_settings_reference.py tests/test_pilots_api_tunables.py tests/test_feature_flags_registry.py tests/test_gui_env_io.py -v
================== 150 passed, 1 skipped, 1 warning in 1.53s ===================
```

### Frontend TypeScript & Vitest Suite
```
npm run --prefix webapp typecheck
> tsc --noEmit
Exit code 0

./node_modules/.bin/vitest run
Test Files  177 passed (177)
     Tests  1957 passed (1957)
   Start at  18:47:12
   Duration  38.15s
```
