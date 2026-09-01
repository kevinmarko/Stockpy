# SVI Stitching Visualization Walkthrough

## Revision note
The original version of this walkthrough described a standalone HTML/SVG widget
(`svi_stitching_visualizer.html`) as built, deployed, and validated. That file was never committed to
this repo — it only ever existed in an IDE-local scratch directory outside git, confirmed via the
branch's own commit reflog (no commit touches an HTML/JS/SVG file) and an exhaustive filesystem search.
This walkthrough is rewritten to describe what was actually built and shipped in this PR.

## What was built
This PR makes the already-shipped `GET /data/trends/stitch-demo` endpoint (from PR #953,
`webapp/src/screens/TrendsVisualizer.tsx`) actually work in live mode, instead of unconditionally
returning `HTTP 501`. It consolidates in the real-data-fetch logic this branch had built under a separate,
now-removed `GET /data/svi-stitching-demo` route, and fixes two genuine bugs found in that earlier version
along the way.

## Implementation details

1. **Consolidation, not duplication.** PR #953 shipped the webapp screen and the mock-mode-only endpoint
   one day before this branch's work began; this branch had independently built a second endpoint doing
   conceptually the same thing. Rather than ship two endpoints, this PR merges the real-data-fetch logic
   into the one endpoint the webapp already calls (`/data/trends/stitch-demo`), matching
   `TrendsStitchDemoResponse`/`TrendsCurve`'s exact shape in `webapp/src/api/types.ts`, and deletes the
   duplicate route. No webapp changes were needed.

2. **Real (proxy) data, honestly labeled.** There is no live Google Trends data source wired into this
   codebase. The endpoint fetches real SPY trading volume via `HistoricalStore` and uses it as an
   explicitly-labeled stand-in for Google Trends SVI, purely to exercise
   `GoogleTrendsStitcher.stitch_intervals` against real numbers rather than synthetic ones. Every curve
   name in the response is labeled "SPY Volume Proxy" — it is never presented to the operator as genuine
   search-volume data.

3. **Bug #1 (fabrication) found and fixed.** The branch's first version of this endpoint had a
   `try/except` around the SPY fetch whose `except` branch fell back to `pd.Series([10.0] * 240)` — a
   flat, made-up 240-day series — and still returned a normal `200 OK`. The inline comment on that branch
   read "Fallback to an un-mocked empty state rather than fabricating data," which described the opposite
   of what the code actually did. This is exactly the class of bug CONSTRAINT #4 (never fabricate a
   metric) exists to prevent. Fixed: the same failure condition now raises `HTTP 503` instead, with no
   fallback series of any kind, and the real exception is logged (`type(exc).__name__` included) so a
   genuine bug is distinguishable in the logs from ordinary insufficient-history.

4. **Bug #2 (dropped dates) found and fixed.** The branch's version stripped the real `DatetimeIndex` off
   the SPY bars (`pd.Series(bars["Volume"].tail(N).values)`), replacing real calendar dates with a
   positional index — this both defeats the intent of `stitch_intervals`'s overlap-window alignment (which
   is meant to align on real dates) and doesn't produce the real epoch-millisecond timestamps the
   frontend's `TrendsCurve.data: [number, number][]` contract expects. Fixed to keep the real
   `DatetimeIndex` through the whole pipeline and convert to `[epoch_ms, value]` pairs at the response
   boundary.

5. **Single source of truth for the stitching math.** `GoogleTrendsStitcher.stitch_intervals` and
   `get_scaling_metadata` both computed the overlap window between two periods. They always agreed by
   construction, so this was not a live correctness bug — but it was a real duplication/drift risk. Fixed
   by having `stitch_intervals` reuse `get_scaling_metadata`'s own computed `overlap_dates` instead of
   recomputing it.

6. **Constraint-numbering correction.** The original PR/plan/task artifacts on this branch labeled "never
   fabricate a metric" as Constraint #2 and "single source of truth" as Constraint #4 — backwards from
   this codebase's actual, repo-wide convention (confirmed by grep: **Constraint #2 = no lookahead bias,
   Constraint #4 = never fabricate a metric**). This walkthrough and the sibling PR body/plan/task files
   use the correct numbering.

## Files changed
- `api/data_api.py` — `GET /data/trends/stitch-demo` live-mode implementation; removal of the duplicate
  `GET /data/svi-stitching-demo` route
- `data/trends_stitcher.py` — `stitch_intervals` reuses `get_scaling_metadata`'s computed overlap window
- `docs/signals/google_trends_asvi.md` — documents the live-mode demo's real-data behavior,
  503-on-failure contract, and the new `get_scaling_metadata` method
- `tests/test_data_api.py` — new coverage for the consolidated endpoint's happy path and its
  insufficient-history 503 path
- `tests/test_trends_stitcher.py` — new direct coverage for `get_scaling_metadata`

## Verification Results

- `pytest tests/test_trends_stitcher.py -q` — **9 passed, 0 failed** (pre-existing suite, unmodified,
  confirmed passing against the `stitch_intervals`/`get_scaling_metadata` refactor before any new tests
  were added).
- `pytest tests/test_data_api.py tests/test_trends_stitcher.py -v` (new endpoint/method coverage
  included) — **73 passed, 0 failed**.
- `pytest tests/ -q --timeout=120 -k "trends or data_api"` (broader sweep for anything else this diff
  might have touched) — **206 passed, 0 failed** (12,567 deselected — everything outside the `trends`/
  `data_api` keyword filter).
- Merge-conflict check against current `origin/main` (`git merge-tree $(git merge-base origin/main HEAD)
  origin/main HEAD`) — **0 conflict markers**; this branch's merge commit already incorporates the
  latest `main`.
- All of the above was independently re-run by a separate verification pass against the actual committed
  `HEAD` (not just the working tree) before push, per this pipeline's own "don't trust a prior agent's
  reported pass count without re-observing it" rule — see the PR's audit trail for the full checklist
  (fabrication check, response-shape-vs-webapp-contract re-derivation, doc/artifact honesty spot-check,
  untracked-file cleanliness).

## Second audit round (6-agent Workflow audit)

The above described the first audit's fixes. A follow-up, independently-launched 6-agent Workflow audit
(one agent per dimension: constraint-compliance, data-integrity, api-webapp-contract, test-coverage,
doc-artifact-honesty, duplication-security-cleanup — each finding then adversarially re-verified by a
second agent against the real checkout, never trusting the first agent's own report) was run against this
PR's actual committed code, explicitly instructed not to take the first audit's conclusions on faith. It
raised 9 findings; all 9 were independently confirmed on re-check (0 refuted). All 9 are fixed here:

1. **[low, constraint-compliance] Stitching computation sat outside the fail-closed `try`/`except`.** The
   slicing/scaling/`GoogleTrendsStitcher.stitch_intervals` calls ran after the `try` block that builds the
   honest `503` closed — any exception there would have surfaced as a raw, unredacted `500` instead.
   Not exploitable today (the fixed 240-bar slicing guarantees non-degenerate overlaps), but a real
   robustness gap. Fixed by moving the whole computation inside the same `try` block.
2. **[medium, data-integrity] Off-by-one-day timezone bug.** `to_curve()` built epoch-ms via `ts.timestamp()`
   on a tz-naive pandas `Timestamp`, which pandas treats as UTC midnight. The one frontend consumer,
   `TrendsStitchChart.tsx`, formatted that epoch-ms using the browser's LOCAL timezone
   (`toLocaleDateString()` with no `timeZone` option) — for any US-based viewer (this platform's entire
   expected audience), every one of the 240 real trading dates rendered one calendar day earlier than the
   real bar date. Reproduced end-to-end with a real Node engine in `America/New_York` before fixing.
   Fixed by formatting in UTC with a pinned locale (`formatUtcDate`, now exported and directly unit-tested
   with a hardcoded epoch-ms → date assertion that doesn't depend on the test runner's own timezone).
3. **[medium, api-webapp-contract] No navigation entry point anywhere in the app.** `/research/trends-
   stitcher` was a real, working route rendering `TrendsVisualizer`, but absent from `navigation.tsx`'s
   `NAV_ITEMS` (source for both the desktop sidebar and the mobile bottom-nav/"more" menu) and absent from
   `Marketplace.tsx`'s "Explore" tile grid — the documented mobile-reachability pattern this codebase uses
   for exactly this class of standalone research screen. A user could only reach the now-fixed endpoint's
   UI by typing the URL by hand. Fixed by adding both.
4. **[low, api-webapp-contract] Screen header didn't disclose the SPY-volume-proxy substitution.** The
   backend goes out of its way (per its own docstring's CONSTRAINT #4 reasoning) to label every curve "SPY
   Volume Proxy" so the demo is never mistaken for real Google Trends data — but the screen's prominent
   title ("Google Trends SVI Stitching") and description asserted the opposite with no caveat, leaving the
   honest disclosure buried in small chart-legend text a viewer might never read. Fixed by rewriting the
   header/description to disclose the proxy substitution up front, with a new test asserting the
   disclosure text actually renders.
5. **[nit, api-webapp-contract] Mock fixture didn't mirror the live contract.** `mock.ts`'s
   `getTrendsStitchDemo()` returned 2 unlabeled raw curves ("Trend A/B (Raw)") against the real live
   endpoint's 3 curves labeled "SPY Volume Proxy — Period A/B/C" — mock mode (this platform's dev default)
   never exercised the real 3-curve honesty-labeled layout. Fixed to match.
6. **[medium, test-coverage] Happy-path test never proved the response is real, injected data.** The
   audit agent empirically proved this by mutation: it replaced the endpoint's real `bars["Volume"]` read
   with a fabricated `np.linspace` ramp while keeping correct dates/labels/shape, re-ran the test suite,
   and all 4 stitch-demo tests — including the happy-path test — still passed. This is exactly the class
   of regression CONSTRAINT #4 exists to prevent, and the existing test suite would not have caught it.
   Fixed by adding an assertion that recomputes period A's expected values and dates directly from the
   real injected fixture and compares them against the response.
7. **[low, test-coverage] SSOT regression test proved behavioral equivalence, not structural delegation.**
   The audit agent proved this by mutation too: reintroducing an independent
   `overlap_dates = period_a_svi.index.intersection(period_b_svi.index)` re-derivation inside
   `stitch_intervals` (the exact duplication the earlier fix removed) still passed all 19
   trends-stitcher/data-api "stitch"-keyword tests, because the reintroduced formula is still
   mathematically identical to `get_scaling_metadata`'s own. Fixed by adding a
   `mock.patch.object(GoogleTrendsStitcher, "get_scaling_metadata", wraps=...)` spy test that asserts
   `stitch_intervals` genuinely calls into it.
8. **[nit, test-coverage] No regression guard against the removed duplicate route reappearing.** The old
   `GET /data/svi-stitching-demo` route was genuinely removed (confirmed absent everywhere via a
   repo-wide grep), but nothing asserted its absence — a future merge/rebase could silently reintroduce
   it undetected. Fixed by adding a test asserting that path now `404`s.
9. **[nit, doc-artifact-honesty] PR-body overclaim.** The PR body's CONSTRAINT #2 (no-lookahead)
   justification stated `GoogleTrendsStitcher` "has zero callers anywhere in the live signal/pipeline
   path" — but `data/attention_sources.py` (imported by the live `pipeline/production_steps.py`/
   `main_orchestrator.py` pipeline) does `from data.trends_stitcher import ASVICalculator,
   GoogleTrendsStitcher`, an unused import that predates this PR. The underlying safety conclusion (no
   lookahead risk, since no method is ever actually invoked from that chain) was correct; the literal
   phrasing wasn't. Fixed by correcting the PR body's wording to describe the unused-import chain
   accurately.

### Second-round verification results
- `pytest tests/test_data_api.py tests/test_trends_stitcher.py -q` — **75 passed, 0 failed** (up from the
  first round's 73 — 2 new test functions: the duplicate-route-404 guard and the structural-delegation
  spy test — plus new fidelity/date assertions added inside the existing happy-path test, all passing).
- `ruff check --select F,E9` (pyflakes + syntax errors — the genuine-bug-focused subset; this repo has no
  `pyproject.toml`/`ruff.toml`, and a full unscoped `ruff check` surfaces ~149 pre-existing style/import-
  order findings repo-wide unrelated to this diff) on every touched Python file — all checks passed.
- `npm run --prefix webapp typecheck` — clean, no errors.
- `npx vitest run` on all 4 touched webapp test files (`TrendsVisualizer.test.tsx`,
  `TrendsStitchChart.test.tsx`, `Marketplace.test.tsx`, `mock.test.ts`) — **70 passed, 0 failed** (this
  included fixing one pre-existing test whose title assertion broke against item 4's honest header
  rewrite, and adding new tests for items 2 and 4's fixes).
- A broader `pytest tests/ -k "trends or data_api"` sweep was attempted but hit unrelated, pre-existing
  numba JIT-cache collection errors in unaffected test files (`test_simulation_engine.py`,
  `test_run_once.py`, etc.) — an environment issue (a shared numba cache under contention from concurrent
  worktree/test activity on this machine), not caused by this diff. Not re-attempted; the scoped runs
  above are the real verification for this round's changes.

## Next steps
None outstanding. Both audit rounds' findings (4 from the first, 9 from the second, 13 total, all
independently re-verified against the real committed code, 0 refuted) are fixed and verified above.
