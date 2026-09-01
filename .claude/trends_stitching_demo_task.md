# Google Trends SVI Stitching Visualization Tasks

- [x] Agent 1: Backend API
  - [x] Add `GET /data/trends/stitch-demo` endpoint to `api/data_api.py`
  - [x] Generate synthetic SVI curves and stitch them
- [x] Agent 2: Frontend Types & Client
  - [x] Update `webapp/src/api/types.ts`
  - [x] Update `webapp/src/api/client.ts`
  - [x] Update `webapp/src/api/mock.ts`
- [x] Agent 3: Frontend Visualization Component
  - [x] Create `webapp/src/components/charts/TrendsStitchChart.tsx`
- [x] Agent 4: Frontend Screen & Routing
  - [x] Create `webapp/src/screens/TrendsVisualizer.tsx`
  - [x] Add route to `webapp/src/App.tsx`
- [x] Agent 5: Backend Tests
  - [x] Add `test_get_trends_stitch_demo` to `tests/test_data_api.py`
- [x] Agent 6: Frontend Tests
  - [x] Create `webapp/src/components/charts/TrendsStitchChart.test.tsx`
  - [x] Create `webapp/src/screens/TrendsVisualizer.test.tsx`

## Post-Merge Remediation

- [x] Pass 1 — CONSTRAINT #4 fix: server-side fabrication removed
  - [x] `GET /data/trends/stitch-demo` (`api/data_api.py`) now unconditionally raises
        `HTTPException(501, detail="Live SVI fetching not implemented. Use mock mode to view
        the demo.")` instead of generating synthetic SVI curves and stitching them server-side
  - [x] Updated `tests/test_data_api.py::test_get_trends_stitch_demo` to assert the 501 status
        and exact detail string
- [x] Pass 2 — this session: mock demo now runs the real stitching algorithm + reachability fixes
  - [x] Added `webapp/src/utils/trendsStitch.ts`, a pure TS port of
        `data/trends_stitcher.py::GoogleTrendsStitcher` (`stitchIntervals`/`stitchMultipleIntervals`)
  - [x] Added `webapp/src/utils/trendsStitch.test.ts` unit tests for the ported algorithm
  - [x] `webapp/src/api/mock.ts`'s `getTrendsStitchDemo()` now generates 3 genuinely
        overlapping raw curves and derives `stitched_curve` via the real
        `stitchMultipleIntervals` call, replacing the earlier version that fabricated an
        unrelated fourth random walk and mislabeled it as the stitched output
  - [x] Deduped `TrendsCurve` type: `webapp/src/components/charts/TrendsStitchChart.tsx` now
        imports/re-exports the canonical type from `webapp/src/api/types.ts` instead of
        redeclaring its own
  - [x] Added nav reachability for `/research/trends-stitcher`: linked from
        `webapp/src/screens/ResearchHub.tsx` and `webapp/src/navigation.tsx` (`NAV_ITEMS`,
        covering both desktop sidebar and mobile bottom-nav "More" sheet) — previously
        reachable only by typing the URL directly
