# S&P 500 overlay on the Pilot performance chart silently truncates (2026-09)

## Status
**Fixed** (this PR). Two parts: (A) `validation/harness.py`'s SPY
macro-benchmark fetch is now FMP-primary (matching the strategy's own price
fetch), yfinance-fallback only; (B) `pilots/performance.py::pilot_performance()`
now anchors `benchmark`/`macro_benchmark` tail-slicing to the STRATEGY curve's
own last date and surfaces an honest, always-visible disclosure
(`macro_benchmark_note`) whenever a persisted `macro_benchmark_curve` still
trails the strategy curve.

## Symptom
On the Pilots PWA's Pilot-detail screen, the performance chart overlays three
lines: **Pilot** (`equity_curve`), **Benchmark** (`benchmark_curve`), and
**S&P 500** (`macro_benchmark_curve`). The S&P 500 line was observed to stop
several months before the other two, with no error, warning, or explanation
anywhere on screen — it simply stopped drawing partway across the chart.

## Root cause
1. `validation/harness.py::_spy_return_series()` fetched SPY via a raw
   `yf.download("SPY", start=start_date, end=end_date, progress=False)`
   call — completely independent of how the strategy's own return series
   (`y`, feeding `benchmark_curve`) is sourced. A 2026-08-21 PR migrated the
   strategy-side price fetch (`scripts/refresh_validations.py::_download_closes`
   / `_fetch_fmp_ohlcv_batch`) off yfinance onto per-ticker FMP calls
   specifically because yfinance has a well-documented history of silent/
   partial failures in this codebase (rate limiting, generic "possibly
   delisted" empty responses even for real large-cap tickers, tz-cache
   SQLite contention under concurrent access). `_spy_return_series` was the
   one remaining yfinance-only fetch path in the harness.
2. `_build_equity_curve()` (used to build all three curves) does
   `returns.dropna()` before computing the cumulative base-100 series. If
   the yfinance SPY download is incomplete/truncated for recent months,
   those trailing rows are silently dropped rather than raising or warning —
   so the persisted `macro_benchmark_curve` in a
   `reports/<strategy_id>_validation_summary.json` file ends up shorter than
   `equity_curve`/`benchmark_curve`. Under healthy conditions all three
   curves share the exact same OOS index and, by construction (their
   downsampling always keeps the last raw point), an identical last date —
   any observed difference is a genuine data-source shortfall, not
   calendar/weekend noise.
3. `pilots/performance.py::_slice_curve_by_range()` tail-sliced `curve`,
   `benchmark`, and `macro_benchmark` **independently, each relative to its
   own last date**. So even before considering #1/#2, a genuinely-shorter
   persisted `macro_benchmark_curve` would show a *different* calendar
   window under a range toggle than the Pilot/Benchmark lines (its own
   trailing window, not the Pilot's), rather than the same window with
   fewer points — potentially rendering nothing at all for a narrow range
   if the whole requested window predated the stale series' own tail.
4. On the frontend, `webapp/src/components/charts.tsx::PerfLine` merges the
   S&P series onto the Pilot's own date axis by a `date -> value` lookup;
   any date the shorter series lacks becomes `null`. `<Line connectNulls>`
   can bridge an internal gap but cannot extend a line past its last real
   point, so the line simply stopped drawing once its data ran out — with
   nothing distinguishing "this data source is stale" from a rendering bug.

## Fix (this PR)
- **`validation/harness.py`** — `_spy_return_series()` is now a two-tier
  dispatcher: `_spy_return_series_fmp()` (mirrors
  `_fetch_fmp_ohlcv_batch`'s fetch/reshape exactly —
  `data.fmp_client.historical_eod_full_range` +
  `data.market_data._fmp_bars_payload_to_df`, same
  `settings.FMP_BARS_ADJUSTMENT` variant resolution) tried first, falling
  back to the renamed `_spy_return_series_yfinance()` (unchanged body) when
  FMP is unconfigured (`historical_eod_full_range` raises `FMPUnavailable`
  *synchronously*, not an empty return — guarded explicitly) or fails for
  any other reason. Any non-`None` FMP result is used as-is — this function
  never second-guesses whether the result is "complete enough," matching
  this codebase's established primary/fallback convention elsewhere. Applies
  to **every** registered strategy's validation run — this is the single
  shared SPY-fetch path, not per-strategy code.
- **`pilots/performance.py`** — `_slice_curve_by_range()` gained an optional
  `anchor_date` parameter; `pilot_performance()` now computes the strategy
  curve's own last date once and passes it as the anchor when slicing
  `benchmark`/`macro_benchmark`, so every overlaid line shares the same
  calendar window under a range toggle instead of each independently
  showing its own trailing window. Omitted (`None`, the default)
  reproduces exactly today's self-anchored behavior — verified
  byte-identical against every existing fixture/test.
- **`pilots/performance.py`** — new `macro_benchmark_note: Optional[str]`
  field (via `_macro_benchmark_staleness_note()`), computed by comparing
  the RAW (unsliced) `macro_benchmark_curve`'s real last date against the
  strategy curve's own last date. `None` when there's no primary curve to
  anchor to, no macro series was rendered, or the gap is within
  `_MACRO_BENCHMARK_STALE_TOLERANCE_DAYS` (5, tolerating ordinary
  weekend/holiday calendar noise). Otherwise a human-readable sentence
  naming the actual date and gap, e.g. *"S&P 500 overlay data is only
  available through 2024-03-31 (91 days behind the Pilot's own track
  record)."*
- **`webapp/`** — `PerformanceResponse` gained `macro_benchmark_note?: string
  | null`; `webapp/src/screens/PilotDetail.tsx` renders it as
  **always-visible** small text right after the legend row, not a
  hover-only tooltip — a silently-missing line is exactly the kind of thing
  a hover-only affordance would still let a user miss, and at narrow range
  toggles the S&P line can render nothing at all (see root cause #3 above),
  so the text note is the only thing telling the user why.

## Scope boundary — disclosed, not silently assumed complete
This fix applies to **every currently-persisted** validation report
immediately, at read time — the anchor-date slicing and the disclosure note
require no re-run and take effect the next time `GET /pilots/{id}/performance`
is called. It does **not** retroactively extend an already-truncated
`macro_benchmark_curve`'s real data. A Pilot whose report was persisted
before this fix will still show a short S&P line after merging — now
correctly windowed against the Pilot's own recency and with an honest note
explaining why, rather than a silent, potentially misaligned truncation.
Actually extending the real SPY data requires a live `scripts/refresh_validations.py`
re-run per strategy (needs live FMP network access, unavailable in this
sandboxed dev environment — no `reports/*.json` files exist in this worktree
at all, confirming this rather than merely asserting it).

A secondary, non-load-bearing side effect: since the strategy's own
underlying (`y`) is also FMP-sourced post the 2026-08-21 migration, routing
the SPY overlay through FMP too means `_build_macro_benchmark_curve`'s
"underlying already IS SPY, overlay would be redundant"
`np.allclose(a, b, ...)` guard now compares two FMP-sourced series for that
case, rather than one FMP series against one yfinance series with a
potentially different adjustment convention — removing a latent
false-negative risk in that guard.

Tests: `tests/test_harness_equity_curve.py::TestSpyReturnSeriesFmpPrimary`;
`tests/test_pilots_performance.py::TestSliceCurveByRangeAnchorDate`,
`TestMacroBenchmarkStalenessNote`, `TestPilotPerformanceMacroBenchmarkStaleness`
(including a regression guard against the existing
`timeseries_momentum` fixture, whose three curves share an identical last
date and so must slice byte-identically to before this change).
