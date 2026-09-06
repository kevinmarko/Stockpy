# Fix: S&P 500 overlay silently truncates on the Pilot performance chart

## Context

On the Pilots PWA's Pilot-detail screen, the performance chart overlays three
lines: **Pilot** (`equity_curve`), **Benchmark** (`benchmark_curve`), and
**S&P 500** (`macro_benchmark_curve`). The user noticed the S&P 500 line
stops several months before the other two.

Root cause, traced end-to-end:

1. `validation/harness.py::_spy_return_series()` fetches SPY via a raw
   `yf.download("SPY", ...)` call — completely separate from how the
   strategy's own return series is sourced. A 2026-08-21 PR migrated the
   strategy-side price fetch (`scripts/refresh_validations.py::_download_closes`
   / `_fetch_fmp_ohlcv_batch`) off yfinance onto per-ticker FMP calls
   specifically because yfinance has a well-documented history of silent/
   partial failures in this codebase (rate limiting, "possibly delisted"
   empty responses, tz-cache SQLite contention). `_spy_return_series` was
   never migrated.
2. `_build_equity_curve()` (used to build all three curves) does
   `returns.dropna()` before computing the cumulative series — so if the
   yfinance SPY download is incomplete/truncated for recent months, those
   trailing rows are silently dropped, leaving the persisted
   `macro_benchmark_curve` shorter than `equity_curve`/`benchmark_curve`
   (which are built from the strategy's own, healthier FMP-sourced returns
   and share the exact same OOS index — under healthy conditions all three
   curves' last dates are identical, confirmed by tracing the downsampling
   math).
3. `pilots/performance.py::_slice_curve_by_range()` tail-slices `curve`,
   `benchmark`, and `macro_benchmark` **independently, each relative to its
   own last date** — so even once #1/#2 is fixed, a genuinely-shorter
   persisted macro curve would show a *different* calendar window under a
   range toggle than the Pilot/Benchmark lines, instead of the same window
   with fewer points.
4. On the frontend, `PerfLine` merges the S&P series onto the Pilot's own
   date axis by lookup; missing dates become `null`, and once the tail is
   all-null the line simply stops drawing — with no error or indicator, so
   it looks like a UI bug rather than a stale/short data series.

This fix applies to **every** Pilot's chart, since both `validation/harness.py`'s
SPY fetch and `pilots/performance.py::pilot_performance()` are the single
shared code path every strategy's validation run / every Pilot's
`/performance` endpoint call goes through — no per-pilot code is needed.

Per the user's direction, this implements:
- **Option 2** — source SPY through FMP (the same provider the strategy's
  own return series already uses), yfinance only as a fallback.
- **Option 3** — an honest, always-visible disclosure when the persisted S&P
  overlay's real data still trails the Pilot's own track record, instead of
  the line silently vanishing.

**Disclosed scope boundary, stated up front:** this fixes the *code path*
and adds honest disclosure for **every currently-persisted** validation
report immediately (read-time). It does **not** retroactively extend an
already-truncated `macro_benchmark_curve`'s real data — that only happens
the next time `scripts/refresh_validations.py` is re-run per strategy
(requires live FMP network access, unavailable in this sandboxed dev
environment; no `reports/*.json` files exist in this worktree at all). So
after merging, a Pilot whose report predates this fix will still show a
short S&P line — now correctly windowed and with an honest note explaining
why, rather than a silent, misaligned truncation.

We're already on a feature branch (`claude/sp-line-missing-data-ec48d9`,
not `main`), so no new branch is needed — this satisfies the repo's
validation-changes-need-a-branch rule already.

## Implementation

### 1. `validation/harness.py` — FMP-primary, yfinance-fallback SPY fetch

- Rename the current `_spy_return_series` function body → `_spy_return_series_yfinance`
  (identical body/docstring, now documented as the fallback tier).
- Add `_spy_return_series_fmp(oos_index, start_date, end_date) -> Optional[pd.Series]`:
  mirrors `scripts/refresh_validations.py::_fetch_fmp_ohlcv_batch`'s per-ticker
  fetch pattern exactly (lazy `from data import fmp_client`, lazy
  `from data.market_data import _fmp_bars_payload_to_df, _warn_once_if_fmp_bars_adjustment_mismatched`,
  lazy `from settings import settings`; resolve `variant` from
  `settings.FMP_BARS_ADJUSTMENT`). Wrap the **entire** fetch+reshape in one
  `try/except Exception` — `historical_eod_full_range` raises
  `FMPUnavailable` *synchronously* when `FMP_API_KEY` is unset (not an empty
  return), so the whole call must be guarded, not just checked for `None`.
  Reshape via `_fmp_bars_payload_to_df`, normalize/sort the index, `.dropna()`
  the Close column, `.pct_change()`, reindex to `oos_index`. Return `None`
  on any failure (never raises — CONSTRAINT #6).
- `_spy_return_series` becomes the dispatcher: try FMP first; if it returns
  `None`, fall back to `_spy_return_series_yfinance`. Any non-`None` FMP
  result is used as-is (no "is it complete enough" second-guessing — matches
  this repo's established primary/fallback convention elsewhere, e.g.
  `CompositeProvider`).
- `_build_macro_benchmark_curve` and its call site are unchanged — it still
  just calls `_spy_return_series(oos_index, ...)`.
- Confirmed zero circular-import risk (`data/fmp_client.py`/`data/market_data.py`
  import nothing from `validation/`/`scripts/`/`pilots/`).
- **Tests** (`tests/test_harness_equity_curve.py`, new class
  `TestSpyReturnSeriesFmpPrimary`): FMP success used as-is (yfinance never
  called); FMP raises/returns `None` → yfinance fallback engages; both fail
  → `None`. Patch `validation.harness._spy_return_series_fmp` /
  `_spy_return_series_yfinance` directly. **No existing test in this file
  needs changing** — every one patches `validation.harness._spy_return_series`
  by exact qualified name, which still exists unchanged as the dispatcher.

### 2. `pilots/performance.py` — shared-anchor slicing + honest disclosure

- `_slice_curve_by_range(curve, range, anchor_date: Optional[str] = None)`:
  when `anchor_date` is given, use it instead of `curve[-1]["date"]` as the
  "today" reference for the cutoff. Omitted (default `None`) reproduces
  today's exact self-anchored behavior — no other caller exists besides
  `pilot_performance`.
- New module constant `_MACRO_BENCHMARK_STALE_TOLERANCE_DAYS = 5` (tolerates
  ordinary weekend/holiday noise between two independently-fetched daily
  series; consistent with this file's existing style of bare module
  constants like `_RANGE_DAYS` for presentation-layer knobs, not
  `settings.py` fields).
- New pure helper `_macro_benchmark_staleness_note(curve_anchor, raw_macro_benchmark, macro_benchmark) -> Optional[str]`:
  compares the **raw (unsliced)** macro curve's last date against
  `curve_anchor`; returns `None` whenever there's no anchor, no macro
  series, or the gap is within tolerance; otherwise returns e.g.
  `"S&P 500 overlay data is only available through {date} ({N} days behind
  the Pilot's own track record)."` Never raises.
- In `pilot_performance()`: compute `raw_curve`/`curve_available`/`curve_anchor`
  **before** slicing benchmark/macro_benchmark; pass `anchor_date=curve_anchor`
  to both slice calls; compute `macro_benchmark_note` once via the helper;
  add `"macro_benchmark_note"` to **all four** return dicts (`None` in both
  early "no validation at all" branches, the computed value in the
  success/no-curve branches). Document in a comment that the existing
  `benchmark = macro_benchmark` fallback (when `benchmark_curve` is absent)
  means the note — rendered next to the S&P 500 dot — also honestly explains
  the "Benchmark" line's truncation in that case.
- Update the function's docstring to describe the new field and anchor
  behavior.
- **Verified no regression**: the only fixture with all three curves
  (`tests/fixtures/timeseries_momentum_validation_summary.json`) has them
  ending on the identical date, so anchoring produces byte-identical output;
  every "curve absent" test (`raw_curve` key missing entirely) has
  `curve_anchor=None`, which self-anchors exactly as today.
- **Tests** (`tests/test_pilots_performance.py`, new class
  `TestPilotPerformanceMacroBenchmarkStaleness`, using the file's existing
  inline-`tmp_path`-JSON idiom): stale macro (gap > tolerance) → sliced to
  the anchor window + non-empty note; gap ≤ tolerance → note `None`;
  exact-match dates → note `None`; re-confirm the existing
  `test_macro_benchmark_is_tail_sliced_like_curve`/`test_benchmark_is_tail_sliced_like_curve`
  still pass unchanged against the existing fixture.
- No change needed to `api/pilots_api.py::get_pilot_performance` (bare
  pass-through, `Dict[str, Any]` return, no Pydantic model) or
  `investyo_mcp_server.py`'s pilot-performance tool.

### 3. Frontend (`webapp/`)

- `webapp/src/api/types.ts` — add `macro_benchmark_note?: string | null;` to
  `PerformanceResponse` (optional, matching the sibling `reason?: string`
  convention already in this interface — so existing hand-built test mocks
  elsewhere compile unchanged).
- `webapp/src/api/mock.ts::getPerformance` — add `macro_benchmark_note: null`
  to both return branches, for live/mock shape parity.
- `webapp/src/screens/PilotDetail.tsx` — render `perf.data.macro_benchmark_note`
  as **always-visible** small text right after the legend row (not a
  hover-only tooltip — matches this same file's existing convention of
  rendering `perf.data?.reason` as plain visible text). A silently-missing
  line is exactly the kind of thing a hover-only affordance would still let
  a user miss, and at narrow range toggles the S&P line can render nothing
  at all (a confirmed, disclosed interaction with `_slice_curve_by_range`'s
  existing "last 2 points" fallback when the whole requested window predates
  the stale series) — the text note is the only thing telling the user why.
- No changes needed to `webapp/src/components/charts.tsx` (`PerfLine`) or
  `Portfolio.tsx` (its `macroBenchmark` prop usage is an unrelated
  buying-power overlay from a different endpoint).
- **Tests**: `webapp/src/api/mock.test.ts` — additive assertion that
  `macro_benchmark_note` is `null` or a string. `webapp/src/screens/PilotDetail.test.tsx` —
  new test mocking `api.getPerformance` with a `macro_benchmark_note` string
  and asserting it renders visibly.

### 4. Docs

- `docs/architecture/webapp-and-gui.md` (the `scripts/refresh_validations.py`
  bullet, currently says "over yfinance history") — correct to state the
  strategy-side fetch is FMP-primary (already true since 2026-08-21) and
  that the macro-benchmark SPY overlay inside `validation.harness.StrategyValidationHarness`
  is now FMP-primary/yfinance-fallback too (this change).
- `docs/architecture/validation-and-signals.md` — add a sentence to the
  `validation/harness.py` bullet describing the two-tier SPY sourcing and
  the new `macro_benchmark_note` field (currently zero mentions of
  `_spy_return_series`/the macro overlay in this doc).
- New `docs/known_issues/sp500_macro_overlay_yfinance_truncation.md`,
  following this repo's established `## Status` / `## Symptom` /
  `## Root cause` / `## Fix (this PR)` / `## Scope boundary` template
  (matching `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`'s
  structure). Scope boundary section states plainly: the anchor-slicing fix
  and disclosure note apply immediately to every persisted report; they do
  **not** retroactively extend an already-short `macro_benchmark_curve` —
  that needs a live `scripts/refresh_validations.py` re-run per strategy.
- Add one row to `docs/known_issues/README.md`'s index table.
- Per CLAUDE.md's PR-artifact convention: copy this implementation plan (and
  a short task tracker / walkthrough) into `.claude/` with a unique,
  feature-scoped name (e.g. `.claude/sp500_macro_overlay_implementation_plan.md`)
  as part of the PR.

## Critical files

- `validation/harness.py` — SPY fetch dispatcher (FMP-primary, yfinance-fallback)
- `pilots/performance.py` — anchor-date slicing + staleness note
- `tests/test_harness_equity_curve.py` — new dispatcher tests
- `tests/test_pilots_performance.py` — new anchor/note tests
- `webapp/src/api/types.ts`, `webapp/src/api/mock.ts` — new field
- `webapp/src/screens/PilotDetail.tsx` — visible disclosure text
- `docs/architecture/webapp-and-gui.md`, `docs/architecture/validation-and-signals.md`,
  new `docs/known_issues/sp500_macro_overlay_yfinance_truncation.md`,
  `docs/known_issues/README.md`

## Verification

1. `pytest tests/test_harness_equity_curve.py tests/test_pilots_performance.py -q`
   — all existing + new tests green (offline, no network/live FMP needed;
   FMP calls are monkeypatched in every test).
2. `npm run --prefix webapp typecheck` — clean.
3. `npm run --prefix webapp test -- mock.test.ts PilotDetail.test.tsx` (or the
   project's equivalent vitest invocation) — green.
4. Manual/browser check (`verify-webapp` style): with a hand-crafted
   validation-summary fixture whose `macro_benchmark_curve` ends earlier than
   `equity_curve`, confirm via the webapp dev server that the S&P 500 legend
   now shows the visible disclosure text instead of a silently-shortened
   line with no explanation.
5. Full `make verify` / targeted `pytest` gate before merge, per CLAUDE.md's
   verification-is-mandatory rule — since this is a "Everything else" tier
   change (`validation/`), merge only after this passes green in this
   session, plus a self-review of the full diff.
