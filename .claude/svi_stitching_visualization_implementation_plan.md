# SVI Stitching Visualization Implementation Plan

## Goal Description
Make the live (non-mock) Pilots PWA's SVI-stitching demo actually work, by wiring
`data/trends_stitcher.py`'s `GoogleTrendsStitcher` overlapping-window stitching algorithm to a real data
source behind the existing `GET /data/trends/stitch-demo` endpoint (shipped by PR #953, currently a
hardcoded `501` in live mode) — instead of building a separate, duplicate endpoint or a standalone HTML
artifact.

## Revision note
This plan originally proposed building a standalone, interactive HTML widget
(`svi_stitching_visualizer.html`) rendered as a Studio/generative-UI artifact, backed by a new, separate
`GET /data/svi-stitching-demo` endpoint. That approach is **abandoned**. Two things changed:
1. The HTML artifact was never actually committed to the repo (it only ever existed in an IDE-local
   scratch directory) — a completed audit confirmed this via the branch's own commit history and an
   exhaustive filesystem search, so there is no artifact to finish or hand off.
2. PR #953 (merged before this branch's work started) had already shipped the real consumer of this data
   — `webapp/src/screens/TrendsVisualizer.tsx` — calling `GET /data/trends/stitch-demo`, which already
   works correctly in mock mode and returns an honest `501` in live mode. Building a second, separate
   endpoint duplicated that work and left the two disconnected. The corrected plan below **consolidates
   into the existing endpoint** rather than adding a new one; no webapp changes are required anywhere in
   this plan, since `TrendsVisualizer.tsx` already calls the route being fixed.

## User Review Required
- **Data source for live mode**: this codebase has no live Google Trends data source wired in anywhere.
  The plan uses real SPY trading volume (via `HistoricalStore`) as an explicitly-labeled stand-in/proxy
  for Google Trends SVI — never presented to the operator as real SVI data — purely to demonstrate the
  stitching math against real, non-fabricated numbers. This is a deliberate scope boundary, not a step
  toward a production search-attention feature.
- **Failure handling**: per CONSTRAINT #4 (never fabricate a metric), any data-fetch failure (insufficient
  SPY history, a store read error, etc.) must produce an honest `HTTP 503`, never a fallback series of
  made-up values returned with a `200 OK`. (The original branch's first attempt at this endpoint got this
  backwards — see the walkthrough for the specific bug and fix.)
- **Single source of truth**: `GoogleTrendsStitcher.stitch_intervals`'s overlap-window/scaling-factor
  logic must not be independently reimplemented at the call site or duplicated inside
  `get_scaling_metadata` — `stitch_intervals` delegates to `get_scaling_metadata` for that computation
  (including reusing its computed overlap window) rather than recomputing it.

## Open Questions
- None. The scope is now well-defined: make one existing, already-consumed endpoint work honestly in
  live mode, using real (proxy) data, with no fabricated fallback.

## Proposed Changes

### Backend Data API
#### [MODIFY] `api/data_api.py`
Replace the hardcoded `501` in `GET /data/trends/stitch-demo`'s live-mode branch with real logic: fetch
SPY bars via `HistoricalStore`, slice into overlapping ~90-day windows, run them through
`GoogleTrendsStitcher.stitch_intervals`, and return the same response shape the mock-mode path already
returns (so `TrendsVisualizer.tsx` needs no changes). Raise `HTTP 503` — never a fabricated series — when
there isn't enough history or the fetch otherwise fails. The earlier, separate
`GET /data/svi-stitching-demo` route is removed; its real-data-fetch logic is folded into this endpoint.

### Stitching Math — Single Source of Truth
#### [MODIFY] `data/trends_stitcher.py`
Tighten `GoogleTrendsStitcher.stitch_intervals` to reuse `get_scaling_metadata`'s own computed overlap
window (`meta["overlap_dates"]`) rather than computing it a second, independent way.

### Documentation
#### [MODIFY] `docs/signals/google_trends_asvi.md`
Document the live-mode demo endpoint's actual behavior — the SPY-volume proxy, the honest
503-on-failure contract, and the new `get_scaling_metadata` method — so this reachable code path isn't
left undocumented. (The original version of this plan claimed no `docs/` update was necessary; that was
wrong — this is a real, live-reachable code path change and needs documentation like any other.)

## Verification Plan

### Automated Tests
- Extend/add coverage in `tests/` for `GET /data/trends/stitch-demo`'s live-mode happy path (real SPY
  data present → valid stitched response, correct shape) and its insufficient-history path (asserts
  `503`, never a `200` with a fabricated series).
- Add/extend a direct unit test for `GoogleTrendsStitcher.get_scaling_metadata` covering the scaling-factor
  math and the overlap-window boundaries.
- Run the targeted test file(s) plus the existing `tests/test_trends_stitcher.py` suite.

### Manual Verification
- Confirm `TrendsVisualizer.tsx` in the Pilots PWA renders the stitched curves correctly against the live
  (non-mock) backend, with the SPY-volume-proxy labeling visible to the operator.
- Confirm a forced insufficient-history condition surfaces as an honest error in the UI rather than a
  silently-fabricated flat curve.

## Agent Handoff Notes
- This is a real, live-reachable backend code path — not an observability/educational artifact outside
  the production system — so it does need a documentation update
  (`docs/signals/google_trends_asvi.md`, done as part of this plan). The original plan's claim that "no
  `docs/architecture/` or `CLAUDE.md` updates are necessary" was correct only in that neither of those two
  specific files needs touching; it was wrong to conclude from that, that no documentation was needed at
  all.
- No `CLAUDE.md`/`docs/architecture/*.md` changes are needed — this doesn't add a new subsystem, setting,
  or architectural component, it fixes and completes an already-documented endpoint.
