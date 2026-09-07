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

## 6-Agent Audit Pass & Fix-Up (same day)

The initial 8-WP build above shipped against an **earlier revision** of the implementation plan than the one that specified universal boolean toggles — it built only the read-only `GET /settings/reference` browse screen. A follow-up 6-agent audit (one agent per dimension: honesty/write-boundary, mock/live parity, field-coverage completeness, promotion-correctness, end-to-end browser verification, docs/test-suite completeness) found this and 7 other real issues, all fixed in the same PR before merge:

1. **Missing write capability (the big one)** — no `PUT`/`PATCH /settings/reference` existed anywhere; `SettingsReference.tsx` had zero `Toggle`/`writable`/`updateSettingsReference` references. Root-caused precisely: the branch was built from `.claude/settings_reference_implementation_plan.md`'s commit `639510ce`, while the universal-toggle requirement was only ever committed on a separate branch (`eb2e94cb` / `settings-reference-plan-v2`) that was never relayed to the building agent. **Fixed**: added `PUT`/`PATCH /settings/reference`, a derived (not hand-listed) `_REFERENCE_WRITE_INDEX` (non-secret, non-`no_op`, boolean `ALLOWED_KEYS` fields), a `writable` field on every `GET` row, real `Toggle` rendering in `SettingsReference.tsx`, and a single-field `ReferenceDangerousConfirmDialog` for `DANGEROUS_KEYS` fields.
2. **Secret `default` not masked** — only the live `value` was masked for a secret field; its compile-time `Field(default=...)` was echoed in the clear (no live leak today since every real secret defaults to `None`/empty, but a latent gap). **Fixed**: `default` is now masked identically to `value`.
3. **Two mock/live `editable_at` mismatches** — `mock.ts` hardcoded `ADVISORY_ONLY` → `/settings/tunables` (real: `/settings/feature-flags`) and `BROKER_BACKEND` → `/settings/feature-flags` (real: `/settings/paper-broker`); both fields belong to more than one editor's group and the mock had the wrong precedence winner. The test file's own inline fixture repeated the same bug. **Fixed** in both `mock.ts` and the test fixture, with the real precedence order verified directly against the running backend (`_EDITABLE_AT_INDEX`) rather than re-guessed.
4. **7 stale/fabricated keys in `pilots/settings_domains.py`'s `_OVERRIDE_DOMAINS`** — `KILLSWITCH_VIX_THRESHOLD`/`KILLSWITCH_SAHM_THRESHOLD`/`KILLSWITCH_OAS_THRESHOLD` (the real fields are the `_AGREED` variants, or don't exist), `DB_ECHO`/`SQLITE_BUSY_TIMEOUT_MS`/`SQLITE_WAL_AUTOCHECKPOINT` (fabricated, appear nowhere in the repo), `MCP_OAUTH_USERS` (real field is `MCP_OAUTH_MULTI_USER_ENABLED`). Harmless today (the lookup only ever queries real `Settings.model_fields` keys) but incorrect. **Fixed**: removed, plus a new hygiene test (`TestSettingsDomainsHygiene`).
5. **Once fields could be promoted to write, the no_op exclusion needed re-deriving for the new endpoint too** — the original `_REFERENCE_WRITE_INDEX` design (boolean + `ALLOWED_KEYS` only) would have made the dead `OPTIONS_EARNINGS_CRUSH_ENABLED` report `writable: true`, showing a live-looking Toggle for a field that does nothing on write — the exact trap the Options Desk Automation promotion was already careful to avoid. **Fixed**: the write index (and the mock's mirror of it) also excludes `docs/settings_liveness.json`'s `no_op` bucket.
6. **Two stale committed artifacts** — `docs/settings_liveness.json` and `docs/settings_field_census.json`/`.md` weren't regenerated after `settings.py`'s description backfill and `api/pilots_api.py`'s growth shifted line numbers referenced by their own freshness tests (`tests/test_settings_liveness.py`, `tests/test_measure_settings_census.py`). **Fixed**: regenerated via `python3 scripts/settings_liveness.py --write` and `python3 scripts/measure_settings_census.py --write`.
7. **Hardcoded `89` in `tests/test_settings_keysets.py`** — `_TUNABLE_INDEX`'s real size grew to 114 (the two new groups + 6 fold-ins = +25) but this test's expected count and the `ALL_EDITOR_KEYS` total (184 → 209) were never updated; it would have failed under `make verify`/a full suite run despite the walkthrough's original "done" framing. **Fixed**, with a dated explanatory comment matching this file's own established convention for prior size bumps.
8. **This walkthrough/CLAUDE.md's own wrong count** — the original CLAUDE.md bullet claimed "bringing the total tunable fields to 157," which didn't correspond to any real quantity (the actual `_TUNABLE_INDEX` size is 114). **Fixed** in `CLAUDE.md`/`AGENTS.md` (auto-synced).

Two new test classes were added specifically to prevent items 1 and 5 from silently regressing: `TestReferenceWriteIndexDerivation` (asserts the write index is a fresh recomputation, not frozen; asserts it excludes every secret/excluded/non-boolean/`no_op` key) and `TestSettingsReferenceWrite` (exercises the actual `PUT` endpoint: auth gating, ordinary writes, non-boolean/secret/no_op rejection as `unknown_key`, and the full dangerous-confirmation flow — required/mismatch/success). A per-group field-membership test was also added for the two new Tunables groups (`tests/test_pilots_api_tunables.py`), closing a gap the original audit flagged (the existing flat-index/group-name checks wouldn't have caught a field placed in the wrong group).

### A 9th bug, found only by an actual live browser check (none of the 6 audit agents caught this)

After applying the 8 fixes above, `npm run dev` (via `npx vite --port 5183` manually, since port 5173 was occupied by an unrelated process) plus a real click-through in the Browser pane found: searching for `OPTIONS_EARNINGS_CRUSH_ENABLED` on `/settings/reference` in mock mode showed an interactive Toggle switch and an "Applies now" badge — for a field that's supposed to be excluded from writability entirely. **Root cause**: `webapp/src/api/mock.ts`'s `mockLiveness()` derives a field's liveness from a small hardcoded `MOCK_DEMO_ONLY_STATES` override map (for `env_pinned`/`no_effect`, which the generic classification can't otherwise produce); `OPTIONS_EARNINGS_CRUSH_ENABLED` had no entry there, so it fell through to the generic live_safe path. **Fixed**: added `OPTIONS_EARNINGS_CRUSH_ENABLED: "no_effect"` to the map (with a comment distinguishing it from the map's other two entries, which are deliberately fictional demo-only states, not real ones). Re-verified live in the browser after the fix: the field now shows a "No effect" badge, the "not read anywhere" warning, and correctly renders **no toggle at all**.

A regression test (`SettingsReference.test.tsx`) was added calling the REAL `mockApi.getSettingsReference()` directly rather than a hand-rolled inline fixture, specifically to close the exact gap the original mock/live-parity audit agent flagged (its 3 tests all mocked `api.getSettingsReference()`, which can never catch a bug living inside `mock.ts` itself). **That test's own first draft was itself broken and silently passed against the unfixed bug**: this file's shared `beforeEach` calls `vi.spyOn(api, "getSettingsReference")`, and since `webapp/src/api/client.ts` exports `api = USE_MOCK ? mockApi : liveApi` as the literal SAME object reference (not a wrapper), that spy clobbers `mockApi.getSettingsReference` for every test in the describe block — including this new one, which therefore always read the OTHER tests' hand-rolled `mockResponse` fixture instead of the real module. Caught by manually reverting the mock.ts fix and confirming the "new" test still passed (it did — proving it was broken); fixed by adding an explicit `vi.restoreAllMocks()` inside the test body before calling the real `mockApi`, then re-verified the full revert/fix cycle a second time to confirm the test now genuinely fails against the bug and passes against the fix.

**Lesson, stated plainly**: an automated 6-agent audit — even one that includes a dedicated end-to-end browser-verification agent — is not a substitute for actually clicking through the fixed feature after the fixes land. The E2E audit agent (dimension 5) ran against the ORIGINAL (pre-fix) branch, before any of these 8 fixes existed, and had no reason to exercise a toggle that didn't exist yet. Once real code changes, an actual re-verification pass — not just re-running the existing test suite — is what caught this.

## Verification Results (after the fix pass)

### Backend Python Suite
```
pytest tests/test_settings_reference.py tests/test_pilots_api_tunables.py tests/test_feature_flags_registry.py \
       tests/test_gui_env_io.py tests/test_settings_keysets.py tests/test_measure_settings_census.py \
       tests/test_settings_liveness.py -q
249 passed, 1 skipped, 1 warning in 10.86s
```

### Full backend suite (offline, non-network)
```
NUMBA_CACHE_DIR=<writable tmp dir> pytest -q -p no:randomly -m "not network" --ignore=tests/test_sizing_properties.py
12902 passed, 16 failed, 34 skipped, 95 deselected in 439.85s
```
The 16 failures are pre-existing, confirmed unrelated to this change (verified: none of the 16 failing test files import `api.pilots_api`, `pilots.settings_domains`, or reference `_REFERENCE_WRITE_INDEX`) — they're network/socket-sandbox restrictions in this environment (bounded-timeout tests needing a real blocking socket, live chat websockets, free-port binding) that reproduce identically against the unmodified base branch. `tests/test_sizing_properties.py` was excluded from this run (missing `hypothesis` package in this environment, also pre-existing and unrelated).

### Frontend TypeScript & Vitest Suite
```
npm run --prefix webapp typecheck
> tsc --noEmit
Exit code 0

npx vitest run
Test Files  177 passed (177)
     Tests  1962 passed (1962)
   Duration  32.36s
```
(1962 = the original 1957 + 5 new: 8 total in `SettingsReference.test.tsx` up from 3 — Toggle rendering, ordinary-field write + reload, dangerous-field confirm flow, cancel-never-writes, and the real-mock-module no_op regression check documented above.)
