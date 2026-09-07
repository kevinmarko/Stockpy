# Task tracker: Settings & Flags Audit → Fix Drift, Build Settings Reference Explainer, Expose Promoted Subset

- [x] **WP1: Fix Feature Flags mock/live parity bug**
  - [x] Added 11 missing feature flags + `MULTI_BROKER_GATEWAY_ENABLED` to `FEATURE_FLAGS_TUNABLE_DEFS` in `webapp/src/api/mock.ts`.
  - [x] Added regression test `test_mock_ts_feature_flags_parity` in `tests/test_feature_flags_registry.py`.
- [x] **WP2: Wire up orphaned TabGuide + add missing entries**
  - [x] Added `tabGuideKey?: string` prop to `GenericSettingsEditor.tsx` rendering `<TabGuide>`.
  - [x] Wired `tabGuideKey` to all 8 settings wrapper screens (`FeatureFlagsScreen`, `SettingsManager`, `SettingsCacheLongShort`, `SettingsPaperBroker`, `SentimentSettings`, `SectorSelectionSettings`, `FmpSettings`, `EtfTransmissionSettings`).
  - [x] Added glossary terms (`"liveness"`, `"settings reference"`) and 8 `TAB_HELP` entries in `webapp/src/help/helpContent.ts`.
  - [x] Verified `webapp/src/help/helpContent.test.ts` (13/13 passing).
- [x] **WP3: Backend `GET /settings/reference` endpoint**
  - [x] Created `pilots/settings_domains.py` mapping all 464 `settings.py` fields across 14 domains without heavy engine imports.
  - [x] Backfilled 22 missing `Field(description=...)` entries in `settings.py` (100% of 464 fields documented).
  - [x] Implemented `_build_editable_at_index()` and `GET /settings/reference` in `api/pilots_api.py`.
  - [x] Masked secrets as `"•••• (set)"` / `"(not set)"` (fail-closed, never leaked).
  - [x] Sourced runtime liveness metadata directly from `pilots/settings_meta.py::field_metadata()`.
- [x] **WP4: Backend Options Desk Automation Tunables group**
  - [x] Added "Options Desk Automation" group (13 fields) to `_TUNABLE_GROUPS` in `api/pilots_api.py`.
  - [x] Excluded `OPTIONS_EARNINGS_CRUSH_ENABLED` (confirmed `no_op`).
- [x] **WP5: Backend Circuit Breaker group + misc fold-ins**
  - [x] Added "Circuit Breaker" group (6 fields) to `_TUNABLE_GROUPS` in `api/pilots_api.py`.
  - [x] Bounded `CIRCUIT_BREAKER_OFI_THRESHOLD` with upper bound `10000.0`.
  - [x] Folded 6 fields into "Financial Constants" and "Runtime & Ops".
  - [x] Added `MULTI_BROKER_GATEWAY_ENABLED` to `pilots/feature_flags.py::WRITE_GATE_REASONS`.
- [x] **WP6: Frontend Settings Reference screen**
  - [x] Added `SettingsReferenceField` and `SettingsReferenceResponse` to `webapp/src/api/types.ts`.
  - [x] Added `getSettingsReference()` to `client.ts` (`liveApi`) and `mock.ts` (`mockApi`).
  - [x] Built representative multi-domain mock fixture in `mock.ts`.
  - [x] Created `webapp/src/screens/SettingsReference.tsx` at `/settings/reference` with search, domain filter dropdown, secret masking, liveness badges, and "Edit here →" navigation.
  - [x] Added `SettingsReferenceLink` card to `webapp/src/screens/SettingsModules.tsx`.
  - [x] Registered route in `webapp/src/App.tsx`.
- [x] **WP7: Frontend mock parity for promoted fields + tests**
  - [x] Updated `TUNABLE_DEFS` in `mock.ts` with all ~25 newly promoted fields.
  - [x] Created `webapp/src/screens/SettingsReference.test.tsx` (3 tests).
  - [x] Updated `SettingsModules.test.tsx` and `SettingsPaperBroker.test.tsx` mocks.
  - [x] Verified `npm run --prefix webapp typecheck` clean (0 errors).
  - [x] Verified all 177 webapp Vitest test suites (1957 tests) passing.
- [x] **WP8: Documentation + backend tests**
  - [x] Created `tests/test_settings_reference.py` with 7 comprehensive tests (auth, secret masking, 464-key completeness, editable_at, no-op guardrail).
  - [x] Updated `tests/test_pilots_api_tunables.py` and `tests/test_feature_flags_registry.py` (all 150 tests passing).
  - [x] Updated `docs/architecture/webapp-and-gui.md`.
  - [x] Updated `docs/HOW_TO_GUIDE.md`.
  - [x] Updated `CLAUDE.md` and synced to `AGENTS.md`.

## 6-Agent Audit Pass & Fix-Up (same day, before merge)

- [x] **Audit finding: missing `PUT`/`PATCH /settings/reference` write endpoint** — the branch above was built against an earlier plan revision (`639510ce`) than the one specifying universal boolean toggles (`eb2e94cb`). **Fixed**:
  - [x] Added derived (not hand-listed) `_REFERENCE_WRITE_INDEX` in `api/pilots_api.py` (non-secret, non-`no_op`, boolean `ALLOWED_KEYS` fields).
  - [x] Added `PUT`/`PATCH /settings/reference`, reusing `_validate_and_write_payload()` (same `DANGEROUS_KEYS` confirmation gate as every other editor).
  - [x] Added `writable: boolean` to every `GET /settings/reference` field.
  - [x] Added `SettingsReferenceUpdateRequest` request model.
  - [x] Added `writable?: boolean` to `TunableField` in `webapp/src/api/types.ts`; `writable: boolean` on `SettingsReferenceField`.
  - [x] Added `api.updateSettingsReference()` in `client.ts` and `mock.ts` (with real `applySettingsReference()` localStorage-backed persistence + dangerous-confirm gate mirroring `applyTunablesGeneric`).
  - [x] Added real `Toggle` rendering in `SettingsReference.tsx` for `field.writable` rows.
  - [x] Added `ReferenceDangerousConfirmDialog` (single-field typed-name confirmation, sibling to `GenericSettingsEditor.tsx`'s batch `DangerousConfirmDialog`).
  - [x] Added `TestReferenceWriteIndexDerivation` and `TestSettingsReferenceWrite` test classes (12 new tests) to `tests/test_settings_reference.py`.
  - [x] Added 4 new webapp tests to `SettingsReference.test.tsx` (Toggle rendering, ordinary write+reload, dangerous confirm flow, cancel-never-writes).
- [x] **Audit finding: secret `default` not masked** — fixed in `GET /settings/reference`; regression test `test_secret_default_masked_too` added.
- [x] **Audit finding: 2 mock/live `editable_at` mismatches (`ADVISORY_ONLY`, `BROKER_BACKEND`)** — fixed in `mock.ts` and the test file's inline fixture; `test_editable_at_mapping` tightened from a loose `in (...)` to the pinned, verified-against-the-real-backend value.
- [x] **Audit finding: 7 stale/fabricated keys in `pilots/settings_domains.py::_OVERRIDE_DOMAINS`** — removed; hygiene test `TestSettingsDomainsHygiene` added.
- [x] **Fixed the no_op-write trap this same fix pass introduced**: the first-draft `_REFERENCE_WRITE_INDEX` design would have made `OPTIONS_EARNINGS_CRUSH_ENABLED` `writable: true` — excluded `no_op` keys from the write index (and mirrored the exclusion in `mock.ts`) before this ever shipped.
- [x] **Audit finding: 2 stale committed artifacts** — regenerated `docs/settings_liveness.json` (`scripts/settings_liveness.py --write`) and `docs/settings_field_census.json`/`.md` (`scripts/measure_settings_census.py --write`).
- [x] **Audit finding: hardcoded `89` in `tests/test_settings_keysets.py`** — updated to `114`, `ALL_EDITOR_KEYS` total `184` → `209`, with a dated explanatory comment.
- [x] **Audit finding: CLAUDE.md's own wrong "157" tunable-field count** — corrected to `114` in `CLAUDE.md`/`AGENTS.md` (auto-synced), plus a full rewrite of the bullet disclosing the write-endpoint addition and every fix above.
- [x] Added per-group field-membership tests for "Options Desk Automation"/"Circuit Breaker" to `tests/test_pilots_api_tunables.py` (closes a gap the audit flagged: the pre-existing flat-index/group-name checks wouldn't catch a field placed in the wrong group).
- [x] Re-ran targeted suite: 249 passed, 1 skipped.
- [x] Ran the FULL backend suite (not just the targeted files) for the first time on this branch: 12902 passed, 16 failed (all pre-existing/environment-caused — network/socket sandbox restrictions, confirmed unrelated by file-reference check — see walkthrough for detail), 34 skipped.
- [x] Re-ran full webapp suite: 177 files / 1961 tests passing (was 1957; +4 new toggle-flow tests).
- [x] `npm run --prefix webapp typecheck`: clean.

## 9th finding: caught only by an actual live browser check, after the 6-agent audit's own fixes landed

- [x] Ran a real `npx vite` dev server + Browser-pane click-through of `/settings/reference` (mock mode) after applying the 8 fixes above — none of the 6 audit agents had exercised the FIXED code live (the E2E audit agent ran before these fixes existed).
- [x] Found: `OPTIONS_EARNINGS_CRUSH_ENABLED` rendered an interactive Toggle + "Applies now" badge instead of "No effect" + no toggle. Root cause: `webapp/src/api/mock.ts::mockLiveness()`'s `MOCK_DEMO_ONLY_STATES` override map had no entry for this key.
- [x] Fixed: added `OPTIONS_EARNINGS_CRUSH_ENABLED: "no_effect"` to `MOCK_DEMO_ONLY_STATES`. Re-verified live in the browser: correct badge, correct warning, no toggle.
- [x] Added a regression test calling the REAL `mockApi.getSettingsReference()` (not a hand-rolled fixture) to `SettingsReference.test.tsx`.
- [x] **Caught that the new test's own first draft was broken**: the file's shared `beforeEach` spy on `api.getSettingsReference` also clobbers `mockApi.getSettingsReference` (same object reference per `client.ts`'s `api = USE_MOCK ? mockApi : liveApi`), so the "new" test always read the OTHER tests' fixture, not the real module — proved this by reverting the mock.ts fix and confirming the test still passed (a false pass). Fixed by adding `vi.restoreAllMocks()` inside the test body.
- [x] Re-verified the full revert → confirm-fails → restore → confirm-passes cycle a second time against the corrected test.
- [x] Final re-run: backend targeted suite 249 passed/1 skipped; webapp typecheck clean; webapp full suite 177 files / 1962 tests passing.
- [x] Updated `CLAUDE.md`/`AGENTS.md` (auto-synced), walkthrough, and this task tracker to disclose the 9th finding.
