# Universe-coverage visibility + fail-closed gate — Task Tracker

- [x] `validation/thresholds.py`: `MIN_UNIVERSE_COVERAGE_PCT = 0.90`.
- [x] `validation/harness.py`: `ValidationReport.universe_coverage` + `universe_coverage_ok`
      property + `deployable` gate + `to_summary_dict()` fields.
- [x] `validation/harness.py`: `StrategyValidationHarness.run(universe_coverage=...)`
      threaded through to `ValidationReport` and `_render_html_report`.
- [x] `reports/validation_report_template.html.j2`: new "Universe Coverage" card.
- [x] `scripts/refresh_validations.py`: `_validate_single_strategy` computes coverage
      dict, passes to `harness.run(...)`.
- [x] `scripts/refresh_validations.py`: `_fail_reason` coverage-shortfall branch (first).
- [x] `scripts/refresh_validations.py`: `_print_summary_table` `Coverage` column.
- [x] `scripts/refresh_validations.py`: `--json` CLI output carries coverage fields.
- [x] `tests/test_refresh_validations.py::_noop_harness_run` compatibility fix.
- [x] New `tests/test_universe_coverage_gate.py` (16 tests: gate logic, boundary,
      summary-dict surfacing, real HTML-report rendering).
- [x] `tests/test_refresh_validations.py` extensions (`TestUniverseCoverageDispatch`,
      `TestFailReasonUniverseCoverage`, `TestPrintSummaryTableCoverageColumn`, `--json`
      output test) — 10 new tests, 150 total in file.
- [x] Targeted regression suite green (300 passed).
- [x] Ruff genuine-bug lint clean.
- [x] Full offline suite green (11935 passed, 13 skipped, 0 failed).
- [x] `docs/architecture/validation-and-signals.md` updated (2 bullets).
- [x] New `docs/known_issues/xsec_universe_coverage_concurrency_variance.md`.
- [x] `docs/VALIDATION_STRATEGY_FIX_LOG.md` new dated entry (2026-08-22).
- [x] Live isolated re-run of `cross_sectional_momentum`/`sector_quality_rank` — both
      achieved 100% coverage (504/504, 100/100) despite a concurrent `lgbm_ranker` job
      on this shared machine. Results: `cross_sectional_momentum` PBO=0.592 → flips
      `deployable=True → False` (2026-08-21's 0.492 was unverified-coverage);
      `sector_quality_rank` MaxDD=21.9% → flips `deployable=False → True` (2026-08-21's
      34.2% was unverified-coverage).
- [x] `docs/signals/cross_sectional_momentum.md` addendum with re-run numbers.
- [x] `docs/signals/sector_quality_rank.md` addendum with re-run numbers + the honest
      EDGAR-fundamentals-coverage-is-a-separate-gap caveat (confirmed live: a real BBY
      EDGAR timeout occurred in this very run despite 100% price coverage).
- [x] Fill in real numbers in `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-22 entry.
- [x] `docs/known_issues/README.md` row added.
- [x] PR opened against `main`: https://github.com/kevinmarko/Stockpy/pull/858
