# Task tracker — S&P 500 macro overlay truncation fix

See `.claude/sp500_macro_overlay_implementation_plan.md` for the full plan and
`.claude/sp500_macro_overlay_walkthrough.md` for the finished walkthrough.

- [x] `validation/harness.py`: FMP-primary/yfinance-fallback SPY fetch
      (`_spy_return_series_fmp`/`_spy_return_series_yfinance`/`_spy_return_series`)
- [x] `pilots/performance.py`: `_slice_curve_by_range(..., anchor_date=)`,
      `_macro_benchmark_staleness_note()`, `pilot_performance()` wiring
- [x] `tests/test_harness_equity_curve.py`: `TestSpyReturnSeriesFmpPrimary`
- [x] `tests/test_pilots_performance.py`: `TestSliceCurveByRangeAnchorDate`,
      `TestMacroBenchmarkStalenessNote`, `TestPilotPerformanceMacroBenchmarkStaleness`
- [x] `webapp/src/api/types.ts`: `macro_benchmark_note?: string | null`
- [x] `webapp/src/api/mock.ts`: `macro_benchmark_note: null` in both branches
- [x] `webapp/src/screens/PilotDetail.tsx`: always-visible disclosure text
- [x] `webapp/src/api/mock.test.ts` + `webapp/src/screens/PilotDetail.test.tsx`: new tests
- [x] `docs/architecture/webapp-and-gui.md`: fixed stale "over yfinance history" wording
- [x] `docs/architecture/validation-and-signals.md`: new sourcing/note description
- [x] `docs/known_issues/sp500_macro_overlay_yfinance_truncation.md`: new write-up
- [x] `docs/known_issues/README.md`: new index row
- [x] Verification: `pytest tests/test_harness_equity_curve.py tests/test_pilots_performance.py tests/test_pilots_api.py -q`,
      `npm run --prefix webapp typecheck`, `npx vitest run` (full webapp suite)
- [ ] Open PR against `main`
