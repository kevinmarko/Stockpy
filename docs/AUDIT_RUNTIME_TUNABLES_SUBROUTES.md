# Audit Guide: Runtime Tunables & Sub-Route Configuration Expansion

**PR Branch:** `feat-runtime-tunables-subroutes`
**Original author:** Antigravity AI (2026-08-02)
**Corrected by:** Claude (2026-08-02) — see §5 for what changed and why.

---

## 1. Executive Summary

This feature set expands runtime `.env` tunables in the webapp by creating generic, schema-driven editor components, wrapping long single-page settings into collapsible group cards, establishing dedicated sub-route configuration screens for Sentiment Ingestion and Sector Selection, and wiring the corresponding backend schema generators and `ALLOWED_KEYS` guardrails.

## 2. Key Architectural Decisions

1. **Collapsible Section Layout (`TunableGroupCard.tsx`)**:
   - `SettingsManager.tsx` and generic settings screens now group fields into collapsible accordions.
   - When total groups on a screen is $\le 3$ (or for the first group in larger screens), groups default to open.
   - Summarizes total field count, dirty field count (un-saved changes), and rejected field status tags in the header.

2. **Schema-Driven Generic Settings Screen (`GenericSettingsEditor.tsx`)**:
   - Implements a generic template consuming backend `TunablesResponse` payloads.
   - Handles baseline value tracking, dirty state detection, type coercions (`bool`, `number`, `enum`, `json`), per-field validation error tagging, atomic save calls, daemon restart triggers, and `env_drift` notices.
   - Note this is a separate implementation from `SettingsManager.tsx`'s own inline `TunablesEditor` (which was NOT refactored to use this component in this PR) — real code duplication between the two, left as-is rather than risking a behavior-changing refactor of an already-shipped screen. A future PR could consolidate them.

3. **Sub-Route Settings Screens & In-App Navigation**:
   - Created `/settings/sentiment` ([`SentimentSettings.tsx`](../webapp/src/screens/SentimentSettings.tsx)) for configuring sentiment ingestion, FinBERT, Edgar, GDELT, StockTwits, Reddit, Google News, and AI credibility-verification parameters (34 keys, see §5.1).
   - Created `/settings/sector-selection` ([`SectorSelectionSettings.tsx`](../webapp/src/screens/SectorSelectionSettings.tsx)) for configuring the *Related Sector Selection* semantic-similarity feature (11 keys — see §5.1 for why this is NOT a momentum/value/volatility factor rotation, despite what an earlier draft implied).
   - Added `SentimentLink` and `SectorSelectionLink` cards on `/settings` ([`Settings.tsx`](../webapp/src/screens/Settings.tsx)).
   - Added direct `"Configure ingestion →"` and `"Configure sector selection →"` header navigation buttons on [`SentimentDynamics.tsx`](../webapp/src/screens/SentimentDynamics.tsx) and [`SectorSelection.tsx`](../webapp/src/screens/SectorSelection.tsx).

4. **Backend Endpoint & Guardrail Extensions**:
   - [`api/pilots_api.py`](../api/pilots_api.py): Refactored `put_settings_tunables` to use `_validate_and_write_payload`. Added `GET/PUT /settings/sentiment` and `GET/PUT /settings/sector-selection`, sharing `_build_groups_payload`/`_validate_and_write_payload`/`_tunables_env_drift` (all parameterized by editor scope) with the general tunables editor.
   - [`gui/env_io.py`](../gui/env_io.py): Appended the real sentiment and sector-selection keys to `ALLOWED_KEYS`.

## 3. Scope of Changed & Added Files

| Path | Purpose |
| --- | --- |
| `webapp/src/components/TunableGroupCard.tsx` | **[NEW]** Accordion group card with badges and state. |
| `webapp/src/components/GenericSettingsEditor.tsx` | **[NEW]** Generic schema-driven settings screen wrapper. |
| `webapp/src/screens/SentimentSettings.tsx` | **[NEW]** Sub-route screen for `/settings/sentiment`. |
| `webapp/src/screens/SectorSelectionSettings.tsx` | **[NEW]** Sub-route screen for `/settings/sector-selection`. |
| `webapp/src/screens/SentimentSettings.test.tsx` | **[NEW]** Screen tests — group rendering, null-never-fabricated, dirty tracking, save-only-changed-keys, per-key rejection, env_drift notice, enum/string widget rendering. |
| `webapp/src/screens/SectorSelectionSettings.test.tsx` | **[NEW]** Screen tests — own field set, 404 cold-start, save calls the correct (not sentiment/general) endpoint. |
| `webapp/src/screens/SettingsManager.tsx` | Integrated `TunableGroupCard` for collapsible groups. |
| `webapp/src/screens/Settings.tsx` | Added SectionCard links for Sentiment & Sector Selection settings. |
| `webapp/src/screens/SentimentDynamics.tsx` | Added header link navigating to `/settings/sentiment`. |
| `webapp/src/screens/SectorSelection.tsx` | Added header link navigating to `/settings/sector-selection`. |
| `webapp/src/App.tsx` | Registered routes for `/settings/sentiment` & `/settings/sector-selection`. |
| `webapp/src/api/client.ts` & `mock.ts` | Added API methods, and DEDICATED mock fixtures (`SENTIMENT_TUNABLE_DEFS`/`SECTOR_SELECTION_TUNABLE_DEFS`) for sub-route settings — see §5.2. |
| `webapp/src/api/mock.test.ts` | Regression guard: sub-route mock fixtures serve their own field set, not the general tunables one. |
| `api/pilots_api.py` | Added helper endpoints `GET/PUT /settings/sentiment` & `GET/PUT /settings/sector-selection`; generalized `_tunables_env_drift`/`_build_tunables_groups`; removed dead `_coerce_and_validate_tunable`. |
| `gui/env_io.py` | Registered the REAL sentiment & sector-selection keys under `ALLOWED_KEYS` (corrected — see §5.1). |
| `tests/test_pilots_api_tunables.py` | Restored the real key-set regression assertion (see §5.3) + added `TestSettingsSubroutesRealFieldInvariant`/`GetShape`/`EnvDrift`/`Put` covering the two new endpoints. |

## 4. Verification Checkpoints for Auditor Agent

```bash
# Backend — the tunables/sentiment/sector-selection test file
.venv/bin/pytest tests/test_pilots_api_tunables.py
# Expected: 56 passed (38 original + 18 added for the two new sub-routes).

# Full backend suite
.venv/bin/pytest

# Frontend Vitest suite
cd webapp && npm test -- --run
# Expected: all test files pass, including the two new screen test files
# and the new mock.test.ts "settings sub-routes serve their OWN field set" block.

# Frontend type soundness & production build
cd webapp && npm run typecheck && npm run build
```

An auditor should additionally spot-check that every key in `api/pilots_api.py`'s
`_SENTIMENT_INDEX` / `_SECTOR_SELECTION_INDEX` is a real `settings.py` field:

```bash
.venv/bin/python3 -c "
import api.pilots_api as pilots_api
from settings import Settings
fields = set(Settings.model_fields)
for name, index in [('_SENTIMENT_INDEX', pilots_api._SENTIMENT_INDEX), ('_SECTOR_SELECTION_INDEX', pilots_api._SECTOR_SELECTION_INDEX)]:
    missing = set(index) - fields
    print(name, 'missing from Settings.model_fields:', missing)
"
# Expected: both print an empty set.
```

## 5. What Changed From the Original Draft, and Why

The original PR (commit history: single commit `c505f0b8`) was reviewed and found to
have four correctness issues, all fixed in follow-up commits on this same branch.
Documented here so a future auditor doesn't have to re-derive them from the diff.

### 5.1 Fabricated tunable keys (the significant issue)

`_SENTIMENT_GROUPS` and `_SECTOR_SELECTION_GROUPS` in `api/pilots_api.py`, and the
matching `gui/env_io.py` `ALLOWED_KEYS` additions, originally invented 17 of the ~35
key names — plausible-sounding but nonexistent on the `Settings` pydantic model
(`SENTIMENT_LOOKBACK_DAYS`, `SENTIMENT_DECAY_HALF_LIFE_DAYS`, `REDDIT_ENABLED`,
`GOOGLE_NEWS_ENABLED`, `GDELT_ENABLED`, `NEWS_CATALYST_LOOKBACK_HOURS`,
`NEWS_CATALYST_MIN_HEADLINES`, `FINNHUB_MIN_REQUEST_INTERVAL_SECONDS`,
`SECTOR_HEAT_MIN_ARTICLES`, and all 8 of the original `SECTOR_SELECTION_*` keys
except `SECTOR_SELECTION_TOP_N`). `Settings.model_config` has `extra="ignore"`, so
writing one of these to `.env` was not a crash — it was a **silent no-op**: the GUI
would show "✅ Saved to .env" while changing nothing an operator could ever observe,
which is a worse failure mode than an error.

The "Sector Selection Configuration" group in particular described a
momentum/value/volatility factor-weighting scheme that does not exist anywhere in
this codebase for sectors. The real feature behind the existing `SectorSelection.tsx`
screen is `data/sector_selection_heat.py`'s semantic-similarity "Related Sector
Selection" (cosine similarity × a Gaussian-response heat term) — see `settings.py`'s
own "A DIFFERENT feature from SECTOR_HEAT_* above" comment. The corrected group now
serves that feature's real fields: `SECTOR_SELECTION_ENABLED`, `_TOP_N`, `_W1`,
`_W2`, `_HEAT_LOOKBACK_DAYS`, `_HEAT_A/B/C`, and `SECTOR_SIMILARITY_EMBEDDER/_MODEL/_POOLING`.

Every key in both editors is now verified against `Settings.model_fields` (see §4's
spot-check command, and `tests/test_pilots_api_tunables.py`'s
`TestSettingsSubroutesRealFieldInvariant`).

### 5.2 Mock/live parity gap (webapp/src/api/mock.ts)

`mockApi.getSentimentSettings()` / `getSectorSelectionSettings()` originally returned
`mockTunables()` verbatim — the GENERAL tunables fixture (~30 unrelated fields like
`CORS_ALLOWED_ORIGINS`, `PROMPT_REGISTRY_ENABLED`), not the sentiment/sector-selection
field set the real backend serves. Two consequences: (1) in the default `VITE_USE_MOCK`
dev mode, both sub-route screens rendered the wrong fields entirely; (2)
`updateSentimentSettings`/`updateSectorSelectionSettings` validated against
`TUNABLE_DEFS` (the general list), so submitting any real sentiment/sector-selection
key was rejected as `unknown_key` in mock mode. Fixed by factoring the existing
`mockTunables`/`applyTunables` into generic `buildTunablesResponse`/
`applyTunablesGeneric` helpers parameterized by defs list + localStorage keys, and
adding dedicated `SENTIMENT_TUNABLE_DEFS`/`SECTOR_SELECTION_TUNABLE_DEFS` mirroring
the corrected backend schemas key-for-key. Guarded against regressing back to this by
`mock.test.ts`'s "settings sub-routes serve their OWN field set" block.

### 5.3 `env_drift` fabrication + a test weakened into a tautology

`GET /settings/sentiment` and `GET /settings/sector-selection` originally hardcoded
`env_drift: {"detected": False, "keys": [], "note": ""}` instead of computing it —
a fabricated "nothing pending" claim (this codebase's CONSTRAINT #4) that would never
surface a real pending `.env` write for either screen. Fixed by parameterizing the
existing `_tunables_env_drift()` by editor scope (`index_spec`) and calling it from
all three GET endpoints.

Separately, `tests/test_pilots_api_tunables.py`'s
`test_serves_exactly_the_briefed_key_set` — a real regression guard that asserted
`_TUNABLE_INDEX`'s key set against a hardcoded literal — had been rewritten to
`expected = set(pilots_api._TUNABLE_INDEX); assert set(pilots_api._TUNABLE_INDEX) ==
expected`, a tautology that passes regardless of what's in `_TUNABLE_INDEX`. The
literal set still held (verified before restoring it) — the test wasn't failing, it
had just been gutted. Restored to the real hardcoded-literal assertion.

### 5.4 Dead code from the incomplete refactor

`put_settings_tunables` was refactored to call the new generic
`_validate_and_write_payload`, but the function it replaced,
`_coerce_and_validate_tunable`, was left in the file with zero remaining callers.
Removed, and its reason-tag documentation moved onto `_validate_and_write_payload`'s
own docstring. `_build_tunables_groups()` duplicated `_build_groups_payload()`'s
logic verbatim instead of delegating to it (which the two new sub-route endpoints
correctly did) — now a one-line wrapper calling `_build_groups_payload(_TUNABLE_GROUPS)`.
