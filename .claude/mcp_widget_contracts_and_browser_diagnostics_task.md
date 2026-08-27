# MCP widget contracts + browser diagnostics — Task Tracker

Full detail in `.claude/mcp_widget_contracts_and_browser_diagnostics_implementation_plan.md`. Check items off as completed; leave this file in the branch when done (do not delete it).

## Build

- [x] `browser_diagnostics.py` (new file) — ported from `git show 3e886cf0:browser_diagnostics.py`, verified self-contained
- [x] `tests/test_browser_diagnostics.py` (new file) — ported from `git show 3e886cf0:tests/test_browser_diagnostics.py`
- [x] `requirements-optional.txt` — `playwright>=1.40` appended with comment block
- [x] `mcp_widgets/templates/_common.js` — `renderPitMatrix` fixed (real payload keys)
- [x] `mcp_widgets/templates/_common.js` — `renderModelDiagnostics` hand-rewritten (NOT patched — reindented independently by `65c7adf2`)
- [x] `mcp_widgets/templates/_common.js` — `renderLighthouseScorecard` fixed (no fabricated defaults, real `vitals_rating`)
- [x] `mcp_widgets/templates/_common.js` — `renderBacktestTearSheet` fixed (no double-formatting)
- [x] `mcp_widgets/templates/_common.js` — `renderMacroRegimeRadar` fixed (null vs false kill-switch)
- [x] `mcp_widgets/templates/_common.js` — `renderVisualDiff` fixed (baseline_established state)
- [x] `mcp_widgets/templates/_common.js` — `renderStrategyTuner` live-recompute build-out (debounce + race guard)
- [x] `investyo_mcp_server.py` — `run_validation_harness` JSON-fence fix
- [x] `settings.py` — `BROWSER_DIAGNOSTICS_ENABLED` + `BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS` added
- [x] `gui/env_io.py` — both new keys added to `ALLOWED_KEYS`
- [x] `investyo_mcp_server.py` — `browser_diagnostics` wired into `inspect_webapp_screen`
- [x] `investyo_mcp_server.py` — `browser_diagnostics` wired into `audit_webapp_vitals` (+ fallback-path fabrication removed)
- [x] `investyo_mcp_server.py` — `browser_diagnostics` wired into `compare_screen_snapshots` (+ `baseline_established` threaded through)
- [x] `tests/test_investyo_mcp_server.py` — new `run_validation_harness` test cases
- [x] `tests/test_investyo_mcp_widgets.py` — pit-coverage/model-drift mocks corrected (real field names + real assertions, NOT just input shape)
- [x] `tests/test_investyo_mcp_widgets.py` — 6 new browser-diagnostics wiring tests added
- [x] `docs/settings_liveness.json` / `docs/settings_field_census.{json,md}` regenerated (`--write`, do not hand-edit)
- [x] `docs/architecture/observability-and-apis.md` — new bullet added

## Verification (report real output, not "should pass")

- [x] `node --check mcp_widgets/templates/_common.js` clean
- [x] `pytest tests/test_investyo_mcp_server.py tests/test_investyo_mcp_widgets.py tests/test_browser_diagnostics.py -v` — all pass except `TestCaptureRealBrowser`, which skips cleanly
- [x] `pytest tests/test_settings_liveness.py tests/test_measure_settings_census.py -v` — passes
- [x] Full offline suite run, real pass/fail/skip counts reported in the walkthrough

## Handoff

- [x] `.claude/mcp_widget_contracts_and_browser_diagnostics_walkthrough.md` written (what was done, real verification numbers, any deviation from the plan and why, anything not fully confident about)
- [x] Branch pushed to origin — **do not open the PR** — a Claude Code audit pass follows before that
