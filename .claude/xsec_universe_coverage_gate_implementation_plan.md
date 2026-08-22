# Universe-coverage visibility + fail-closed gate for cross-sectional validation runs — Implementation Plan

## Context

An audit of `scripts/refresh_validations.py`'s 29-strategy validation run found that
`cross_sectional_momentum` and `sector_quality_rank`'s `deployable` verdicts have swung
wildly (PBO 0.11→0.69, Sharpe 0.68→1.01, `deployable` flipping True/False) across dozens
of real runs in the durable `validation_runs` DB table over ~36 hours, with **no code
changes in between**.

**Root cause, empirically confirmed** (re-running `cross_sectional_momentum` under heavy
concurrent FMP load vs. twice more in isolation produced bit-identical output all three
times) — the strategy math is fully deterministic. The swings come from *other* concurrent
runs (other worktrees/sessions sharing this machine's one FMP rate-limit budget) hitting
FMP's cooldown circuit-breaker at different points, so `_download_closes`/`_download_ohlcv`
correctly and intentionally drop failed tickers (never fabricate — CONSTRAINT #4), but a
cross-sectional strategy's whole Sharpe/PBO/DSR/deployable verdict then silently depends
on exactly which random subset of the ~500-ticker universe happened to succeed *that* run
— with zero indication of this anywhere in the report.

The fix makes universe-coverage a first-class, visible, and (below a threshold) **gating**
part of every validation report — it does not touch the strategy math, which is already
correct. This affects all 7 strategies sharing `_XSEC_UNIVERSE_WIDE`/`_XSEC_UNIVERSE_CAPPED`
(`cross_sectional_momentum`, `relative_strength_xsec`, `multifactor_lowvol_size`,
`macro_regime_pit`, `signal_replay_balanced_blend`, `lgbm_ranker`, `sector_quality_rank`),
so the fix is implemented generally in the harness, not per-strategy.

## Decision: fail-closed, unconditional, 90% threshold (no settings flag)

Per this repo's dominant CONSTRAINT #6 convention (the options-selling stress gate fails
closed when never tested; VRP fails closed on NaN; ETF-transmission multiplier defaults to
neutral rather than corrupting the portfolio cap on missing data), a run whose declared
universe was only partially fetched **forces `deployable=False`**, regardless of how good
the PBO/DSR/Sharpe/MaxDD numbers look.

This gate is **unconditional** (no opt-in `settings.*_ENABLED` flag), unlike
`VALIDATION_HARNESS_OOS_GATE_ENABLED` (which changed the computation methodology for every
run and needed a flag to avoid silently invalidating the whole registry's recorded numbers
before re-verification). This gate only changes the verdict on a **new, narrow failure
mode** (partial universe coverage) — a fully-covered run (the normal case in an isolated
run) is completely unaffected/bit-identical.

Threshold: **90%** (`MIN_UNIVERSE_COVERAGE_PCT = 0.90` in `validation/thresholds.py`).
Applies uniformly to every adapter (single-ticker adapters are always 1/1 = 100%, so the
gate is a structural no-op for them).

## Implementation (delivered)

1. **`validation/thresholds.py`** — `MIN_UNIVERSE_COVERAGE_PCT: float = 0.90`.
2. **`validation/harness.py`** — `ValidationReport.universe_coverage` (optional dict),
   `universe_coverage_ok` property, `deployable` ANDs it in, `to_summary_dict()` surfaces
   both fields, `StrategyValidationHarness.run(universe_coverage=...)` threads it through
   to the report and to the HTML render call.
3. **`reports/validation_report_template.html.j2`** — new "Universe Coverage" card
   (requested/fetched/%, PASS/FAIL badge, missing-ticker list), placed right after the
   header, shown only when tracked.
4. **`scripts/refresh_validations.py`** — `_validate_single_strategy` computes the
   coverage dict from data it already derives (`available` vs. `universe`); `_fail_reason`
   reports a coverage-shortfall reason first; `_print_summary_table` gained a `Coverage`
   column; the `--json` CLI output gained `universe_coverage_pct`/`universe_coverage_ok`.
5. **`tests/test_refresh_validations.py::_noop_harness_run`** — compatibility fix (new
   `universe_coverage=None` kwarg + realistic echo), required for existing
   `_patch_harness()`-based tests to keep passing.
6. **New `tests/test_universe_coverage_gate.py`** — `ValidationReport`-level regression
   tests (the 60%-vs-100%-coverage divergence, boundary, `to_summary_dict()`, and a real
   end-to-end Jinja2 render of the new HTML section) plus `TestUniverseCoverageDispatch`/
   `TestFailReasonUniverseCoverage`/`TestPrintSummaryTableCoverageColumn` in
   `tests/test_refresh_validations.py`.
7. **Docs** — `docs/architecture/validation-and-signals.md` (2 bullets),
   `docs/known_issues/xsec_universe_coverage_concurrency_variance.md` (new),
   `docs/VALIDATION_STRATEGY_FIX_LOG.md` (new dated entry), `docs/signals/
   cross_sectional_momentum.md` and `docs/signals/sector_quality_rank.md` (new addenda
   with coverage-verified re-run numbers).
8. **Live re-run** — `cross_sectional_momentum`/`sector_quality_rank` re-validated with a
   real FMP key, `--workers 1`, matching the 2026-08-21 entry's CPCV config for
   comparability; numbers recorded in the two doc files above.

## Verification

- `uv run pytest tests/test_universe_coverage_gate.py tests/test_refresh_validations.py
  tests/test_harness_equity_curve.py tests/test_harness_oos_gate.py
  tests/test_family_deployable.py tests/test_stress_gate.py tests/test_validation_history.py
  tests/test_html_report.py -q` — all passing.
- `uv run python -m ruff check . --select=F821,F822,F823,E9` — clean.
- Full offline suite (`make ci` equivalent, `pytest -m "not network and not slow"`) —
  11935 passed, 13 skipped, 0 failed.
- Live re-validation run — see `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-22 entry.

## Out of scope (disclosed, not attempted)

- Fixing/changing the cross-sectional strategy math itself (already correct).
- A cross-worktree FMP rate-limit coordination mechanism (documented as a follow-up in
  the new known-issues doc).
- `gui/panels/validation_lab.py`, `reports/cpcv_report.html.j2`, and any webapp surface.
- Re-running the other 5 tiered-universe strategies (`relative_strength_xsec`,
  `multifactor_lowvol_size`, `macro_regime_pit`, `signal_replay_balanced_blend`,
  `lgbm_ranker`) — flagged with the same unverified-coverage caveat, not re-run here.
