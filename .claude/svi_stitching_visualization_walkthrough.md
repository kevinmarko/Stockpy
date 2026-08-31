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

## Next steps
None outstanding — this PR's scope (consolidate the endpoint, fix the fabrication bug, fix the dropped-
dates bug, fix the single-source-of-truth duplication, document the change, add test coverage) is
complete and verified above.
