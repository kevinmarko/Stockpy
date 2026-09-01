# SVI Stitching Visualization Tasks

## Revision note
This tracker originally checked off a standalone HTML artifact (`svi_stitching_visualizer.html`) as
`[x]` complete. That file was never committed to this repo (confirmed by an audit of the branch's commit
history and an exhaustive filesystem search — it only ever existed in an IDE-local scratch directory) and
that work is not part of this PR's actual deliverable. The checklist below reflects what was actually
built: consolidating this branch's real-data-fetch logic into the already-shipped
`GET /data/trends/stitch-demo` endpoint from PR #953, instead of a second standalone endpoint or artifact.

## Tasks
- [x] Fetch real SPY trading volume via `HistoricalStore` as an explicitly-labeled proxy for Google
      Trends SVI (used only to demonstrate the stitching math against real, non-fabricated numbers — never
      presented as genuine search-volume data)
- [x] Consolidate into the existing `GET /data/trends/stitch-demo` endpoint (PR #953), removing this
      branch's separate `GET /data/svi-stitching-demo` route rather than shipping two endpoints doing the
      same thing
- [x] Fix the CONSTRAINT #4 (never fabricate a metric) violation: the endpoint's original fallback on a
      data-fetch failure was a flat, made-up series (`pd.Series([10.0] * 240)`) returned as a normal
      `200 OK` — despite an inline comment claiming the opposite. Replaced with an honest `HTTP 503` on
      any fetch failure; no fabricated fallback remains.
- [x] Fix a real bug: the SPY-fetch path stripped the real `DatetimeIndex` off the bars before slicing,
      losing real calendar dates and breaking the frontend's `[epoch_ms, value][]` timestamp contract.
      Fixed to keep the real dates through the whole pipeline.
- [x] Fix single-source-of-truth duplication in `data/trends_stitcher.py`: `stitch_intervals` now reuses
      `get_scaling_metadata`'s own computed overlap window instead of recomputing it independently
- [x] Update `docs/signals/google_trends_asvi.md` to document the live-mode demo's real-data behavior,
      the 503-on-failure contract, and the new `get_scaling_metadata` method
- [x] Add automated tests:
  - [x] Consolidated endpoint's happy path (real SPY data present → valid stitched response)
  - [x] Consolidated endpoint's insufficient-history path (asserts `503`, never a fabricated `200`)
  - [x] Direct unit test for `GoogleTrendsStitcher.get_scaling_metadata`
- [x] Run the relevant test files and confirm a clean pass before merge (see walkthrough for the actual
      results)

## Second audit round (6-agent Workflow audit, 9/9 findings confirmed, all fixed)
A fresh 6-agent audit (constraint-compliance, data-integrity, api-webapp-contract, test-coverage,
doc-artifact-honesty, duplication-security-cleanup) with adversarial re-verification of every raised
finding was run against this PR's actual committed code (not its own self-report). 9 findings raised, 9
confirmed on independent re-check, 0 refuted. All 9 are fixed in this round:
- [x] `api/data_api.py`: moved the slicing/scaling/`stitch_intervals` computation inside the endpoint's
      existing `try` block, so any exception there also degrades to the honest `503` instead of an
      unhandled raw `500` (was reachable in principle, not in practice with today's fixed 240-bar slicing)
- [x] `webapp/src/components/charts/TrendsStitchChart.tsx`: fixed a real off-by-one-day bug — the backend
      encodes each date as UTC midnight, but the chart formatted it in the viewer's LOCAL timezone,
      shifting every date back a day for any US-based viewer. Now formats in UTC via an exported, directly
      unit-tested `formatUtcDate` helper.
- [x] `webapp/src/navigation.tsx` + `webapp/src/screens/Marketplace.tsx`: the screen (`/research/trends-
      stitcher`) had zero navigation entry points anywhere in the app — reachable only by typing the URL
      manually. Added a `NAV_ITEMS` entry and a Marketplace "Explore" tile, per this codebase's documented
      mobile-reachability convention for standalone research screens.
- [x] `webapp/src/screens/TrendsVisualizer.tsx`: the screen's headline title/description presented the
      chart as unqualified real Google Trends data, contradicting the backend's own honest "SPY Volume
      Proxy" curve labeling. Rewrote to disclose the proxy substitution up front; added a test asserting
      the disclosure text renders.
- [x] `webapp/src/api/mock.ts`: the mock fixture returned 2 unlabeled raw curves ("Trend A/B (Raw)")
      instead of the real live contract's 3 honestly-labeled curves — mock mode never exercised the real
      3-curve layout. Fixed to match.
- [x] `tests/test_data_api.py`: the happy-path test only checked shape/labels, never that returned values
      actually trace back to the injected fixture data — a fabrication regression (e.g. a fake linspace
      ramp) would have passed undetected. Added a fidelity assertion recomputing expected values/dates
      from the real fixture and comparing directly. Also added a regression test asserting the old,
      removed `GET /data/svi-stitching-demo` route stays a `404`.
- [x] `tests/test_trends_stitcher.py`: the SSOT regression test only proved behavioral equivalence, not
      structural delegation — a reintroduced duplicate (but still mathematically identical) overlap
      computation would have passed undetected. Added a `mock.patch.object(..., wraps=...)` spy test
      proving `stitch_intervals` genuinely calls `get_scaling_metadata`.
- [x] `.claude/svi_stitching_visualization_pr_body.md`: corrected an overclaim ("`GoogleTrendsStitcher`
      has zero callers anywhere in the live signal/pipeline path") — it's referenced via an unused,
      pre-existing import chain in `data/attention_sources.py` (imported by the live pipeline, but no
      method on the class is ever called from there). The safety conclusion was correct; the phrasing
      wasn't.

Verification for this round: `pytest tests/test_data_api.py tests/test_trends_stitcher.py -q` — **75
passed, 0 failed** (up from 73; +2 new test functions, plus new assertions inside existing tests).
`ruff check --select F,E9` on all touched Python files — all checks passed. `npm run --prefix webapp
typecheck` — clean. `npx vitest run` on the 4 touched webapp test files — **70 passed, 0 failed**.

## Explicitly dropped from original scope
- ~~Create `svi_stitching_visualizer.html` artifact~~ — never delivered, not part of this PR
- ~~Scaffold HTML/Tailwind CSS~~ — dropped with the above
- ~~Implement mock data generator (240 days, events on 45, 115, 195)~~ — superseded by real SPY-volume
  fetching through the consolidated endpoint
- ~~Implement Canvas/SVG rendering for lines and overlap windows~~ — rendering is `TrendsVisualizer.tsx`'s
  job (already shipped in PR #953); this PR is backend-only
- ~~Add interactive tooltips/annotations~~ — same as above, out of scope for this PR
