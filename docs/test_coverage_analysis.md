# Test-Coverage Analysis

## Executive summary

The Stockpy test suite is large and genuinely mature — **3,446 test functions across 203 `test_*.py`
files** at the time of this audit — with disciplined conventions: fully-offline mocking, in-memory DB
isolation, dedicated no-lookahead perturbation proofs, and dead-letter resilience checks. But three
gaps blunted its value at audit time. First, **no coverage was measured** (no pytest-cov, no
`coverage.py`, no `.coveragerc`), so the suite's real line/branch reach was unknown. Second, **nothing
ran it automatically** — there was no CI workflow, so a regression only surfaced when a developer
remembered to run `pytest` locally. Third, several **load-bearing modules were effectively
unexercised**, most notably `simulation_engine.py`, alongside large orchestration/evaluation modules
that had no owning test file and were only touched incidentally.

This document inventories the suite, enumerates the gaps with evidence, and lays out a prioritized
roadmap. This work landed alongside an independent, concurrently-developed change that added the
repo's first CI workflow (`.github/workflows/ci.yml`, offline suite via `-m "not network"`) and
deleted `reporting_engine.py` as dead code (superseded by `diagnostics_and_visuals.py`) — so that
module is no longer listed as a gap below, and the coverage tooling here extends the existing CI
workflow with `--cov` rather than adding a second one. `pytest --cov` now produces real numbers,
folded into the module-level table below.

---

## Current state

### The numbers

| Metric | Value |
|--------|-------|
| `test_*.py` files under `tests/` | 203 |
| `def test_` functions | 3,446 |
| `class Test*` classes | 664 |
| pytest collection scope | `testpaths = tests` (pytest.ini) |
| Coverage measurement | none at audit time → `pytest-cov` + `.coveragerc` added here |
| CI automation | none at audit time → `.github/workflows/ci.yml` added independently in parallel; extended here with `--cov` |

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

- **Stray, never-collected root test files.** `test_gravity.py` and `test_mock_abc.py` live at the
  repo root. Because `pytest.ini` pins `testpaths = tests`, they are **never collected**. (Despite
  its name, `test_gravity.py`'s actual content exercises `scripts/refresh_validations.py`, not the
  Gravity auditor — the filename is misleading.)
- **Unregistered marker.** `@pytest.mark.slow` is used (twice, in `test_validation_lgbm.py`) but
  there is no `markers` section in `pytest.ini`, so pytest emits an unregistered-marker warning and
  the marker cannot be reliably selected/deselected.

---

## Module-level gaps

Ranked by risk × gap. Six modules below now have real `pytest --cov` percentages, measured after the
new owning suites landed. "No owning file" (remaining rows) means no `tests/test_<module>.py`; the
module is exercised only incidentally by other modules' tests.

| Module | Lines | Current coverage | Risk if broken | Proposed tests |
|--------|-------|------------------|----------------|----------------|
| `simulation_engine.py` | 258 | **Now 81%** (was: only `print_survivorship_warning_for_backtest` referenced once, in `test_universe.py`) via new `test_simulation_engine.py`. | High — CLAUDE.md mandates every new strategy be *proven here* (with `TieredCostModel` + survivorship warning) before wiring into `strategy_engine.py`. A silent break invalidates that gate. | Done: backtest runs end-to-end on synthetic OHLCV, applies `TieredCostModel` commission/slippage, emits the survivorship warning, vectorbt sweep + backtrader event-driven run both exercised. |
| ~~`reporting_engine.py`~~ | — | **Deleted upstream** (2026-07-09) — superseded by `diagnostics_and_visuals.py` and removed as dead code in a concurrent change. No longer a gap; `tests/test_reporting_engine.py` was not added. | — | — |
| `main_orchestrator.py` | 1,519 | **Now 51%** (was: no owning file, only 25 incidental touches) via new `test_main_orchestrator.py`. | High — the async master pipeline; a break degrades the primary production run path. | Done: `run_pipeline` with injected `MockDataEngine` + `EngineContext`; `PipelineFatalError` raised on fatal fetch/validation failure; dry-run broker skip; xsec-rank vectorization correctness. Remaining 49% is mostly the live-broker/reconciliation branches — next candidate for Phase 4. |
| `evaluation_engine.py` | 1,013 | **Now 43%** (was: no owning file, 14 incidental refs) via new `test_evaluation_engine.py`. | High — strategy performance evaluation feeds sizing and reporting. | Done: known-input assertions for edge/heat/Brinson-Fachler/Kelly-target, NaN-shaped empty inputs. Remaining 57% is largely the calibration/tracking surfaces already owned by other files (`test_calibration.py` etc.) that weren't re-measured against this module in isolation. |
| `strategy_engine.py` | 668 | **Now 76%** (was: no owning file, 16 incidental refs) via new `test_strategy_engine.py`. | High — the core signal-generation + tactical-range + sizing surface. | Done: `evaluate_security` end-to-end with injected in-memory `TransactionsStore`; `apply_sell_side_range`/`apply_tactical_ranges` boundaries; `_calculate_kelly_sizing` path-tag branches; regime/meta-label multiplier clamp. |
| `diagnostics_and_visuals.py` | 1,115 | **Partial** — HTML-report path covered (`test_html_report.py`, `test_diagnostics_extra.py`); the broader visualization/chart-generation helpers are not. | Medium — active report path (called by both entry points); chart helpers can silently break. | Extend to Plotly/visual helpers: figure objects build from fixtures, empty/degraded inputs render placeholders not crashes. |
| `research_engine.py` | 498 | **Now 52%** (was: thin/scattered — `test_correlation_clusters.py`, `test_indicators_lookahead.py`, `test_no_fabricated_metrics.py`, `test_dead_letter_resilience.py`) via new `test_research_engine.py`. | Medium — analytics feeding risk/attribution. | Done: sector/dividend/leverage/momentum-slope/slippage/options-vol-edge/CoVaR known-answer tests. Remaining 48% is mostly the correlation-clustering helpers already owned by `test_correlation_clusters.py`. |
| `data/robinhood_client.py` | 345 | **No owning file** — only indirect stub coverage via `test_portfolio_sync.py`'s fakes. | Medium — account/watchlist discovery; `_suppress_rs_output` + `discover_universe`/`discover_watchlists` untested directly. | Owning `test_robinhood_client.py` with a fake `robin_stocks`: discovery union/dedupe, per-list failure skip, output-suppression context manager, unauthenticated short-circuit. |
| `gui/panels/report_viewer.py` (1,546), `observability.py` (1,190), `launcher.py` (934), `strategy_matrix.py` (610), `live_inventory.py` (347), `settings_manager.py` (138), `paper_monitor.py` (122) | ~4,900 total | **No owning file per panel** — some behavior is touched by scoped tests (`test_launcher_maintenance.py`, `test_observability_telemetry.py`, `test_launcher_safety_controls.py`), but the render helpers and data-shaping logic per panel are largely unpinned. | Medium — operator surface; Streamlit render code is hard to test but the pure data-shaping helpers are not. | Extract and test pure helper functions per panel (status derivation, table shaping, threshold badges) with fixtures; leave `st.*` render calls to `safe_panel` integration. |
| ~~`gravity/__init__.py`~~ | — | **Deleted 2026-07-10** — this was a dead, unimported duplicate of `Gravity AI Review Suite.py`'s `GravityAIAuditor` class from an incomplete Phase 4b package extraction (see `docs/IMPROVEMENT_PLAN.md`'s "4b Gravity split" row). Its 10 audit steps with no live-file equivalent (help explainers, advisory agent, trade signals, Robinhood orders/execution bridge, LLM commentary, AI Gravity runner, AI Insights, Opal research, AI Control Center) were migrated into `Gravity AI Review Suite.py` (step_75–step_85) before deletion. No longer a coverage gap. | — | — |

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

### Phase 4 — Long tail + ratchet
- **GUI panel helpers:** extract pure data-shaping helpers from the seven un-owned panels and unit
  test them; leave `st.*` rendering to `safe_panel` integration coverage.
- **`data/robinhood_client.py`:** owning test with a fake `robin_stocks`.
- **Resolve the stray root files:** rehome `test_gravity.py` (and `test_mock_abc.py`) under `tests/`
  so they are actually collected; register `@pytest.mark.slow` in `pytest.ini`.
- **Add a coverage-floor ratchet to CI** only after the Phase 3 modules land and a stable baseline
  exists — fail the build on regression below the established floor, raising it as coverage improves.
