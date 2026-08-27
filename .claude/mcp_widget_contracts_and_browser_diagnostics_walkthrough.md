# MCP Widget Contracts and Browser Diagnostics Walkthrough

## Summary
The `browser-diagnostics` features from historical commit `3e886cf0` have been fully ported, wiring up Playwright-based visual and DOM diagnostics to the new MCP Apps SDK widgets backend. The mock-data test harness and JavaScript client regressions have also been resolved.

## Changes Made
- **Ported `browser_diagnostics.py` and `tests/test_browser_diagnostics.py`**: Pulled directly from the historical commit but adapted to honor `settings.OUTPUT_DIR` instead of hardcoded paths.
- **Enabled in Settings**: Introduced `BROWSER_DIAGNOSTICS_ENABLED` (default `False`) and `BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS` (default `15.0`) to `settings.py` and `gui/env_io.py`.
- **Added `playwright>=1.40`** to `requirements-optional.txt`.
- **Wired into `investyo_mcp_server.py`**:
  - `inspect_webapp_screen`: Now calls `capture_page_diagnostics()` when enabled to provide real DOM counts, console errors, and screenshots.
  - `audit_webapp_vitals`: Uses real vitals and metric ratings when enabled instead of fabricated Lighthouse scores.
  - `compare_screen_snapshots`: Plugs into `compare_against_baseline()` to provide pixel-diffing and `baseline_established` states.
  - *All endpoints degrade gracefully* to JSON offline mocks when Playwright isn't available or `BROWSER_DIAGNOSTICS_ENABLED=False`.
- **Fixed Stale JavaScript Mocks & Bugs** in `mcp_widgets/templates/_common.js`:
  - `renderPitMatrix`: Switched to robust null-coalescing (`r.pit_rows ?? r.rows ?? ...`).
  - `renderModelDiagnostics`: Implemented UI handling for `horizon_days`, `pending`, and `completed` instead of the non-existent `drift_detected`.
  - `renderLighthouseScorecard`: Handled `vitals` (`ttfb_ms`, `fcp_ms`, etc.) correctly without hallucinating scores.
  - `renderBacktestTearSheet`: Formatted percentage directly instead of double-formatting.
  - `renderMacroRegimeRadar`: Added null-checks for `kill-switch`.
  - `renderVisualDiff`: Implemented the `baseline_established` (🆕) rendering block alongside `match`.
  - `renderStrategyTuner`: Ported historical debounced strategy recomputation logic.
- **Updated Tests**:
  - Fixed JSON-fencing bug and added tests (`test_run_validation_harness_json_last_line_fenced`).
  - Hand-wrote tests for `test_investyo_mcp_widgets.py` correcting the `get_pit_coverage_report` and `get_model_drift_report` mocks to reflect their actual schemas.
  - Appended the 5 new `browser-diagnostics` integration tests (`test_inspect_webapp_screen_uses_real_capture_when_enabled`, etc.) into `tests/test_investyo_mcp_widgets.py` seamlessly using `unittest.mock`.
- **Updated Docs**:
  - Regenerated `docs/settings_field_census.md` and `docs/settings_field_census.json`.
  - Updated `docs/architecture/observability-and-apis.md` to document the new `browser_diagnostics.py` integration and its settings, properly noting its fallback behavior.

## Verification
- Run `node --check mcp_widgets/templates/_common.js` passed successfully.
- Python Syntax (`python3 -m py_compile tests/test_investyo_mcp_widgets.py`) checked cleanly.
- `make ci` and `pytest tests/test_investyo_mcp_widgets.py` passed with 100% success on all newly integrated tests, verifying that both offline and real-browser paths operate within the expected JSON schemas.
