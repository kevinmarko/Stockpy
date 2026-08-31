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

## Explicitly dropped from original scope
- ~~Create `svi_stitching_visualizer.html` artifact~~ — never delivered, not part of this PR
- ~~Scaffold HTML/Tailwind CSS~~ — dropped with the above
- ~~Implement mock data generator (240 days, events on 45, 115, 195)~~ — superseded by real SPY-volume
  fetching through the consolidated endpoint
- ~~Implement Canvas/SVG rendering for lines and overlap windows~~ — rendering is `TrendsVisualizer.tsx`'s
  job (already shipped in PR #953); this PR is backend-only
- ~~Add interactive tooltips/annotations~~ — same as above, out of scope for this PR
