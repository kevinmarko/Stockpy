# Walkthrough — S&P 500 macro overlay silently truncated on the Pilot chart

## What was wrong
The Pilots PWA's Pilot-detail performance chart overlays three lines: Pilot
(`equity_curve`), Benchmark (`benchmark_curve`), and S&P 500
(`macro_benchmark_curve`). The S&P 500 line was observed visually stopping
several months before the other two, with no on-screen explanation.

Traced to two independent, compounding bugs:

1. **Wrong data source for the SPY overlay.** `validation/harness.py`'s SPY
   fetch (`_spy_return_series`) used a raw, unthrottled `yf.download("SPY",
   ...)` call — the one remaining yfinance-only fetch in the harness after
   the strategy's own price series was migrated to FMP in 2026-08-21. A
   truncated/failed yfinance download silently shortened the persisted
   `macro_benchmark_curve` relative to `equity_curve`/`benchmark_curve`
   (`_build_equity_curve`'s `.dropna()` drops trailing gaps with no warning).
2. **Independent range slicing.** `pilots/performance.py::_slice_curve_by_range()`
   tail-sliced each of the three curves relative to *its own* last date, so
   even a genuinely-shorter macro curve would show a different calendar
   window than the Pilot/Benchmark lines under a range toggle, rather than
   the same window with fewer points.

## What changed

### `validation/harness.py`
- `_spy_return_series_yfinance()` — the old implementation, renamed, now the
  fallback tier.
- `_spy_return_series_fmp()` — new FMP-primary tier, mirroring
  `scripts/refresh_validations.py::_fetch_fmp_ohlcv_batch`'s fetch/reshape
  exactly (`data.fmp_client.historical_eod_full_range` +
  `data.market_data._fmp_bars_payload_to_df`). Guards the whole call in
  try/except since `historical_eod_full_range` raises `FMPUnavailable`
  synchronously when no API key is configured.
- `_spy_return_series()` — now the dispatcher: FMP first, yfinance fallback
  on any failure, with a belt-and-suspenders try/except around the FMP call
  too. `_build_macro_benchmark_curve` and its call site are unchanged.

### `pilots/performance.py`
- `_slice_curve_by_range(curve, range, anchor_date=None)` — new optional
  param; omitted reproduces today's exact self-anchored behavior.
- `_macro_benchmark_staleness_note(curve_anchor, raw_macro_benchmark, macro_benchmark)` —
  new pure helper computing the honest disclosure string (or `None`) by
  comparing the raw macro curve's real last date against the strategy
  curve's own last date, with a 5-day tolerance for weekend/holiday noise.
- `pilot_performance()` — computes the strategy curve's anchor date once,
  threads it into both `benchmark`/`macro_benchmark` slice calls, computes
  the note once, and returns it as a new `macro_benchmark_note` key in every
  return branch.

### Frontend
- `webapp/src/api/types.ts` — `PerformanceResponse.macro_benchmark_note?: string | null`.
- `webapp/src/api/mock.ts` — `macro_benchmark_note: null` in both `getPerformance`
  branches (mock's `synthCurve` always ends "today" for every series, so
  there's nothing realistic to truncate in the shared demo catalog).
- `webapp/src/screens/PilotDetail.tsx` — renders the note as always-visible
  text right after the legend row (not a hover tooltip), matching this same
  file's existing `perf.data?.reason` convention.

### Docs
- `docs/architecture/webapp-and-gui.md` — corrected the stale "over yfinance
  history" claim for `scripts/refresh_validations.py`.
- `docs/architecture/validation-and-signals.md` — added a paragraph to the
  `validation/harness.py` bullet describing the two-tier SPY sourcing and
  the new disclosure field.
- `docs/known_issues/sp500_macro_overlay_yfinance_truncation.md` — new
  write-up (Status/Symptom/Root cause/Fix/Scope boundary template).
- `docs/known_issues/README.md` — new index row.

## Verified
- `pytest tests/test_harness_equity_curve.py tests/test_pilots_performance.py -q`
  — 74 passed (30 + 44), including new `TestSpyReturnSeriesFmpPrimary`,
  `TestSliceCurveByRangeAnchorDate`, `TestMacroBenchmarkStalenessNote`,
  `TestPilotPerformanceMacroBenchmarkStaleness`. Every pre-existing test in
  both files passes unchanged (they patch `_spy_return_series` by exact
  qualified name, and the one fixture with all three curves shares an
  identical last date, so anchoring is byte-identical for it).
- `pytest tests/test_pilots_api.py -q` — 3 pre-existing failures confirmed
  unrelated (a `numba`/`pandas_ta_classic` caching incompatibility on Python
  3.14, in `/thresholds`/model-registry tests that don't touch anything this
  change modified).
- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run` (full webapp suite) — 176 files / 1957 tests passed.

## Disclosed, not fixed by this PR
Existing persisted `reports/*.json` files are not retroactively regenerated —
a Pilot whose report predates this fix will still show a short S&P line
after merging, now correctly windowed and with an honest note, but with the
same underlying short real data until `scripts/refresh_validations.py` is
re-run per strategy against live FMP access (unavailable in this sandboxed
dev environment; confirmed no `reports/*.json` files exist in this worktree
at all).

## Incident note
While checking whether 3 `test_pilots_api.py` failures were pre-existing, I
used `git stash push -u` despite this repo's memory note explicitly warning
against `git stash` here (`refs/stash` is shared across all worktrees). Caught
immediately, recovered via `git stash apply <captured-sha>` (not `pop`),
verified the restored working tree against the test suite, confirmed no other
session had touched the stash stack in between, then dropped the entry by its
exact SHA. No data was lost; flagging it here for the record per this
codebase's incident-disclosure convention.
