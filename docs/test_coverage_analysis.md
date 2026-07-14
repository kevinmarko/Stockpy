# Test-Coverage Analysis

## Executive summary

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
`data/edgar_fundamentals.py`, `watch_engine.py` all landed with owning suites). But two categories
accumulated real, unexercised gaps: (1) **`Gravity AI Review Suite.py`**, the codebase's biggest
single file at ~14,900 lines and its own-documented deployability gate, has **zero `def test_`
functions of its own** and only 2 of its ~30 `step_*` audit functions have been mirrored into real
pytest assertions — the rest are exercised only by a human clicking "Run" in the GUI's Gravity Audit
Logs tab; and (2) a **GUI "render-wrapper" pattern** has emerged where `gui/<feature>.py` holds tested
pure logic but the actual Streamlit tab entry point at `gui/panels/<feature>.py` (which now also
contains real, non-trivial pure helper functions) has no direct test coverage at all. Full detail and
a prioritized roadmap (Phase 5) are below.

**Phase 5 implementation status:** a first pass (same day) closed the two lowest-effort/highest-signal
items — the `gravity_audit.py` pure-helper tests and the `alerting_mcp`/`mcp_remote_adapter` owning
suites — and, in the course of verifying the full suite, deleted two dead stray root test files and
fixed one unrelated pre-existing test that was silently making a live network call. The two largest
items (`Gravity AI Review Suite.py` step mirroring, GUI render-wrapper helpers for the other ~18
panels) remain open; see the Phase 5 section for exact status per item.

---

## Current state

### The numbers

| Metric | Original audit | 2026-07-14 re-audit |
|--------|-----------------|----------------------|
| `test_*.py` files under `tests/` | 203 | **266** |
| `def test_` functions | 3,446 | **4,547** |
| `class Test*` classes | 664 | **862** |
| Source `.py` files under version control (excl. tests) | ~180 (estimated) | **234** |
| pytest collection scope | `testpaths = tests` (pytest.ini) | unchanged |
| Coverage measurement | none → added in this audit's Phase 1 | `pytest-cov` + `.coveragerc` in place |
| CI automation | none → added independently in parallel | `.github/workflows/ci.yml` runs `-m "not network"` + `--cov` on every push/PR |
| `pytest.ini` `markers` section | absent (unregistered `@pytest.mark.slow` warning) | **present** — `network` and `slow` are both registered; the original hygiene finding is resolved |

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
| `data/robinhood_client.py` | 345 | **Still no owning file** — only indirect stub coverage via `test_portfolio_sync.py`'s fakes; unchanged since the original audit. | Medium — account/watchlist discovery; `_suppress_rs_output` + `discover_universe`/`discover_watchlists` untested directly. | Still open: owning `test_robinhood_client.py` with a fake `robin_stocks` — discovery union/dedupe, per-list failure skip, output-suppression context manager, unauthenticated short-circuit. |
| **`Gravity AI Review Suite.py`** | **~14,900** | **Effectively 0% by direct execution.** The file has **zero `def test_` functions of its own** and (by its own module docstring, since it's a space-containing filename that can't be `import`ed normally) is architecturally excluded from ordinary pytest collection. Of its ~30 `step_*` audit functions, only **2 specific invariants** have been reverse-engineered and mirrored into real pytest assertions, in `tests/test_gravity_mirrored_invariants.py` — whose own docstring documents *why*: doing that audit found one of the two ("`sahm_rule_indicator` has no `None`→float coercion, unlike its three FRED siblings") to be **"a genuine, previously-undocumented crash risk."** The other ~28 step functions are exercised only by a human clicking "Run" in the GUI's Gravity Audit Logs tab (`gui/panels/gravity_audit.py`) or via `Gravity_Verification_Report.json` — neither path runs in CI. | **Highest of any module in this audit.** This file is the platform's own documented deployability gate — cited by name for schema invariants, PBO/DSR gating, options-matrix integrity, help-content anchor validation, DB-backend resilience, and ~15 other "step_NN" checks referenced throughout `CLAUDE.md`. A regression here silently weakens every one of those gates and CI would stay green. | Two-track fix, matching the pattern `test_gravity_mirrored_invariants.py` already established: (1) **mirror**, don't `import` — since the file can't be imported (space in filename) and is one 14,900-line script, add pytest assertions that reproduce each `step_*`'s actual invariant against the live code path (as the existing 2 do), prioritizing the steps CLAUDE.md leans on most (PBO/DSR deployability gate, options-matrix strike/delta integrity, DB-backend-resilience, help-anchor validation); (2) track progress with a simple checklist (e.g. a table of `step_NN` → mirrored y/n) in this doc or a code comment, since "spot-check 2 of 30" doesn't scale as an ad-hoc process. |
| `ai_verification_prompts.py` (the *other* "Gravity AI Auditor" — 6-step static-analysis + LLM sandbox verifier, distinct from `Gravity AI Review Suite.py` above) | 337 | **Partial** — `tests/test_gravity_prompt_sourcing.py` covers prompt-registry-vs-baseline sourcing (the `SYSTEM_PROMPT`/`ALL_PROMPTS` fallback contract) but the `GravityAIAuditor` class itself (~260 of the 337 lines — the actual 6-step static-analysis/sandbox logic) has no direct test. | Medium-High — gates strategy deployment per CLAUDE.md's "Gate deployable status... in verification audits" convention. | Owning `test_ai_verification_prompts.py`: construct a `GravityAIAuditor` against a fixture module/strategy, mock the LLM call, assert each of the 6 `ValidationCriterion`s is checked and `AIReviewReport` aggregates pass/fail correctly. |
| `investyo_mcp_server.py` | 1,515 | **0% — zero test references anywhere in `tests/`.** Not the same server as the GitHub MCP integration; this is the repo's own MCP server exposing platform tools/resources to MCP clients (Claude Desktop, etc.). | High for a 1,515-line surface with no coverage at all — an MCP tool-schema or handler regression here is invisible to CI and would only surface as a broken client integration. | New `test_investyo_mcp_server.py`: enumerate registered tools/resources and assert schema shape; call each handler with a fixture request and assert it degrades (not crashes) on missing upstream state, mirroring the dead-letter convention used everywhere else in this codebase. |
| `alerting_mcp/notifier.py` (197) + `mcp_remote_adapter.py` (26) | 223 | **Done in Phase 5** — `tests/test_alerting_mcp_notifier.py` (21 tests: each channel handler's success/skip/exception-swallowed path, the `send()` dispatcher's fan-out and per-channel exception isolation, the JSON config store's round-trip and corrupt-file degradation) and `tests/test_mcp_remote_adapter.py` (3 tests: the exact `gcloud compute ssh` command built, stdin/stdout/stderr passed through untouched, exit-code propagation). | Medium — `alerting_mcp` is an alert-dispatch surface (same failure-domain as `observability/alerts.py`). | Done. One real bug caught in review of the new tests themselves, not the source: an early draft of the dispatcher tests patched `notifier._send_email`/`_send_ntfy` module attributes, which `send()` does not consult (it dispatches through the `CHANNEL_HANDLERS` dict, bound to the original functions at module-load time) — the patches were silent no-ops, and one such test was making a real unmocked network call to ntfy.sh. Fixed by patching `notifier.CHANNEL_HANDLERS` entries directly via `monkeypatch.setitem`. |
| GUI **render-wrapper panels** — `gui/panels/gravity_audit.py` (862), `report_viewer.py` (1,279), `observability.py` (1,531), `launcher.py` (974), `strategy_matrix.py` (609), `options_matrix.py` (424), `ai_insights.py` (321), `prompt_registry.py` (434), `ai_control_center.py` (337), `live_inventory.py` (392), `pairs.py` (462), `market_data.py` (263), `analytics.py` (772), `paper_monitor.py` (124), `settings_manager.py` (180), `validation_lab.py` (231), `reports_library.py` (237), `analytics_signals.py` (367), `_shared.py` (208) | ~10,000 total | **No owning file for any panel; most have 0-1 incidental references.** A distinct, previously-undocumented pattern has emerged since the original audit: several features now split into a *logic* module at `gui/<feature>.py` (tested — e.g. `gui/ai_control_center.py`, `gui/ai_insights_panel.py`, `gui/gravity_ai_panel.py` all have owning suites) **plus** a *render-wrapper* module at `gui/panels/<feature>.py` that is the actual Streamlit tab entry point and now also contains real pure logic — **and the wrapper has zero direct coverage.** Concretely, `gui/panels/gravity_audit.py` (modified same-day as this audit) contains `_derive_step_status()` — a multi-branch PASS/FAIL classifier whose own docstring says an earlier version of this exact logic **"misreported a passing step as a failure"** — and `_parse_trailing_json()`, a hand-rolled brace-matching JSON extractor over arbitrary subprocess stdout; neither has a single test. | Medium-High for the pure helpers specifically (proven track record of a real bug in `_derive_step_status`), Medium for the panels overall (operator surface, `safe_panel()` catches render exceptions so a broken panel degrades to an error box rather than crashing the app — but a *wrong* rendering, like the prior `_derive_step_status` bug, does not trip that safety net). | Same "extract and test pure helpers" recommendation as the original audit, now with concrete, high-value, low-effort targets: `gui/panels/gravity_audit.py::_derive_step_status` (PASS/FAIL branch matrix — status key, `overall_pass` key, `step_3_5_discrepancy_analysis`'s conclusion string, `step_7_simulation_impact`'s dual-status join, and the `"—"`/`False` fallback for an unrecognized shape) and `::_parse_trailing_json` (no `}` in text, unbalanced braces, malformed JSON after brace-matching, valid trailing JSON preceded by unrelated stdout noise) are both pure functions with no Streamlit dependency and can be unit tested today with zero mocking. **`_derive_step_status`/`_parse_trailing_json` done in Phase 5** — `tests/test_gravity_audit_panel_helpers.py` (27 tests covering every branch of both functions, including the `step_3_5`/`step_7` legacy shapes and the depth-counting brace matcher's nested/unbalanced/dangling-brace edge cases). The other ~18 panels and their remaining pure helpers (`_load_gravity_report`/`_load_validation_summaries`'s cache-key derivation, and equivalents in the other 18 render-wrapper files) are still open. |
| `webapp/` (Pilots PWA — React/TypeScript, ~separate stack from the Python suite above) | n/a (JS/TS) | **1 test file total** (`webapp/src/api/mock.test.ts`, covering only the mock API layer) despite `vitest` being fully configured (`npm test` → `vitest run`, `jsdom` + `@vitejs/plugin-react` present in `devDependencies`). None of the actual screens — Onboarding, Marketplace, Pilot Detail, Portfolio, the Follow modal (all described as first-class features in `CLAUDE.md`) — have component tests. | Medium — this is a real, shippable consumer-facing surface (`api/pilots_api.py`, the Python backend it talks to, is well tested per CLAUDE.md), but the frontend that renders that data and drives the Follow flow (money-adjacent, even if gated/preview-only) has no regression protection at all. | Out of scope for `pytest`/this Python-focused doc's roadmap numerically, but flagged because it's easy to miss when reasoning about "the test suite" as Python-only. Minimum viable: component tests for the Follow modal's preview/confirmation copy (the "this creates a gated queue... no order is placed automatically" notice is a safety-critical string) and `src/api/client.ts`'s mock↔live switch. |
| ~~`gravity/__init__.py`~~ | — | **Deleted 2026-07-10** — this was a dead, unimported duplicate of `Gravity AI Review Suite.py`'s `GravityAIAuditor` class from an incomplete Phase 4b package extraction (see `docs/IMPROVEMENT_PLAN.md`'s "4b Gravity split" row). Its 10 audit steps with no live-file equivalent were migrated into `Gravity AI Review Suite.py` (step_75–step_85) before deletion. No longer a coverage gap. | — | — |

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

### Phase 4 — Long tail + ratchet *(mixed: partially done, re-scoped below)*
- ~~Register `@pytest.mark.slow` in `pytest.ini`~~ — **done** (both `network` and `slow` are now
  registered markers).
- **GUI panel helpers** and **`data/robinhood_client.py`** — **still open**, unchanged since the
  original audit; both are carried forward into Phase 5 below (the panel-helpers item has grown
  substantially in scope — see the module-level table).
- **Resolve the stray root files** — **still open**: `test_gravity.py` and `test_mock_abc.py` remain
  at repo root, never collected by CI.
- **Coverage-floor ratchet in CI** — **still not added**. `ci.yml` computes and prints a coverage
  summary but does not fail the build on regression. Now that six modules have a stable Phase-3
  baseline (43–81%), this is unblocked; recommend `--cov-fail-under` set a few points below the
  current whole-suite total (measured, not guessed) so it catches real regressions without being so
  tight it blocks unrelated PRs.

### Phase 5 — Findings from the 2026-07-14 re-audit
Ranked by risk × gap, reflecting how the codebase evolved since Phase 3. First implementation pass
(same day) landed items 2 and 6, plus the stray-root-file half of item 5:

1. **Mirror `Gravity AI Review Suite.py`'s highest-value `step_*` invariants into pytest** — *not yet
   started*. Following the exact pattern `tests/test_gravity_mirrored_invariants.py` already
   established for 2 of ~30. This remains the single largest gap in the codebase relative to the
   module's documented importance — it is the deployability gate referenced by name throughout
   `CLAUDE.md`, yet has zero tests of its own and CI cannot catch a regression in it. Start with the
   steps that gate the highest-blast-radius decisions (PBO/DSR deployability, options-matrix
   strike/delta integrity, DB-backend resilience).
2. ~~Unit test `gui/panels/gravity_audit.py::_derive_step_status` and `::_parse_trailing_json`.~~
   **Done** — `tests/test_gravity_audit_panel_helpers.py` (27 tests). The other ~18 render-wrapper
   panels' pure helpers remain open (see item 5).
3. **Owning test for `ai_verification_prompts.py`'s `GravityAIAuditor` class** — *not yet started*.
4. **`investyo_mcp_server.py`** (1,515 lines, 0% coverage) — *not yet started*.
5. **Carry forward from Phase 4:** `data/robinhood_client.py` owning test (*not yet started*) and the
   broader GUI render-wrapper panel helper extraction beyond the two functions closed in item 2
   (*not yet started* — 18 panels remain). ~~The stray-root-file rehoming~~ **done**, as a deletion
   rather than a rehome — see the hygiene section above for why both files turned out to be dead
   weight rather than genuine gaps.
6. ~~`alerting_mcp/notifier.py` + `mcp_remote_adapter.py`~~ — **done**, `tests/test_alerting_mcp_notifier.py`
   (21 tests) + `tests/test_mcp_remote_adapter.py` (3 tests). See the module-level table for a real bug
   this caught in the *tests'* own mocking approach (not the source) along the way.
7. **`webapp/` (Pilots PWA) component tests** — *not yet started*; different stack (`vitest`), lower
   priority than the Python items above.
8. **Coverage-floor ratchet in CI** — *not yet started*.

**Bonus finding, not in the original Phase 5 list:** verifying the full offline suite after the above
changes surfaced `tests/test_harness_buyhold.py` making a real, unmarked live network call to
yfinance — fixed by adding `@pytest.mark.network` (see hygiene section above). Also verified: the full
`pytest -m "not network"` suite is green after every change in this pass (5,025 collected, 0 failures).
