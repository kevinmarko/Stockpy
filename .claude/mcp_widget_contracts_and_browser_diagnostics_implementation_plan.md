# MCP widget/tool payload-contract fixes + real browser diagnostics — Implementation Plan

**Status: plan only, not yet built.** Intended for an Antigravity (Gemini) session to build out. Claude Code will audit and fix the result afterward — do not skip the audit step, this file's own investigation already caught real gaps in the historical source material this plan is based on (see "A finding discovered during exploration" below).

## Context

A historical commit (`3e886cf0`, on an abandoned local branch that was never merged) bundled 10 code-review findings plus a net-new feature build-out for this repo's MCP DevTools widgets. That branch was one of ~20 stale worktrees/branches cleaned up in a prior session. Before discarding it, two independent verification passes confirmed most of its content is **not** redundant — 8 of the 10 original findings, plus the entire new feature, are genuinely still-broken/missing on current `main` today. This plan revives that work as a fresh, properly-scoped PR against current `main`, fixing what's still broken and completing the browser-diagnostics feature that was never finished.

Two things are explicitly **not** in scope, confirmed already resolved independently since the historical commit was written — do not touch these, do not reapply the historical commit's version of them:
- `trigger_macro_engine`'s `kill_switch_active` hardcoding (`investyo_mcp_server.py:2254` already reuses `MacroEconomicDTO.killSwitch`).
- `_common.js`'s "unterminated string" syntax error in `renderModelDiagnostics` (fixed differently by an unrelated later commit, `65c7adf2`, which also reindented that whole function — this matters below, see the cherry-pick-safety section).

## What's actually still broken (verified against real payload shapes, not assumed)

| # | Finding | File | Verified real payload / root cause |
|---|---|---|---|
| 1 | `renderPitMatrix` reads nonexistent keys (`r.rows`, `r.Rows`, `r.count`, `r.earliest`, `r.latest`) | `mcp_widgets/templates/_common.js` ~1108-1118 | Real payload (`get_pit_coverage_report` → `validation/pit_fundamentals.py::generate_coverage_report`) uses `pit_rows`, `earliest_report_date`, `latest_report_date` |
| 2 | `renderModelDiagnostics` reads nonexistent keys (`payload.drift_detected`, `r.decay_pct`, per-row `r.horizon_days`, `r.inverse_rmse`/`r.skill_score`) | same file ~1127-1172 | Real payload (`get_model_drift_report` → `pilots/observability.py::forecast_skill_by_symbol_summary`) is top-level `{horizon_days, rows}`, rows are `{symbol, pending, completed, skill_weights, n_by_model}` — no drift/decay signal exists at all |
| 3 | `run_validation_harness` fences the whole stdout (human table + trailing JSON line) as one `json` block, breaking `JSON.parse` | `investyo_mcp_server.py` ~2447-2470 | `scripts.refresh_validations --json` prints a table then exactly one JSON line as the last line |
| 4 | `renderLighthouseScorecard` defaults to fabricated scores/vitals (`{performance: 90, ...}`, `{lcp: "0.8s", ...}`) and hardcodes a "Good" badge | same JS file ~768-827 | Should render `"—"` for unmeasured, read real `vitals_rating` |
| 5 | `renderBacktestTearSheet` double-formats Max DD/Total Return through `fmtMetric` (which only accepts raw numbers, not pre-formatted strings — confirmed by reading `fmtMetric`'s own implementation) | same file ~848-852 | Always renders "—" instead of the real percentage |
| 6 | `renderMacroRegimeRadar` collapses an unknown (`null`) kill-switch state into the same "safe" badge as a confirmed-inactive one | same file ~928-931 | |
| 7 | `renderVisualDiff` has no handling for a first-ever screenshot (no baseline yet) | same file ~1006-1044 | Depends on finding #10's real diff wiring |
| 8 | Strategy Tuner sliders are display-only — hardcoded stat row (`Sharpe: 1.42`, `MaxDD: 12.4%`, `Win Rate: 64.5%`), no live recompute | same file, `renderStrategyTuner` ~1174-1230 | `tune_strategy_parameters` (already exists, `investyo_mcp_server.py:5739`) params/return fields confirmed byte-identical to what the historical widget code expects — no adaptation needed |
| 9 | `audit_webapp_vitals` fabricates Lighthouse-style scores unconditionally (`96 if is_online else 0`, etc.) | `investyo_mcp_server.py` ~5470-5532 | CONSTRAINT #4 violation |
| 10 | **New feature, entirely absent from the repo today**: real headless-browser diagnostics | new `browser_diagnostics.py` + wiring | Confirmed zero trace anywhere in the repo (no `playwright` reference, no settings field, no test file) |

## Approach

### Ordered edit list

1. **`browser_diagnostics.py`** (new file, repo root) — fully self-contained, zero overlap with anything else. A historical, never-merged commit `3e886cf0` has a complete, verified-good 262-line implementation: `git show 3e886cf0:browser_diagnostics.py`. Port it near-verbatim. Required properties (confirm none dropped):
   - `PLAYWRIGHT_AVAILABLE` lazy/optional import guard (`try: from playwright.sync_api import sync_playwright; PLAYWRIGHT_AVAILABLE = True; except ImportError: PLAYWRIGHT_AVAILABLE = False`) — module MUST import cleanly with no `playwright` installed.
   - `capture_page_diagnostics(url, timeout_seconds) -> dict`: real Chromium capture (console, page errors, Core Web Vitals via injected `PerformanceObserver`s — not fabricated, DOM node count, real base64 screenshot, title). NEVER raises — degrades to `{"available": False, "reason": "..."}` on any failure, always releases the browser via `finally`.
   - `_rate(value, key) -> Optional[str]`: rates against real web.dev thresholds (`lcp_ms: (2500, 4000)`, `fcp_ms: (1800, 3000)`, `ttfb_ms: (800, 1800)`, `cls: (0.1, 0.25)`) → `"good"`/`"needs-improvement"`/`"poor"`/`None`. Never rates `None` (CONSTRAINT #4).
   - Deliberately does NOT invent a 0-100 Lighthouse-style composite score (no axe-core/SEO engine runs) — accessibility/bestPractices/seo report `None` always.
   - `compare_against_baseline(route, screenshot_bytes, threshold_pct=1.0) -> dict`: real Pillow+numpy pixel-diff. First call for a route saves baseline, reports `baseline_established: True`. Size mismatch reported honestly, never resized.
   - Check `settings.OUTPUT_DIR`'s actual default before hardcoding `Path("output") / "visual_baselines"` — use judgment on literal vs. `settings.OUTPUT_DIR`-relative, note the choice.

2. **`tests/test_browser_diagnostics.py`** (new file) — port `git show 3e886cf0:tests/test_browser_diagnostics.py` (288 lines) near-verbatim. Classes: `TestRate`, `TestRouteSlug`, `TestCaptureUnavailableWhenPlaywrightMissing`, `TestCaptureMockedPlaywright` (all unconditional, fully mocked), `TestCompareAgainstBaseline` (real Pillow/numpy math, no browser needed, unconditional), `TestCaptureRealBrowser` (`@_skip_no_playwright`-gated — MUST skip cleanly, not error, since Chromium is almost certainly not installed).

3. **`requirements-optional.txt`**: append `playwright>=1.40` with the historical commit's explanatory comment block (notes the required separate `playwright install chromium` step).

4. **`mcp_widgets/templates/_common.js`** — 7 of 10 findings, one continuous pass, one file:
   - **`renderPitMatrix`** (~1108-1118): `${r.rows || r.Rows || r.count || "—"}` → `${r.pit_rows ?? r.rows ?? r.Rows ?? r.count ?? "—"}`; same pattern for `earliest_report_date`/`latest_report_date`. Small, mechanical, safe to port near-verbatim.
   - **`renderModelDiagnostics`** (~1127-1172): **DO NOT patch/cherry-pick — hand-write against the current 4-space-indented body** (independently reindented by `65c7adf2` since the historical commit). Target: drop `payload.drift_detected` (doesn't exist), use `payload.horizon_days` for a neutral badge; table columns become `Symbol | Pending | Completed | Skill Weights`; per row `r.pending ?? "—"`, `r.completed ?? "—"`, `skill_weights` joined as `"model: weight"` pairs via `fmtMetric` on each weight.
   - **`renderLighthouseScorecard`** (~768-827): `payload.scores || {}` (not fabricated numbers); unmeasured → `"—"` + neutral gauge class; `payload.vitals || {}` with real keys `ttfb_ms`/`fcp_ms`/`lcp_ms`/`cls`; read `payload.vitals_rating || {}` for the badge instead of hardcoding "Good". Largest single hunk — re-verify brace/quote style before pasting (a `${...}` template expression inside a double-quoted HTML attribute is exactly what caused the historical syntax bug elsewhere in this file).
   - **`renderBacktestTearSheet`** (~848-852): stop double-formatting through `fmtMetric` — format the percentage directly, `"—"` when null. Mechanical.
   - **`renderMacroRegimeRadar`** (~928-931): distinguish `null`/`undefined` kill-switch (neutral "Kill Switch Unknown" badge) from `false` (confirmed-inactive) from `true` (active). Mechanical.
   - **`renderVisualDiff`** (~1006-1044): add a `baseline_established` branch (🆕 badge) alongside existing match/no-match. Must land together with step 8's `compare_screen_snapshots` wiring — a JS-only or Python-only half of this is a broken half-feature.
   - **`renderStrategyTuner`** (~1174-1230): port the historical commit's debounce/race-guard implementation near-verbatim — `state` object, `renderStats`, `scheduleRecompute`/`runRecompute` (350ms debounce, `requestSeq` race guard), `app.callServerTool({name: "tune_strategy_parameters", arguments: {...state}})`, `extractJsonPayload(result?.content?.[0]?.text)` (shared helper already exists in `_common.js`), graceful degradation when `app.callServerTool` isn't available. Confirmed `tune_strategy_parameters`'s real params/return fields (`simulated_sharpe`/`simulated_max_dd_pct`/`simulated_win_rate_pct`) match exactly what this JS expects — no adaptation needed. Reference the current, currently-shipping pattern in `mcp_widgets/templates/pilot-picker.html` for the base `app.callServerTool` + `extractJsonPayload` plumbing.
   - Verify with `node --check mcp_widgets/templates/_common.js` after EVERY function edit, not just once at the end.

5. **`investyo_mcp_server.py`** — `run_validation_harness` fix (~2447-2470): extract `json_line = stdout_clean.splitlines()[-1] if stdout_clean else ""`, validate with `json.loads` in try/except; if valid, fence only that line alongside the full table text; if not, return the plain unfenced table (tool call still succeeds). Small, mechanical, safe to port near-verbatim.

6. **`settings.py`**: insert after `MARKET_DATA_LATENCY_TRACKING_ENABLED` — `BROWSER_DIAGNOSTICS_ENABLED: bool = Field(default=False, ...)` and `BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS: float = Field(default=15.0, ...)`. Port the historical commit's docstrings (correct, unit-drifted).

7. **`gui/env_io.py`**: insert the two new keys into `ALLOWED_KEYS` after `"MARKET_DATA_LATENCY_TRACKING_ENABLED"`, with the historical commit's comment ("non-secret opt-in dependency flags, no credential material").

8. **Wire `browser_diagnostics` into `investyo_mcp_server.py`'s three tools** (`inspect_webapp_screen`, `audit_webapp_vitals`, `compare_screen_snapshots`, ~5371-5666) — must happen after steps 1 and 6 exist. Port near-verbatim (confirmed byte-identical pre-image against current main):
   ```python
   if settings.BROWSER_DIAGNOSTICS_ENABLED:
       import browser_diagnostics
       real = browser_diagnostics.capture_page_diagnostics(url, timeout_seconds=settings.BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS)
       if real.get("available"):
           # build payload from real fields, return early
       # else fall through to the existing HTTP-only path, unchanged
   ```
   Also fix `audit_webapp_vitals`'s fallback path: `scores` becomes `{"performance": None, "accessibility": None, "bestPractices": None, "seo": None}` unconditionally; `vitals`/`vitals_rating` become `None` per field except `ttfb_ms` (genuinely still measurable via wall-clock, keep it real). `compare_screen_snapshots` additionally calls `browser_diagnostics.compare_against_baseline(...)` and threads `baseline_established` into its payload.

9. **Test fixes**:
   - `tests/test_investyo_mcp_server.py`: new test cases for `run_validation_harness`'s fix (multi-line table + valid JSON last line → only that line fenced; invalid last line → plain unfenced text, no crash). Insert near the existing `test_run_validation_harness*` tests (~2061-2086).
   - `tests/test_investyo_mcp_widgets.py` — **two required sub-steps, both mandatory, not optional**:
     - **Fix stale mocks** (new scope beyond the historical commit — see "A finding discovered during exploration" below): `test_get_pit_coverage_report_emits_json_matching_widget_schema` (~649-662) currently mocks `generate_coverage_report` with the WRONG field names (`rows`/`earliest`/`latest`). Fix the mock to use real names (`pit_rows`/`earliest_report_date`/`latest_report_date`) AND update the assertions to check those exact keys. Same for `test_get_model_drift_report_emits_json_matching_widget_schema` and `test_get_model_drift_report_fires_alert_on_synthetic_injected_drift` (~676-710) — mock `forecast_skill_by_symbol_summary` with real fields (`pending`/`completed`/`skill_weights`/`n_by_model`, no `decay_pct`/`drift_detected`/per-row `horizon_days`), and rewrite the "fires alert on synthetic injected drift" test's assertion since there's no real `drift_detected` signal to assert on — replace with something the real payload actually carries.
     - **Add 6 new browser-diagnostics tests** from the historical commit (`git show 3e886cf0 -- tests/test_investyo_mcp_widgets.py`), insert after `test_compare_screen_snapshots_emits_json_matching_widget_schema` (~611), before `test_trace_webapp_network_emits_json_matching_widget_schema` (~614). All use `monkeypatch.setitem(sys.modules, "browser_diagnostics", mock.MagicMock())` — none need real Playwright.

10. **Regenerate settings artifacts LAST**, after `settings.py` is final:
    ```
    python3 scripts/settings_liveness.py --write
    python3 scripts/measure_settings_census.py --write
    ```
    This updates `docs/settings_liveness.json`, `docs/settings_field_census.json`, `docs/settings_field_census.md`. Never hand-edit these — `tests/test_settings_liveness.py`/`tests/test_measure_settings_census.py` are drift tests.

### A finding discovered during exploration, not in the historical commit's original 10 — required, not optional

`tests/test_investyo_mcp_widgets.py`'s existing pit-coverage and model-drift tests mock their backing functions with the **same wrong field names** the JS bugs in findings #1/#2 are built around. These tests currently provide false confidence — they'll keep passing whether or not the widget JS is actually fixed. Correcting the mocks AND their assertions (step 9 above) is required, or the JS fixes ship with no real test coverage.

### Documentation update (part of this plan, not deferred)

`docs/architecture/observability-and-apis.md` — one new bullet describing `browser_diagnostics.py` and its two settings, inserted after the existing widgets/DevTools bullet (confirmed unchanged anchor at current line ~12, before the `streamable-http` transport bullet at ~13). No other `docs/` files apply here (not a signal module, not a validation strategy, not `CLAUDE.md`/`AGENTS.md` itself).

### CONSTRAINT #4 / #6 discipline (this repo's fail-honest / fail-closed rules — non-negotiable)

Every fix above must degrade to `None`/`"—"`/unmeasured, NEVER a fabricated plausible-looking number. `browser_diagnostics.py`'s capture/compare functions must never raise — any failure mode degrades to `{"available": False, "reason": ...}` and falls through to the existing (currently-shipping) HTTP-only path.

## Verification — must show actual zero-failure output, not "should pass"

1. `node --check mcp_widgets/templates/_common.js` — after every function edit, not just once at the end.
2. `python3 -m pytest tests/test_investyo_mcp_server.py tests/test_investyo_mcp_widgets.py tests/test_browser_diagnostics.py -v` — expect `TestCaptureRealBrowser` to report **skipped** (not failed/errored); everything else must genuinely pass, including the real Pillow+numpy pixel-diff tests (need no browser at all).
3. `python3 -m pytest tests/test_settings_liveness.py tests/test_measure_settings_census.py -v` — after artifact regeneration.
4. Full offline suite (this repo's standard offline pytest invocation / `make ci`) as the final gate.
5. Report REAL pass/fail/skip counts, not a paraphrase.

**Known, honestly-stated gap**: no JS test runner exists in this repo (no jest/mocha, no `.test.js` files). `node --check` proves syntax only; the Python tests prove payload-shape correctness only. Nothing here executes `renderModelDiagnostics`/`renderLighthouseScorecard`/etc. against a real DOM. Flag this in the walkthrough — a human should sanity-check the widgets render correctly in an actual MCP Apps host before this reaches real usage.

## Critical files

- `mcp_widgets/templates/_common.js` (7 of 10 findings)
- `investyo_mcp_server.py` (`run_validation_harness`, `audit_webapp_vitals`, `inspect_webapp_screen`, `compare_screen_snapshots`)
- `settings.py`, `gui/env_io.py` (two new opt-in flags)
- New: `browser_diagnostics.py`, `tests/test_browser_diagnostics.py`
- `tests/test_investyo_mcp_widgets.py`, `tests/test_investyo_mcp_server.py`
- `docs/architecture/observability-and-apis.md`
- Reference only (read, don't edit): `pilots/observability.py`, `validation/pit_fundamentals.py`, `mcp_widgets/templates/pilot-picker.html` (existing live-recompute pattern), `git show 3e886cf0:browser_diagnostics.py` / `:tests/test_browser_diagnostics.py`

## Branch / PR

Build directly on `fix-mcp-widget-contracts-and-browser-diagnostics` (already created off `origin/main`, this file is committed to it). When done, leave the branch pushed to origin — do not open the PR yourself; a Claude Code audit pass follows before that happens.
