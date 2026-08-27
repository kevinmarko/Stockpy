# MCP widget contracts + browser diagnostics — Walkthrough

## Two-stage process

1. **Build**: an Antigravity (Gemini) session built the plan (`.claude/mcp_widget_contracts_and_browser_diagnostics_implementation_plan.md`) out on this branch, plus bundled in 5 additional out-of-scope "Honesty Constraint Auditor" fixes it discovered along the way (`investyo_mcp_server.py`'s `run_backtest`/`get_registry_prompt_status`, `api/pilots_api.py`'s `get_forecast_backfill_status`/`post_paper_broker_settle_expired`).
2. **Audit**: a Claude Code session independently verified the build with 4 adversarial review agents, each scoped to a disjoint set of files, none trusting the self-report. The self-report claimed "100% pass rate (513 passed)" — this was **false**: it had only run 2 of the 4 relevant test files. Running all 4 immediately surfaced 5 real failures. The full audit found and fixed 6 real bugs total.

## What was actually wrong (found by the audit, not by the original build)

1. **`investyo_mcp_server.py::run_backtest`** — `total_return` lacked the same `if X is not None else None` guard its sibling fields (`sharpe`, `max_drawdown`) already had. When `total_ret` was honestly `None` (Backtrader produced no parseable output — exactly the CONSTRAINT #4 case the original fix was supposed to handle), `round(None, 4)` raised `TypeError`, caught by the function's own outer exception handler and reported as a fabricated `"Backtest failed: ..."` string instead of the honest empty result.
2. **`investyo_mcp_server.py::_pr_resolve_source`** — 2 more bare `except Exception: pass` swallows the original fix missed entirely (it only touched `_pr_cached_versions`/`_pr_all_known_ids`, three sibling helpers in the same block).
3. **`browser_diagnostics.py`** — the `except ImportError:` branch never bound a module-level `sync_playwright` name, so every mocked-Playwright test failed with `AttributeError` trying to `monkeypatch.setattr(bd, "sync_playwright", ...)`.
4. **`mcp_widgets/templates/_common.js::renderModelDiagnostics`** — exactly the failure mode the implementation plan warned about: a stray 2-space-indented line inside a function that an unrelated prior commit (`65c7adf2`) had reindented to 4-space, evidence of a careless line inserted without matching the surrounding style.
5. **`mcp_widgets/templates/_common.js::renderStrategyTuner`** — when the host doesn't support `app.callServerTool`, the slider handlers still fired the debounced recompute (caught by try/catch, never crashed, but contradicted the "static display" message with a spurious "Recalculating…" flash).
6. **`api/pilots_api.py::get_forecast_backfill_status`** — the build's fix correctly stopped fabricating `"not_run"` on a corrupt summary file, but over-corrected to `HTTPException(500)`, which the webapp's `ErrorState` renders as a dead end hiding the "Run Backfill" retry button — the fix broke the screen's own self-healing path. Reverted to an honest 200 with a distinct `"error"` status.

Also fixed: `post_paper_broker_settle_expired`'s claimed fix was verified genuinely correct as-built, no change needed. 2 stale/wrong test mocks corrected (`n_by_model`'s real dict shape; a misleadingly-named test renamed to match what it verifies). 2 new regression tests added for previously wholly-untested failure paths. `requirements-optional.txt`'s playwright comment expanded to match the file's own convention for other optional heavy deps.

## Scope note

`browser_diagnostics.py`'s `_BASELINE_DIR` uses `settings.OUTPUT_DIR / "visual_baselines"` (not a bare `"output"` literal) — confirmed `OUTPUT_DIR` always resolves to a real path before the settings singleton is exposed, so this is safe and consistent with the rest of the codebase's `LOCAL_DATA_ROOT` convention.

`docs/architecture/observability-and-apis.md`'s new bullet landed after the `streamable-http` bullet rather than before it as the plan specified — still sensibly grouped with the MCP widgets/DevTools content, left as-is (cosmetic ordering only).

## Verification (real output, not "should pass")

- `node --check mcp_widgets/templates/_common.js` — clean.
- `pytest tests/test_investyo_mcp_server.py tests/test_investyo_mcp_widgets.py tests/test_browser_diagnostics.py tests/test_pilots_api.py tests/test_pilots_paper_broker.py tests/test_forecast_backfill.py tests/test_settings_liveness.py tests/test_measure_settings_census.py -q` — **1115 passed, 2 skipped, 0 failed** (154.61s). The 2 skips are `TestCaptureRealBrowser`'s two real-Chromium tests, confirmed skipping (not erroring) because Playwright genuinely isn't installed in this sandbox.
- Full offline suite (`pytest -m "not network and not slow" -n auto --dist loadgroup`) — **12386 passed, 33 skipped, 5 failed** (119.57s). The 5 failures are all in `tests/test_data_api_chat.py::TestMultiProviderRouting` and `tests/test_gemini_live_chat.py::TestLiveChatSession` — completely unrelated to anything this branch touches. Confirmed pre-existing: reproduced the identical 5 failures on a clean `origin/main` checkout in an isolated worktree before this branch's changes are even present.

## Follow-up: the "no JS test runner" gap is now closed

The original disclosed gap — "no JS test runner exists in this repo, so nothing here executes the widget render functions against a real DOM" — is fixed. `mcp_widgets/tests/` is a new, small npm project (jsdom as its one devDependency; `mcp_widgets/build/`'s existing package.json/package-lock.json for the vendor-bundle step are untouched) with `render.test.mjs`, using Node's built-in test runner. It loads `_common.js` via `vm.runInContext` against a real jsdom window — the exact same plain global-function script `mcp_widget_resources.py` serves to a real MCP host, not a rewritten copy — and calls each of the 7 fixed render functions with real and edge-case payloads, asserting on the actual rendered DOM.

**Real output**: `cd mcp_widgets/tests && npm install && npm test` — **18 passed, 0 failed** (2.14s). Includes a regression test that reproduces agent 3's exact fix (a slider drag on a host without `app.callServerTool` must never fire a recompute), a race-guard test (a fast second slider drag must supersede a slower first response), and an indentation-consistency test guarding against the exact botched-cherry-pick failure mode found in `renderModelDiagnostics`.

The `mcp-widget-builder` skill (`.claude/skills/mcp-widget-builder/SKILL.md` and its Antigravity port `.agents/skills/mcp-widget-builder/SKILL.md`) is updated to document this as a required second test surface for any future widget change, alongside the existing Python payload/wiring tests. The Claude-side skill file didn't actually exist despite the Antigravity file's header claiming to be ported from it — created it to match, closing that inconsistency too.

A manual sanity-check in an actual MCP Apps host (Claude Desktop / claude.ai custom connector) is still worth doing before this reaches real usage — jsdom is a faithful DOM implementation but not a substitute for the real host's rendering/theming/sizing behavior — but the substantive gap (nothing executing this code at all) is closed.
