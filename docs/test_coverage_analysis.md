# Test-Coverage Analysis

## Executive summary

**2026-08-05 re-audit — top-line result: coverage has genuinely improved, not regressed, since the
last pass, and this round's highest-value find was a real, previously-unknown production bug caught
and fixed along the way (see "Phase 6" below).** The codebase's source surface grew from 234 to
**343 Python modules** (excl. `tests/`, `gui/`, `webapp/`) since 2026-07-14, and the test suite grew
faster still — **413 `test_*.py` files, ~8,400 `def test_` functions** locally, confirmed against the
real GitHub Actions CI run for the current `main` HEAD (commit `45ad294`, run
[30997767741](https://github.com/kevinmarko/Stockpy/actions/runs/30997767741)): **9,188 passed, 0
failed, 25 skipped, real measured coverage 68.42%** — up from the 61% baseline this doc's `.coveragerc`
floor (`fail_under = 58`) was set a few points below. The floor has real headroom to raise now (see
Phase 6). This re-audit's methodology and its one major correction to the *process* (not just the
numbers) are covered in Phase 6; the prior audits' findings below (Phases 1-5) remain accurate and are
left as historical record.

The Stockpy test suite is large and genuinely mature — **4,547 test functions across 266 `test_*.py`
files** as of the 2026-07-14 re-audit (up from 3,446 tests / 203 files at the original audit) — with
disciplined conventions: fully-offline mocking, in-memory DB isolation, dedicated no-lookahead
perturbation proofs, and dead-letter resilience checks. The structural gaps from the original audit
(no coverage measurement, no CI) are resolved (see "Current state" below), and the five highest
risk×gap engine modules flagged in Phase 3 (`simulation_engine.py`, `main_orchestrator.py`,
`evaluation_engine.py`, `strategy_engine.py`, `research_engine.py`) now have owning suites.

**This re-audit's core finding:** the codebase has grown ~30% in both source surface and test surface
since the original audit, but growth has been uneven. New *engine* modules are consistently tested
(pilots/, `desktop/orchestrator_daemon.py`, `api/pilots_api.py`, `allocators/dual_momentum.py`,
`data/edgar_fundamentals.py`, `watch_engine.py` all landed with owning suites). Two categories
initially looked like real, unexercised gaps: (1) **`Gravity AI Review Suite.py`**, the codebase's
biggest single file at ~14,900 lines and its own-documented deployability gate, has **zero
`def test_` functions of its own**; and (2) a **GUI "render-wrapper" pattern** has emerged where
`gui/<feature>.py` holds tested pure logic but the actual Streamlit tab entry point at
`gui/panels/<feature>.py` also contains real, non-trivial pure helper functions with no direct test
coverage. A full two-round investigation of (1) — 38 of ~50 step blocks read and cross-checked against
the live test suite by direct execution, not assumption — **substantially revised the initial
framing**: nearly every Gravity step turns out to be independently, often more rigorously, covered by
dedicated test files already; only 4 genuine gaps surfaced across the whole investigation, 3 of which
are now mirrored. The file having zero tests of its own never meant its checked behaviors were
untested — the real gap was verified traceability, not missing coverage. See the Phase 5 section for
the full, evidence-based breakdown.

**Phase 5 implementation status: complete for this pass.** Closed: the `gravity_audit.py` pure-helper
tests; the `alerting_mcp`/`mcp_remote_adapter` owning suites; `data/robinhood_client.py` (0%→94%);
`ai_verification_prompts.py` (91%, plus a correction to what its `GravityAIAuditor` class actually
does vs. what its docstring claims); the full two-round Gravity step investigation with 3 of 4
confirmed gaps mirrored; and `investyo_mcp_server.py` (0%→84%, 99 tests) — which also surfaced and
fixed two real, previously-shipped-broken tools (`configure_alerts`/`send_test_alert` importing a
module that doesn't exist, and two plot tools writing to a hardcoded personal-machine path from the
retired Antigravity IDE). Two stray dead root test files were deleted and one unrelated pre-existing
test that silently made a live network call was fixed. The full offline suite (5,179 tests) is green
after every change. `webapp/` component tests and the CI coverage-floor ratchet are also done (the
latter: `fail_under = 58` in `.coveragerc`, a few points below the measured 61% whole-suite baseline).
The only item not closed or substantially addressed this pass is the long tail of ~18 GUI
render-wrapper panels' pure helpers beyond `gravity_audit.py` — see the Phase 5 section for exact
status per item.

---

## Current state

### The numbers

| Metric | Original audit | 2026-07-14 re-audit | 2026-08-05 re-audit |
|--------|-----------------|----------------------|----------------------|
| `test_*.py` files under `tests/` | 203 | 266 | **413** |
| `def test_` functions | 3,446 | 4,547 | **~8,400** |
| Source `.py` files under version control (excl. tests, `gui/`, `webapp/`) | ~180 (estimated) | 234 | **343** |
| pytest collection scope | `testpaths = tests` (pytest.ini) | unchanged | unchanged |
| Coverage measurement | none → added in this audit's Phase 1 | `pytest-cov` + `.coveragerc` in place | unchanged |
| **Real measured coverage (offline suite, real CI run)** | n/a | 61% | **68.42%** — [run 30997767741](https://github.com/kevinmarko/Stockpy/actions/runs/30997767741), commit `45ad294` |
| CI automation | none → added independently in parallel | `.github/workflows/ci.yml` runs `-m "not network"` + `--cov` on every push/PR | unchanged; now split into `test`/`test-slow` jobs (PR #606) |
| `pytest.ini` `markers` section | absent (unregistered `@pytest.mark.slow` warning) | present — `network` and `slow` are both registered | unchanged |
| `.coveragerc` `fail_under` floor | n/a | 58 (a few points below the 61% baseline) | **58 — now 10+ points below the real 68.42% baseline; has room to raise (see Phase 6)** |

**Methodology note for the 2026-08-05 figures:** this round deliberately used the *real* GitHub Actions
CI job log for the current `main` HEAD as ground truth for coverage and pass/fail counts, rather than a
local re-run — see Phase 6 for why. `test_*.py` file/function counts are from a local static count
(`find`/`grep`), which is environment-independent and unaffected by that distinction.

The "2026-07-14 re-audit" column above is the audit baseline captured before Phase 5 implementation
work began that same day. After Phase 5 (6 new test files, several existing files extended, 2 dead
files deleted): **272 `test_*.py` files, 4,748 `def test_` functions**, full offline suite (`-m "not
network"`) green.

### What is genuinely strong

This is not a thin suite — several areas are covered to a high standard and should be preserved as
the template for new tests:

- **No-lookahead / no-leakage proofs.** A shared `tests/lookahead_check.py` utility plus dedicated
  `*_lookahead.py` / `*_no_leakage.py` files perturb future data and assert indicator/forecast
  outputs are byte-identical up to the cutoff (RSI, MACD, ATR, Aroon, Chandelier, the CNN-LSTM
  scaler, triple-barrier labels, HMM `predict_proba`). This is the codebase's single most valuable
  test category and it is thorough.
- **Fully-offline determinism.** Network I/O is uniformly monkeypatched (`unittest.mock` +
  `monkeypatch`); no test hits Yahoo/FRED/Robinhood/Alpaca except explicitly-gated live smoke tests
  (`test_alpaca_paper_smoke.py`) that skip when credentials are absent.
- **DB isolation.** `tests/_db_isolation.py` and the `TransactionsStore(db_url="sqlite:///:memory:")`
  pattern keep DB-dependent tests hermetic and free of on-disk state bleed.
- **Risk-gate and resilience depth.** All ten `PreTradeRiskGate` checks, the kill switch,
  reconciliation drift, order idempotency, and dead-letter degradation paths each have focused tests
  (`test_risk_gate.py`, `test_kill_switch.py`, `test_reconciliation.py`,
  `test_order_manager_idempotency.py`, `test_dead_letter_resilience.py`).
- **No-fabricated-metrics discipline.** `test_no_fabricated_metrics.py` and per-module NaN-vs-0.0
  assertions enforce CONSTRAINT #4 across engines.
- **Per-file "Coverage:" docstrings.** Most test files document exactly which behaviors they pin,
  which makes gap analysis tractable.

---

## Structural gaps

### (a) No coverage measurement (resolved by this change)

At audit time there was no `pytest-cov`, no `coverage.py`, no `.coveragerc`, and no `[coverage]`
section in any config file — the suite's real reach was unknown, dead code and untested branches
were invisible, and there was no floor to ratchet against. This change adds `pytest-cov` +
`.coveragerc` (branch coverage, sensible omits) so `pytest --cov` yields real numbers, used in the
module-level table below.

### (b) No CI (resolved independently, extended by this change)

At audit time `.github/` contained only `CODEOWNERS` and `pull_request_template.md` — there was no
`.github/workflows/` directory, so nothing ran the suite on push or PR. An independent,
concurrently-developed change added `.github/workflows/ci.yml` (offline suite via
`-m "not network"`, Python 3.12) before this change merged. Rather than add a second, duplicate
workflow, this change extends that existing workflow with `--cov`/coverage-summary steps.

### (c) Hygiene issues

- **Stray, never-collected root test files — resolved in Phase 5.** `test_gravity.py` and
  `test_mock_abc.py` lived at the repo root, not under `tests/`; because `pytest.ini` pins
  `testpaths = tests`, they were never collected or run in CI. Investigating them (rather than just
  moving them, per the original plan) found both were dead weight, not gaps: `test_gravity.py`'s
  entire assertion — an unknown-strategy `run_validations()` call returns `deployable=False` with an
  `"error"` key — is already a real, passing pytest test at
  `tests/test_refresh_validations.py::TestRunValidations::test_unknown_strategy_is_dead_lettered`.
  `test_mock_abc.py` was a scratch script proving out a `sys.modules`-injection technique for mocking
  `tensorflow` that was never adopted — the actual test suite (`tests/test_forecasting_engine.py`)
  uses a simpler `monkeypatch`-based `_no_tensorflow` fixture instead, so the script's own technique
  is unused elsewhere in the codebase. Neither file contained a real `assert` (both were print-based
  scratch scripts) so even a mechanical rehome would not have added a genuine pytest test. **Both were
  deleted** rather than rehomed, per this codebase's own "delete rather than carry dead code" convention.
- **Unregistered marker — resolved.** `pytest.ini` now has a `markers` section registering `network`
  and `slow`; `--strict-markers` is set, so a typo'd marker fails loudly instead of silently no-op'ing.
  No action needed.
- **New in Phase 5: an unmarked live-network test found while verifying the suite end-to-end.**
  Running the full offline suite (`pytest -m "not network"`) after the Phase 5 changes below surfaced
  one pre-existing failure unrelated to any of them: `tests/test_harness_buyhold.py` makes a real,
  unmocked `yf.download("SPY", ...)` call and had no `@pytest.mark.network` marker, so it was not
  being deselected by CI's `-m "not network"` filter and would fail (or silently pass/fail depending
  on network conditions) in any offline environment. Fixed by adding the marker — the offline suite is
  now genuinely network-free end to end (verified: 5,025 collected, 0 failures with `-m "not network"`).

---

## Module-level gaps

Ranked by risk × gap. Modules below have real `pytest --cov` percentages where a dedicated coverage
run was performed after a new owning suite landed. "No owning file" means no `tests/test_<module>.py`
exists and the module's logic is exercised only incidentally (or not at all) by other tests — verified
here not just by filename convention but by grepping `tests/*.py` for actual import statements of
each candidate module, to rule out both false negatives (imported under a different alias/pattern)
and false positives (a bare package-level import that doesn't actually exercise the module).

| Module | Lines | Current coverage | Risk if broken | Proposed tests |
|--------|-------|------------------|----------------|----------------|
| `simulation_engine.py` | 258 | **81%** via `test_simulation_engine.py` (Phase 3, confirmed still in place). | High — CLAUDE.md mandates every new strategy be *proven here* before wiring into `strategy_engine.py`. | Done. |
| `main_orchestrator.py` | 1,519 | **51%** via `test_main_orchestrator.py` (Phase 3, confirmed still in place). | High — the async master pipeline. | Done; live-broker/reconciliation branches remain the residual gap (unchanged from original audit). |
| `evaluation_engine.py` | 1,013 | **43%** via `test_evaluation_engine.py` (Phase 3, confirmed still in place). | High — feeds sizing and reporting. | Done; residual gap unchanged from original audit. |
| `strategy_engine.py` | 668 | **76%** via `test_strategy_engine.py` (Phase 3, confirmed still in place). | High — core signal-generation + tactical-range + sizing surface. | Done. |
| `research_engine.py` | 498 | **52%** via `test_research_engine.py` (Phase 3, confirmed still in place). | Medium — analytics feeding risk/attribution. | Done; residual gap unchanged from original audit. |
| `diagnostics_and_visuals.py` | 1,115 | **Partial**, unchanged since the original audit — HTML-report path covered (`test_html_report.py`, `test_diagnostics_extra.py`); broader Plotly/chart-generation helpers are not. | Medium — active report path. | Still open: figure objects build from fixtures, empty/degraded inputs render placeholders not crashes. |
| `data/robinhood_client.py` | 345 | **Done in Phase 5 — 94%** via `tests/test_robinhood_client.py` (37 tests), up from 0%. | Medium — account/watchlist discovery. | Done: `login`/`fetch_positions`/`list_watchlist_names` happy-path + exception-degradation; `_suppress_rs_output` redirect/restore (including restore-on-exception and the `_rs_helper is None` fallback); `_sanitize_tickers`/`_watchlist_tickers`/`_file_tickers`/`_watchlist_files_from_env` shape/edge cases; `discover_watchlists`/`discover_universe` union + per-source failure isolation. Remaining 6% is two narrow branch pairs (`hasattr`-style shape guards) not worth chasing for marginal coverage. |
| **`Gravity AI Review Suite.py`** | **~16,062 lines / 6,962 statements (2026-08-05 measured)** | **Confirmed, hard-measured 0% via the real CI coverage report (`Gravity AI Review Suite.py 6962 6962 498 0 0%`, run 30997767741)** — no longer "effectively 0% by direct execution" as an estimate; this is now a directly-measured number from `pytest-cov`, and this substantially overstates the real *behavioral* gap** (fully investigated in Phase 5, two rounds). The file has **zero `def test_` functions of its own** and (space-containing filename) is architecturally excluded from ordinary pytest collection. Round 1 investigated all 20 `step_61`–`step_91` methods (the later Tier/Task-numbered audits): **every one already independently covered**, e.g. `step_78_advisory_agent_audit` ↔ `tests/test_advisory_agent.py`, `step_89_rolling_beta_lookahead_audit` ↔ `tests/test_indicators_lookahead.py`. Round 2 investigated the earlier, higher-a-priori-risk `step_1`–`step_58` inline-block layer (18 audits spanning CPCV/PBO/DSR deployability, the full Kelly/vol-target sizing stack, the HMM regime detector, the tail-scenario stress gate, all six bugs in the 2026-06 regression audit, and more) and found the SAME result: **near-total coverage already exists**, often more rigorously than Gravity's own checks (e.g. the BUG-6 Monte-Carlo-drift check in `tests/test_bug_fixes.py` is a genuine AST-based semantic-pattern detector, strictly stronger than Gravity's literal-string match). Across **both rounds combined (38 steps investigated), exactly 4 genuine gaps surfaced** — all now closed except one, deferred with reasoning (see Phase 5 item 1). | **Revised from Highest to Low-Medium.** The file's own test-count (0) never reflected the real risk — behavioral coverage exists for the checks that matter; what was missing was verified traceability, not tests. | Done, to the extent practical: 3 of 4 confirmed gaps mirrored (`settings.DRY_RUN` default in `tests/test_settings.py`, `TieredCostModel.estimate_round_trip_cost()` in `tests/test_cost_model.py`, `CANONICAL_REGIMES`-vs-`MacroEconomicDTO` cross-check in `tests/test_signal_weight_validation.py`). The 4th (a zero-`position_size`→$10,000 default guard in `pipeline/production_steps.py::StrategyEvalStep`) is real but deferred — see Phase 5 item 1 for why. Remaining low-value items: `step_82`–`step_86` (LLM/AI-panel audits, likely covered by the many `test_*_panel.py`/`test_gravity_ai_*.py` files given the pattern held everywhere else) and the confirmed-but-cosmetic duplicate step-numbering ("STEP 22"/"STEP 23" each label two unrelated audits) are not worth further investigation time given this evidence. |
| `ai_verification_prompts.py` (a prompt-compilation scaffold for an LLM-based "Gravity AI Auditor", distinct from `Gravity AI Review Suite.py`'s own `step_*` static-analysis audits above) | 337 | **Done in Phase 5 — 91%** via `tests/test_ai_verification_prompts.py` (15 tests), up from partial (only `tests/test_gravity_prompt_sourcing.py`'s registry-fallback coverage). **Correction to this doc's original characterization:** reading `GravityAIAuditor` in full for this work found the class does NOT do what its own docstring claims — "pre-checks the code via RegEx for required terminology" and "interacts directly with the Claude/OpenAI APIs" are both aspirational, not implemented. The class has exactly two real methods: `generate_prompt_for_step` (pure string concatenation) and `run_full_validation_suite` (matches step numbers against `ALL_PROMPTS` and appends a **hardcoded stub** `AIReviewReport(status="PENDING_API_CALL", score=0.0, ...)` — it never calls an LLM, never checks a `ValidationCriterion`, and never produces a real PASSED/FAILED verdict). The original roadmap plan ("mock the LLM call, assert each of the 6 ValidationCriterions is checked") described logic that does not exist in this file — there are 8 steps, not 6, and no criterion-checking code to test. | Medium — gates strategy deployment per CLAUDE.md's "Gate deployable status... in verification audits" convention, though the actual gating logic turned out to live elsewhere (this file is scaffolding, not the auditor itself). | Done: prompt compilation (system+step+code ordering/inclusion), the stub-report shape and its silent-skip-on-unknown-step-number behavior, `ALL_PROMPTS`'s 8-step/no-gaps/no-duplicates structure, and dataclass round-trips. Remaining 9% is the `if __name__ == "__main__":` demo block, not part of the public API. |
| `investyo_mcp_server.py` | 1,515 | **Done in Phase 5 — 84%** via `tests/test_investyo_mcp_server.py` (99 tests), up from 0%. Not the same server as the GitHub MCP integration; this is the repo's own MCP server exposing ~28 tools/3 resources/1 prompt to MCP clients (Claude Desktop, etc.) over SSH via `mcp_remote_adapter.py`. **Two genuine, previously-undetected bugs found and fixed while reading the file to write these tests:** (1) `configure_alerts`/`send_test_alert` imported from `alerting.notifier`, which doesn't exist (`alerting` is a plain module, `alerting.py`, not a package) — both tools raised `ModuleNotFoundError` on every single call, silently caught and returned as a generic "failed" string; fixed to import from `alerting_mcp.notifier` (the correct sibling module, itself covered by `tests/test_alerting_mcp_notifier.py`). (2) `plot_equity_curve`/`plot_portfolio_equity` wrote artifacts to a hardcoded personal-machine path (`/Users/kevinlee/.gemini/antigravity/brain/<uuid>`) left over from the retired Antigravity IDE — fixed to use `settings.OUTPUT_DIR / "artifacts"`, this codebase's established convention. | High for a 1,515-line, zero-covered surface — realized: the two bugs found are exactly the kind of regression this gap allowed to ship silently (both tools were completely non-functional in production with no test to catch it). | Done: all 3 resources, the prompt, `query_investyo_db`'s SELECT-only guard, `execute_paper_trade`, `update_watch_rules`/`update_universe_tickers` file I/O, `get_portfolio_summary`'s P&L math, PIT-audit tools, `get_model_registry_status`, `get_execution_queue`, `get_trade_journal`, `configure_alerts`/`send_test_alert` (also the regression proof for bug fix #1), both bug-fix regression tests, and representative + argv-level coverage of the ~10 near-identical subprocess-wrapping tools. Remaining 16% is mostly deep exception branches and the `if __name__ == "__main__":` CLI block. |
| `alerting_mcp/notifier.py` (197) + `mcp_remote_adapter.py` (26) | 223 | **Done in Phase 5** — `tests/test_alerting_mcp_notifier.py` (21 tests: each channel handler's success/skip/exception-swallowed path, the `send()` dispatcher's fan-out and per-channel exception isolation, the JSON config store's round-trip and corrupt-file degradation) and `tests/test_mcp_remote_adapter.py` (3 tests: the exact `gcloud compute ssh` command built, stdin/stdout/stderr passed through untouched, exit-code propagation). | Medium — `alerting_mcp` is an alert-dispatch surface (same failure-domain as `observability/alerts.py`). | Done. One real bug caught in review of the new tests themselves, not the source: an early draft of the dispatcher tests patched `notifier._send_email`/`_send_ntfy` module attributes, which `send()` does not consult (it dispatches through the `CHANNEL_HANDLERS` dict, bound to the original functions at module-load time) — the patches were silent no-ops, and one such test was making a real unmocked network call to ntfy.sh. Fixed by patching `notifier.CHANNEL_HANDLERS` entries directly via `monkeypatch.setitem`. |
| GUI **render-wrapper panels** — `gui/panels/gravity_audit.py` (862), `report_viewer.py` (1,279), `observability.py` (1,531), `launcher.py` (974), `strategy_matrix.py` (609), `options_matrix.py` (424), `ai_insights.py` (321), `prompt_registry.py` (434), `ai_control_center.py` (337), `live_inventory.py` (392), `pairs.py` (462), `market_data.py` (263), `analytics.py` (772), `paper_monitor.py` (124), `settings_manager.py` (180), `validation_lab.py` (231), `reports_library.py` (237), `analytics_signals.py` (367), `_shared.py` (208) | ~10,000 total | **No owning file for any panel; most have 0-1 incidental references.** A distinct, previously-undocumented pattern has emerged since the original audit: several features now split into a *logic* module at `gui/<feature>.py` (tested — e.g. `gui/ai_control_center.py`, `gui/ai_insights_panel.py`, `gui/gravity_ai_panel.py` all have owning suites) **plus** a *render-wrapper* module at `gui/panels/<feature>.py` that is the actual Streamlit tab entry point and now also contains real pure logic — **and the wrapper has zero direct coverage.** Concretely, `gui/panels/gravity_audit.py` (modified same-day as this audit) contains `_derive_step_status()` — a multi-branch PASS/FAIL classifier whose own docstring says an earlier version of this exact logic **"misreported a passing step as a failure"** — and `_parse_trailing_json()`, a hand-rolled brace-matching JSON extractor over arbitrary subprocess stdout; neither has a single test. | Medium-High for the pure helpers specifically (proven track record of a real bug in `_derive_step_status`), Medium for the panels overall (operator surface, `safe_panel()` catches render exceptions so a broken panel degrades to an error box rather than crashing the app — but a *wrong* rendering, like the prior `_derive_step_status` bug, does not trip that safety net). | Same "extract and test pure helpers" recommendation as the original audit, now with concrete, high-value, low-effort targets: `gui/panels/gravity_audit.py::_derive_step_status` (PASS/FAIL branch matrix — status key, `overall_pass` key, `step_3_5_discrepancy_analysis`'s conclusion string, `step_7_simulation_impact`'s dual-status join, and the `"—"`/`False` fallback for an unrecognized shape) and `::_parse_trailing_json` (no `}` in text, unbalanced braces, malformed JSON after brace-matching, valid trailing JSON preceded by unrelated stdout noise) are both pure functions with no Streamlit dependency and can be unit tested today with zero mocking. **`_derive_step_status`/`_parse_trailing_json` done in Phase 5** — `tests/test_gravity_audit_panel_helpers.py` (27 tests covering every branch of both functions, including the `step_3_5`/`step_7` legacy shapes and the depth-counting brace matcher's nested/unbalanced/dangling-brace edge cases). The other ~18 panels and their remaining pure helpers (`_load_gravity_report`/`_load_validation_summaries`'s cache-key derivation, and equivalents in the other 18 render-wrapper files) are still open. |
| `webapp/` (Pilots PWA — React/TypeScript, ~separate stack from the Python suite above) | n/a (JS/TS) | **2026-08-05 re-measured: 39/45 screens have tests, 36/55 components have tests — grown substantially but no longer "fully done" now that the app itself has grown (was 9 files/100 tests covering every then-existing screen; the screen/component count has since roughly doubled and 6 screens + 19 components shipped since without matching tests).** See Phase 6 for the specific untested files (`charts.tsx`, the `Settings*` sub-screens, `BottomNavigation.tsx`) and the mock/live API parity mechanism (compile-time `typeof liveApi` type annotation, not a runtime test). | Medium — real, shippable consumer-facing surface. | Partially open — see Phase 6 items. |
| ~~`gravity/__init__.py`~~ | — | **Deleted 2026-07-10** — this was a dead, unimported duplicate of `Gravity AI Review Suite.py`'s `GravityAIAuditor` class from an incomplete Phase 4b package extraction (see `docs/IMPROVEMENT_PLAN.md`'s "4b Gravity split" row). Its 10 audit steps with no live-file equivalent were migrated into `Gravity AI Review Suite.py` (step_75–step_85) before deletion. No longer a coverage gap. | — | — |
| `engine/llm_commentary.py` / `engine/opal_research.py` | 194 / 107 | **0% (measured, run 30997767741) — new 2026-08-05 finding.** A matched pair of thin CLI preview wrappers (`python -m engine.opal_research SYMBOL`, `python -m engine.llm_commentary SYMBOL`); both docstrings state they reuse already-tested business logic and don't reimplement it, but neither has any test file, import, or subprocess invocation anywhere in `tests/`. | Medium — CLI-only preview tools for Tier-9 features; a regression in arg parsing or the preview-mode stub for `alerting.notify` could silently send a real ntfy push instead of a preview. | Open — see Phase 6. |
| `execution/overnight_guardrails.py` | 47 | **0% (measured) — new 2026-08-05 finding.** Own module docstring: *"NOT YET WIRED INTO THE LIVE ORDER PATH... it currently enforces nothing in a real trading cycle despite its execution-facing name."* Dead code today. | Low today (nothing calls it) / High if ever wired in without tests first — an execution-facing module with an execution-sounding name but zero verification is a foot-gun for whoever wires it in later believing it's already proven. | Open — see Phase 6. |
| `reporting/pairs_snapshot.py` | 209 (125 stmts) | **15% (measured) — new 2026-08-05 finding.** Writer side of the pairs-trading JSON artifact (`output/pairs.json`) the Pilots PWA's pairs radar reads. Only the READER (`pilots/pairs.py`) is tested, via a hand-built fixture in `test_pilots_api.py::TestPairsRadar` — a writer-side schema/rounding/honesty-on-NaN regression would silently corrupt the mobile Pairs tab with no test catching it. | Medium — consumer-facing artifact, writer-side unverified. | Open — see Phase 6. |
| `data/rag_index.py` | 428 (181 stmts) | **21% (measured) — lower than expected given it has an owning `tests/test_rag_index.py`.** Illustrates a general pattern this round surfaced: a real, passing test file does not guarantee high statement coverage — `test_rag_index.py` exercises construction/insertion/search happy paths (the Round-1/Round-2 libomp-collision fix's own concern) but leaves much of the FAISS-index-management and error-branch surface unexercised. | Low-Medium — sentiment-document archive, not trading-critical. | Open, lower priority — see Phase 6. |
| `volatility/bootstrap_iv_history.py` | 73 | **0% (measured) — new 2026-08-05 finding.** Standalone CLI backfill script (`main()` only, not imported elsewhere) that seeds the `iv_history` table feeding `volatility/iv_engine.py`'s IV-rank/VRP computations — which gate options-selling per this codebase's `true_ivr > 50, VRP > 0.02` convention. A bug here could silently corrupt data behind a real risk gate. | Medium — feeds a documented options-selling risk gate, though only via a one-time/occasional manual backfill, not the live cycle. | Open — see Phase 6. |
| `scripts/auditor/stockpy_codebase_auditor.py` | 927 | **Untestable-by-measurement (excluded from `.coveragerc` via the `scripts/*` omit) + zero test file — new 2026-08-05 finding.** A second, independent, stdlib-only static codebase auditor (10 audit areas: architecture, security, config, data pipeline, strategy, backtesting, Robinhood/execution safety, error handling, code quality, known-issue heuristics) — not wired into CI, `make verify`, or any other script; the `scripts/*` coverage omit means a regression here would never show up as a coverage drop either. Read-only/non-destructive (parses via `ast`, never imports/executes audited code), so the blast radius of a bug is "silently misses findings," not "crashes/corrupts something." | Medium — a second, newer instance of exactly the "big non-pytest static auditor" pattern `Gravity AI Review Suite.py` already represents, without that file's compensating traceability investigation (Phase 5) having been done for it yet. | Open — see Phase 6. |
| `cli_introspect/targets.py` / `cli_introspect/capture.py` | 39 / 143 (9 / 64 stmts) | **0% / 39% (measured) — lower than the "solid" characterization a same-named `test_cli_introspect.py` file would suggest.** The 10 end-to-end subprocess-capture tests exercise the happy path of a couple of manifest targets; most of the module's branch/error-handling surface (and `targets.py` entirely) isn't reached. | Low — build-tooling for the command manifest, not trading-critical. | Open, low priority. |

---

## Recommendations — prioritized roadmap

### Phase 1 — Coverage tooling + this document *(done)*
- Added `pytest-cov` (requirements.txt) and `.coveragerc` (source scoping, sensible omits for
  `tests/`, `.venv/`, GUI render code) so `pytest --cov` produces real numbers.
- Landed this analysis document as the shared reference for the roadmap.

### Phase 2 — Continuous integration *(done, reconciled with a concurrent change)*
- An independent change added `.github/workflows/ci.yml` (offline suite, `-m "not network"`,
  Python 3.12) while this work was in flight. Rather than land a duplicate workflow, this change
  extends that one with `--cov`/coverage-summary steps instead of adding `tests.yml`.
- No coverage floor yet — this baseline measurement is the first step toward one.

### Phase 3 — Write the owning test files *(done — 5 of the original 6; `reporting_engine.py` deleted upstream)*
Closed the highest risk × gap items:
1. `test_simulation_engine.py` — restores the "prove strategies here" gate (258 lines → 81%).
2. ~~`test_reporting_engine.py`~~ — moot; `reporting_engine.py` was deleted upstream as dead code
   before this landed.
3. `test_evaluation_engine.py` (43%), `test_main_orchestrator.py` (51%), `test_strategy_engine.py`
   (76%) — pin the large orchestration/evaluation/signal surfaces directly rather than incidentally.
4. `test_research_engine.py` (52%) — covers sector/dividend/leverage/momentum-slope/slippage/
   options-vol-edge/CoVaR known-answer cases.

### Phase 4 — Long tail + ratchet *(all items closed via Phase 5 below)*
- ~~Register `@pytest.mark.slow` in `pytest.ini`~~ — **done** (both `network` and `slow` are now
  registered markers).
- ~~GUI panel helpers~~ and ~~`data/robinhood_client.py`~~ — the latter **done** (94%, Phase 5); the
  former partially done (the `gravity_audit.py` pure helpers), remaining ~18 panels still open — see
  the module-level table.
- ~~Resolve the stray root files~~ — **done**, as a deletion (both `test_gravity.py` and
  `test_mock_abc.py` turned out to be dead weight, not genuine gaps — see the hygiene section above).
- ~~Coverage-floor ratchet in CI~~ — **done**, `fail_under = 58` in `.coveragerc` — see Phase 5 item 8.

### Phase 5 — Findings from the 2026-07-14 re-audit
Ranked by risk × gap, reflecting how the codebase evolved since Phase 3. First implementation pass
(same day) landed items 2 and 6, plus the stray-root-file half of item 5:

1. **Mirror `Gravity AI Review Suite.py`'s highest-value `step_*` invariants into pytest** —
   **investigation complete (2 rounds, 38 of ~50 total step blocks read), 3 of 4 confirmed genuine
   gaps mirrored, 1 deferred.** Round 1 (20 steps, `step_61`–`step_91`, the later Tier/Task-numbered
   audits) found every one already independently covered by a dedicated test file — e.g.
   `step_78_advisory_agent_audit` ↔ `tests/test_advisory_agent.py`,
   `step_89_rolling_beta_lookahead_audit` ↔ `tests/test_indicators_lookahead.py`. Round 2 (18 steps,
   the earlier `step_1`–`step_40`/`step_45`/`step_58` inline-block layer, chosen because
   `tests/test_gravity_mirrored_invariants.py`'s own precedent found its 2 gaps exactly there) found
   the SAME result — near-total, often stricter, existing coverage (CPCV/PBO/DSR deployability, the
   full Kelly/vol-target sizing stack, the HMM regime detector, the tail-scenario stress gate, and all
   six bugs in the 2026-06 regression audit were each independently and thoroughly covered). This
   revises the original framing entirely: the file having zero tests of its own never meant its
   checked behaviors were untested — the real, and much smaller, gap was verified traceability.
   **The 4 genuine gaps found across both rounds, and their disposition:**
   - `step_72` check 8 — `CANONICAL_REGIMES` cross-checked against `dto_models.MacroEconomicDTO`'s
     actual regime strings (existing tests only compared two hardcoded literal sets, tautologically).
     **Mirrored**: `tests/test_signal_weight_validation.py::TestCanonicalRegimesMatchMacroEconomicDTO`
     (5 tests, one per real regime-triggering scenario, constructing a real DTO for each).
   - `settings.DRY_RUN`'s default (`False`) — the safety-critical master switch gating
     `OrderManager._submit_with_retry`, asserted nowhere despite `test_settings.py::test_settings_defaults`
     covering many other defaults. **Mirrored**: one-line addition to that existing test.
   - `TieredCostModel.estimate_round_trip_cost()` — never called by any test (only its constituent
     `calculate_cost()` was); encodes a real behavioral point (sec_fee/taf come from the sell leg
     only, not double-counted). **Mirrored**: `tests/test_cost_model.py::test_estimate_round_trip_cost_aapl`
     + a zero-trade-value no-division-by-zero test.
   - A zero-`position_size`→`$10,000` default guard in `pipeline/production_steps.py::StrategyEvalStep`
     (~line 750-756) — real behavioral half of the documented 2026-06 crash-fix; only checked today via
     a fragile source-string grep (in Gravity itself). **Deferred, not mirrored**: `StrategyEvalStep.run()`
     is a ~450-line synchronous method with many real engine dependencies (`StrategyEngine`,
     `EvaluationEngine`, `ml.meta_bootstrap`, `global_registry.run_pre_compute`, …) — exercising it
     end-to-end just to reach one defensive-default branch is disproportionate effort relative to the
     other three closed gaps. A future pass could extract this guard into a small pure function
     (`ctx.dashboard_df` in, mutated `position_size` column out) to make it cheaply testable in
     isolation — that refactor is out of scope for a coverage-only change.
   Remaining low-value: `step_82`–`step_86` (LLM/AI-panel audits) and the confirmed cosmetic duplicate
   step-numbering ("STEP 22"/"STEP 23" each label two unrelated audits internally) were not
   investigated further given how consistently the pattern held across 38 already-checked steps.
2. ~~Unit test `gui/panels/gravity_audit.py::_derive_step_status` and `::_parse_trailing_json`.~~
   **Done** — `tests/test_gravity_audit_panel_helpers.py` (27 tests). The other ~18 render-wrapper
   panels' pure helpers remain open (see item 5).
3. ~~Owning test for `ai_verification_prompts.py`'s `GravityAIAuditor` class~~ **done** (91% via
   `tests/test_ai_verification_prompts.py`, 15 tests) — see the module-level table for a correction to
   this doc's original characterization of what the class actually does.
4. ~~`investyo_mcp_server.py`~~ **done** (84% via `tests/test_investyo_mcp_server.py`, 99 tests), up
   from 0%. Also caught and fixed two real, previously-shipped-broken tools along the way — see the
   module-level table for detail.
5. **Carry forward from Phase 4:** ~~`data/robinhood_client.py` owning test~~ **done** (94% via
   `tests/test_robinhood_client.py`, 37 tests). The broader GUI render-wrapper panel helper
   extraction beyond the two functions closed in item 2 remains *not yet started* — 18 panels remain.
   ~~The stray-root-file rehoming~~ **done**, as a deletion rather than a rehome — see the hygiene
   section above for why both files turned out to be dead weight rather than genuine gaps.
6. ~~`alerting_mcp/notifier.py` + `mcp_remote_adapter.py`~~ — **done**, `tests/test_alerting_mcp_notifier.py`
   (21 tests) + `tests/test_mcp_remote_adapter.py` (3 tests). See the module-level table for a real bug
   this caught in the *tests'* own mocking approach (not the source) along the way.
7. ~~`webapp/` (Pilots PWA) component tests~~ **fully done.** Closed in three waves: this branch added
   `@testing-library/react` (previously absent; component tests were structurally impossible before
   this) and tests for `format.ts` + `FollowModal.tsx`; concurrently on `main` (a different session,
   PRs #266–#268, reconciled in by rebase) a more complete effort shipped `Marketplace`/`PilotDetail`/
   `Portfolio`/`client.ts` coverage, live-verified against a real running backend; then `Onboarding.tsx`
   + `onboarding.ts` (the last screen and its localStorage completion-marker module) were closed out as
   a direct follow-up — see the module-level table for full detail. Net: every screen now has a test
   file, 100 tests total across 9 files.
8. ~~Coverage-floor ratchet in CI~~ **done** — `.coveragerc` now sets `fail_under = 58`, a few points
   below the measured whole-suite baseline (61%, `pytest -m "not network" --cov`, verified this pass:
   passes cleanly at current coverage, verified to actually fail — non-test-failure exit code 1 — when
   coverage genuinely drops below the floor). `ci.yml` needed no changes beyond a clarifying comment,
   since `--cov` already reads `.coveragerc` and pytest-cov enforces `fail_under` automatically.

**Bonus finding, not in the original Phase 5 list:** verifying the full offline suite after the above
changes surfaced `tests/test_harness_buyhold.py` making a real, unmarked live network call to
yfinance — fixed by adding `@pytest.mark.network` (see hygiene section above). Also verified: the full
`pytest -m "not network"` suite is green after every change in this pass (5,025 collected, 0 failures).

### Phase 6 — Findings from the 2026-08-05 re-audit

Methodology this round differed from Phase 5 in one deliberate way, and it's worth stating up front
because it changed what counted as a "real" finding. A local `pytest -m "not network" -n auto --cov`
run in this sandboxed dev environment produced 7 test failures and a coverage number corrupted to 14%
by repeated `pytest-xdist` worker crashes (`node down: not properly terminated`). Rather than take
that at face value, each failure was individually re-run in isolation to separate genuine bugs from
local-environment artifacts:

- **2 of the 7 ("failures") were pure invocation mistakes, not codebase bugs.**
  `test_refresh_validations.py::TestRunValidations::test_all_registered_adapters_run_end_to_end` (a
  ~5-minute real `STRATEGY_REGISTRY` sweep) and `test_train_lgbm.py::test_training_produces_model_and_real_metrics`
  (pinned via `@pytest.mark.xdist_group("train_lgbm")` to build its shared fixture once) both pass
  cleanly in isolation. Reproducing them requires the exact flags `ci.yml`'s `test-slow` job uses
  (`--dist=loadgroup`, `--timeout=600`) — this local run used neither, so the "failures" were an
  artifact of a simplified reproduction, not a real gap. **Lesson for future re-audits: replicate CI's
  exact per-job flags (marker filter, `--dist`, `--timeout`) before trusting a local coverage run's
  failures as real.**
- **1 of the 7 (`test_edgar_fundamentals.py::TestThreadSafety::test_throttle_serializes_request_issuance`)
  passes cleanly and fast (0.22s) alone** and doesn't appear anywhere (slow list or failure list) in the
  real CI log for the current HEAD — almost certainly collateral misattribution from a crashed sibling
  xdist worker, not a real flake.
- **The remaining 4 (all in `tests/test_data_api_chat.py::TestContextField`) were a genuine, confirmed,
  reproducible bug** — see below.

#### The `_iter_blocking` StopIteration hang — found, confirmed, and fixed

`api/data_api.py::_iter_blocking()` bridges a synchronous LLM-SDK stream iterator into an async
generator via `await loop.run_in_executor(None, next, it)`, relying on `except StopIteration: return`
to detect exhaustion. This is a **known, documented Python/asyncio hazard**: `asyncio.Future.set_exception()`
explicitly refuses to accept `StopIteration` (`TypeError: StopIteration interacts badly with generators
and cannot be raised into a Future` — PEP 479-related), so when the wrapped iterator is exhausted inside
the executor thread, the internal future-chaining callback that would normally deliver that exception
back to the awaiting coroutine raises instead, and the `await` **hangs forever** rather than completing
with an error. Confirmed with a minimal, isolated repro script reproducing the exact `TypeError` and the
subsequent permanent hang (needed `asyncio.wait_for(..., timeout=8)` to terminate at all).

This is exactly the code path `tests/test_data_api_chat.py::TestContextField`'s four Gemini/Anthropic
"context field" tests hit — their fake SDK modules return `iter([])` (zero chunks; the tests only need to
inspect the call kwargs, not exercise real streaming), which is precisely the exhausted-iterator case.
Under `--timeout=180 --timeout-method=thread`, pytest-timeout's watchdog detects the hang and force-kills
the whole worker process (`os._exit()`), which is exactly what pytest-xdist reports as
`"worker crashed while running <test>"` — a failure mode that reads like flaky test infrastructure,
not a deterministic code bug, which is itself worth flagging (see the process recommendation below).

**Fixed** by replacing the bare `next(it)` with `next(it, SENTINEL)` (a sentinel default) so the
executor call never raises `StopIteration` in the first place — verified via the same repro script (both
the empty- and non-empty-iterator cases now complete correctly with no hang) and via the real test suite
(`tests/test_data_api_chat.py` — all 10 tests, including the 4 previously-hanging ones — now pass in
2.24s; the broader `test_data_api.py`/`test_data_api_ai*.py` suite — 96 tests — also passes; `ruff
--select=F821,F822,F823,E9` clean).

**Important honesty check on how this fits into the "is CI currently broken" question — it doesn't:**
pulling the actual GitHub Actions job log for `test (offline suite)` on the current `main` HEAD (commit
`45ad294`, [run 30997767741](https://github.com/kevinmarko/Stockpy/actions/runs/30997767741)) shows
**9,188 passed, 0 failed, 25 skipped, 68.42% coverage** — `test_data_api_chat.py` is not in the failure
list, the slowest-50 list, or anywhere flagged. The hang is real and the fix is correct and safe (a
pure, behavior-preserving change verified not to affect the non-exhausted-iterator case), but it did not
reproduce on the pinned CI runner (Python 3.12.13) for this exact commit the way it did in this
sandboxed local environment (Python 3.12.3) — plausibly because whichever exception actually propagates
out of the broken `Future.set_exception()` path differs subtly across CPython patch versions, and any
non-`StopIteration` exception would be caught by `chat_endpoint`'s own outer `except Exception`, letting
the stream complete (with a swallowed error) rather than hang. **The fix is applied regardless**, because
the underlying hazard — a real Gemini/Anthropic stream that happens to return zero chunks before
completing (a network hiccup, an immediate content-filter rejection) — hitting this exact code path in
*production*, on *any* Python patch version where the hang manifests, would silently pin a live
request/executor-thread pair forever. Relying on undefined, version-sensitive behavior for whether an
edge case hangs vs. silently degrades is not a acceptable place to leave a paid-LLM-calling streaming
endpoint.

**Process recommendation this finding motivates:** a `pytest-xdist` `"worker crashed while running X"`
message should be treated with the same seriousness as a plain assertion failure, not written off as
"CI/infra flakiness" — in this case it was the only visible symptom of a genuine, fixable bug. Consider
adding a note to this effect near `ci.yml`'s worker-count/timeout configuration.

#### New zero/low-coverage modules (ground-truth, from the real CI coverage report)

Pulling the complete, authoritative coverage table from the same CI run (rather than a locally-reproduced
one) surfaced several genuine, previously undocumented gaps — added to the module-level table above:
`engine/llm_commentary.py` (0%), `engine/opal_research.py` (0%), `execution/overnight_guardrails.py`
(0%, confirmed dead code), `volatility/bootstrap_iv_history.py` (0%), `reporting/pairs_snapshot.py`
(15%), `data/rag_index.py` (21%, despite an owning test file — see the general lesson below),
`cli_introspect/targets.py` (0%)/`cli_introspect/capture.py` (39%), and `scripts/auditor/stockpy_codebase_auditor.py`
(a 927-line, brand-new, second static codebase auditor with zero tests, excluded from coverage
measurement entirely by the `scripts/*` omit rule, and not wired into CI/`make verify`/anything else).

**General lesson surfaced by `data/rag_index.py`, `cli_introspect/`, and similar cases:** "has an owning
`test_*.py` file" and "high statement coverage" are not the same claim. A real, passing, well-scoped test
file can still leave the bulk of a module's error-handling/branch surface unexercised if it only proves
out the happy path. The source-vs-test inventory approach used in Phase 3-5 (does a test file reference
this module at all?) is a good first filter but should be periodically cross-checked against real
`pytest-cov` numbers, which is exactly what surfaced this gap between "solid" (by the import-reference
heuristic) and 21% (by actual statement execution).

#### `.coveragerc` vs. this doc's own stated intent — a real discrepancy

Phase 1's own description of `.coveragerc` claims "sensible omits for `tests/`, `.venv/`, **GUI render
code**." The actual `.coveragerc` `omit` list is `tests/*, .venv/*, */__pycache__/*, gravity/*, scripts/*,
setup.py, conftest.py, */conftest.py` — **there is no `gui/*` entry**. The ~19 `gui/panels/*.py`
render-wrapper files therefore ARE included in the measured 68.42% total, each sitting between 5% and
46% statement coverage (Streamlit rendering code is inherently hard to unit-test without something like
`streamlit.testing.v1.AppTest`, which a few panels have started adopting — see
`tests/test_ai_control_center.py`). This is a genuine open question, not a bug to silently fix either
way: either (a) `.coveragerc` should gain the `gui/panels/*` omit this doc has claimed exists since
Phase 1 (which would raise the reported TOTAL, giving a cleaner signal on the engine/signal/execution
code that actually trades money), or (b) the omission was never really the intent and GUI panels should
continue being pulled toward real coverage via the pure-helper-extraction pattern already underway (11 of
18 panels have some direct helper-level test now, per the current pass's investigation) — in which case
this doc's Phase 1 description should be corrected instead. **Recommendation: (b)** — continuing the
extraction pattern is real, in-progress, forward progress with a proven track record (a real bug was
caught in `gravity_audit.py`'s `_derive_step_status` this way); adding a blanket omit would erase that
signal. But the doc's Phase 1 text should be corrected to stop claiming an omit that doesn't exist.

#### webapp/ — re-measured, no longer "fully done"

The Phase 5 "fully done, every screen has a test file" claim is now stale purely because the app grew:
**39 of 45 screens** and **36 of 55 components** have test files today (was 100% of a smaller app).
Untested screens: `SettingsBrokers.tsx`, `SettingsData.tsx` (1,075 lines — the single largest untested
webapp file), `SettingsGeneral.tsx`, `SettingsModules.tsx`, `SettingsLayout.tsx` (thin shell),
`SettingsUniverse.tsx` (trivial). Untested components include `charts.tsx` (859 lines — the largest
untested file in `components/`; its sibling `ui.tsx` does have `ui.test.tsx`), `BottomNavigation.tsx`
(216 lines — core mobile nav, notable given this codebase has previously flagged mobile
nav-reachability as a recurring failure mode), and 17 others. Separately: **webapp has no coverage
tool or enforced threshold at all** (`package.json`'s `test` script is a bare `vitest run`, no
`--coverage`, no `@vitest/coverage-v8`/`-istanbul` dependency, no `coverage` key in `vite.config.ts`'s
`test` block) — unlike the Python side's ratcheted `.coveragerc` floor, nothing stops webapp coverage
from silently regressing. The mock/live API parity guarantee (`client.ts`'s `export const api: typeof
liveApi = USE_MOCK ? mockApi : liveApi`) is enforced only at TypeScript compile time, not by a runtime
`vitest` assertion — a small `Object.keys(mockApi)` vs `Object.keys(liveApi)` cross-check test would make
this guarantee visible in test output and catch the one blind spot the type annotation can't (an extra,
unused method on `mockApi`).

#### A verification gap automated tests structurally cannot close

`docs/known_issues/robinhood_device_approval_login_hang_risk.md` already documents this honestly and
in detail, but it's worth surfacing here explicitly as a *test-coverage* finding rather than leaving it
buried in a known-issues doc: the subprocess/deadline/kill-escalation plumbing around Robinhood's
device-approval login is fully unit-tested (`tests/test_robinhood_login.py`, against a stub worker that
deliberately hangs), but the actual real-world behavior this design rests on — that omitting `mfa_code`
triggers a genuine push challenge, that a real approval tap flips `get_prompts_status` within the
180s deadline, whether a denial is distinguishable from an ignored prompt, and whether repeated attempts
trigger Robinhood-side rate-limiting — **cannot be exercised by any pytest test**, sandboxed or otherwise,
without a real Robinhood account and a real phone. **Recommendation:** maintain this as an explicit
manual verification checklist (a short runbook entry, not a pytest file) that gets walked through once
against a real account before this login path is treated as fully proven rather than "correct by
construction" — and note in `docs/RUNBOOK.md` that this is the one login-path risk automated coverage
cannot retire.

#### Recommendations — prioritized

1. **(Done as part of this pass)** Fix `_iter_blocking`'s `StopIteration`/`run_in_executor` hang in
   `api/data_api.py` — see above.
2. **`engine/llm_commentary.py` / `engine/opal_research.py` (0%, cheap)** — both are thin CLI wrappers
   around already-tested logic; a basic argv-parsing + happy-path smoke test each (following the pattern
   `tests/test_investyo_mcp_server.py` used for similar subprocess-wrapping tools) would close both for
   low effort.
3. **`scripts/auditor/stockpy_codebase_auditor.py` (927 lines, 0%)** — the highest-value target this
   round after the confirmed bug fix. Stdlib-only, pure static analysis (`ast`-based), fully
   deterministic given a fixture directory tree — should be cheap to test thoroughly (construct small
   fixture trees per audit area, assert findings/severities/exit-code-vs-`--fail-on` behavior) without
   needing the same two-round traceability investigation `Gravity AI Review Suite.py` required, since
   this file is small enough to test directly rather than mirror.
4. **`execution/overnight_guardrails.py` (47 lines, dead code, 0%)** — either add tests now, before it's
   wired into the live order path (so the wiring PR isn't the first time its logic is exercised), or
   explicitly flag it as shelved/experimental in a code comment so a future reader doesn't assume
   "execution-facing name" implies "already proven."
5. **`reporting/pairs_snapshot.py` writer-side tests (15%)** — schema, rounding, and NaN-honesty tests
   for the writer, mirroring the rigor `pilots/pairs.py`'s reader-side fixture already has.
6. **`volatility/bootstrap_iv_history.py` (0%)** — a basic CLI-argument + happy-path test; feeds a real
   options-selling risk gate (`true_ivr`/`VRP` thresholds) even though it's a manual/occasional backfill.
7. **webapp: `charts.tsx`, the four real `Settings*` sub-screens, `BottomNavigation.tsx`** — the highest
   remaining untested surface in webapp by line count and, for `BottomNavigation.tsx`, by known
   failure-mode history (mobile nav-reachability).
8. **webapp: add a coverage tool + threshold** (`@vitest/coverage-v8` + a `coverage` block in
   `vite.config.ts`'s `test` config) so webapp gets the same ratchet-against-regression the Python side
   already has.
9. **webapp: formalize the mock/live API parity check as an explicit `vitest` test**, not only a
   compile-time type annotation — closes the one blind spot (an unused extra method on `mockApi`) the
   `typeof liveApi` annotation can't catch, and makes the guarantee visible in test output.
10. **Continue the GUI panel pure-helper extraction** — `options_matrix.py`, `ai_insights.py`,
    `prompt_registry.py`, `live_inventory.py` remain the four panels with genuinely zero test attention
    of any kind (`options_matrix.py::_compute_directive_row` and `prompt_registry.py::_pr_resolve_source`
    are the two highest-value single targets — both real multi-branch classification/resolution logic).
    `validation_lab.py::_summary_row` is a near-miss: the panel has a test file, but it only proves the
    render function is importable, never touching this obviously-testable pure helper.
11. **Raise `.coveragerc`'s `fail_under` floor** from 58 toward the real 68.42% baseline (with margin,
    e.g. 63-65) now that real coverage has moved well past the old floor — the ratchet's own comment
    says to do this as coverage improves; it hasn't been revisited since Phase 5. Resolve the GUI-omit
    documentation-vs-config discrepancy above at the same time (recommended: correct the doc, keep the
    panels measured).
12. **Add the manual Robinhood device-approval verification checklist** to `docs/RUNBOOK.md` — the one
    login-path risk this codebase's own known-issues doc already flags as unverifiable by pytest alone.
