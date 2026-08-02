# Audit Guide: Runtime Tunables & Sub-Route Configuration Expansion

**PR Branch:** `feat-runtime-tunables-subroutes`  
**Author:** Antigravity AI  
**Date:** 2026-08-02  

---

## 1. Executive Summary

This feature set expands runtime `.env` tunables in the webapp by creating generic, schema-driven editor components, wrapping long single-page settings into collapsible group cards, establishing dedicated sub-route configuration screens for Sentiment Ingestion and Sector Selection, and wiring the corresponding backend schema generators and `ALLOWED_KEYS` guardrails.

---

## 2. Key Architectural Decisions

1. **Collapsible Section Layout (`TunableGroupCard.tsx`)**:
   - `SettingsManager.tsx` and generic settings screens now group fields into collapsible accordions.
   - When total groups on a screen is $\le 3$ (or for the first group in larger screens), groups default to open.
   - Summarizes total field count, dirty field count (un-saved changes), and rejected field status tags in the header.

2. **Schema-Driven Generic Settings Screen (`GenericSettingsEditor.tsx`)**:
   - Implements a generic template consuming backend `TunablesResponse` payloads.
   - Handles baseline value tracking, dirty state detection, type coercions (`bool`, `number`, `enum`, `json`), per-field validation error tagging, atomic save calls, daemon restart triggers, and `env_drift` notices.

3. **Sub-Route Settings Screens & In-App Navigation**:
   - Created `/settings/sentiment` ([`SentimentSettings.tsx`](file:///Users/kevinlee/Anti/Stockpy-main/webapp/src/screens/SentimentSettings.tsx)) for configuring sentiment ingestion, FinBERT, Edgar, GDELT, StockTwits, Reddit, and Google News parameters (~33 keys).
   - Created `/settings/sector-selection` ([`SectorSelectionSettings.tsx`](file:///Users/kevinlee/Anti/Stockpy-main/webapp/src/screens/SectorSelectionSettings.tsx)) for configuring sector selection top-N, weighting scheme, lookbacks, and factor weights (9 keys).
   - Added `SentimentLink` and `SectorSelectionLink` cards on `/settings` ([`Settings.tsx`](file:///Users/kevinlee/Anti/Stockpy-main/webapp/src/screens/Settings.tsx)).
   - Added direct `"Configure ingestion →"` and `"Configure sector selection →"` header navigation buttons on [`SentimentDynamics.tsx`](file:///Users/kevinlee/Anti/Stockpy-main/webapp/src/screens/SentimentDynamics.tsx) and [`SectorSelection.tsx`](file:///Users/kevinlee/Anti/Stockpy-main/webapp/src/screens/SectorSelection.tsx).

4. **Backend Endpoint & Guardrail Extensions**:
   - [`api/pilots_api.py`](file:///Users/kevinlee/Anti/Stockpy-main/api/pilots_api.py): Refactored `put_settings_tunables` to use `_validate_and_write_payload`. Added `GET/PUT /settings/sentiment` and `GET/PUT /settings/sector-selection`.
   - [`gui/env_io.py`](file:///Users/kevinlee/Anti/Stockpy-main/gui/env_io.py): Appended all 36 newly exposed sentiment and sector selection keys to `ALLOWED_KEYS`.

---

## 3. Scope of Changed & Added Files

| Path | Purpose |
| --- | --- |
| `webapp/src/components/TunableGroupCard.tsx` | **[NEW]** Accordion group card with badges and state. |
| `webapp/src/components/GenericSettingsEditor.tsx` | **[NEW]** Generic schema-driven settings screen wrapper. |
| `webapp/src/screens/SentimentSettings.tsx` | **[NEW]** Sub-route screen for `/settings/sentiment`. |
| `webapp/src/screens/SectorSelectionSettings.tsx` | **[NEW]** Sub-route screen for `/settings/sector-selection`. |
| `webapp/src/screens/SettingsManager.tsx` | Integrated `TunableGroupCard` for collapsible groups. |
| `webapp/src/screens/Settings.tsx` | Added SectionCard links for Sentiment & Sector Selection settings. |
| `webapp/src/screens/SentimentDynamics.tsx` | Added header link navigating to `/settings/sentiment`. |
| `webapp/src/screens/SectorSelection.tsx` | Added header link navigating to `/settings/sector-selection`. |
| `webapp/src/App.tsx` | Registered routes for `/settings/sentiment` & `/settings/sector-selection`. |
| `webapp/src/api/client.ts` & `mock.ts` | Added API methods & mock handlers for sub-route settings. |
| `api/pilots_api.py` | Added helper endpoints `GET/PUT /settings/sentiment` & `GET/PUT /settings/sector-selection`. |
| `gui/env_io.py` | Registered sentiment & sector selection keys under `ALLOWED_KEYS`. |
| `tests/test_pilots_api_tunables.py` | Verified scope invariants, validation, and endpoint behavior. |

---

## 4. Verification Checkpoints for Auditor Agent

1. **Backend Integration & Safety**:
   ```bash
   .venv/bin/pytest tests/test_pilots_api_tunables.py
   ```
   *Expected Output*: 38 passed.

2. **Frontend Vitest Suite**:
   ```bash
   cd webapp && npm test -- --run
   ```
   *Expected Output*: 982 passed across 87 test files.

3. **Frontend Type Soundness & Build**:
   ```bash
   cd webapp && npm run build
   ```
   *Expected Output*: Clean `tsc --noEmit` and Vite bundle generation.
