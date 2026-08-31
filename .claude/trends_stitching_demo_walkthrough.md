# Walkthrough: Google Trends SVI Stitching Visualization

> **POST-AUDIT REVISION.** An earlier version of this file described PR #953's
> original, pre-remediation design: a backend endpoint that generated
> synthetic SVI data server-side and stitched it via
> `GoogleTrendsStitcher.stitch_multiple_intervals`, with a test asserting an
> HTTP 200 OK payload. That design was superseded across two rounds of fixes.
> First, a CONSTRAINT #4 (never fabricate data) remediation made the backend
> endpoint fail closed instead of fabricating SVI curves. Then an independent
> audit of the result found the mock-mode demo itself didn't actually
> demonstrate the stitching algorithm — it drew three unrelated random walks
> and labeled the third one "Stitched Output" with no real relationship to
> the other two. This revision documents what actually shipped after both
> fixes, verified against the code in this session, not the original plan.

## Overview
The Pilots PWA has a Google Trends SVI overlapping-window stitching
visualization at `/research/trends-stitcher`. The backend endpoint that was
originally meant to fetch and stitch live SVI data is intentionally
unimplemented and fails closed with an HTTP 501 rather than fabricating a
plausible-looking response (CONSTRAINT #4). The demo is fully mock-mode only:
`webapp/src/api/mock.ts` generates three synthetic, overlapping, differently-
scaled SVI windows and runs them through a genuine client-side TypeScript
port of `data/trends_stitcher.py`'s stitching algorithm to produce the
"stitched" curve shown in the chart — so the demo now actually demonstrates
the algorithm it claims to, rather than faking its output.

## Architecture and Execution

### Backend API — fails closed, does not fabricate data
- **Endpoint:** `GET /data/trends/stitch-demo` in `api/data_api.py`.
- **Behavior:** unconditionally raises `HTTPException(501, detail="Live SVI
  fetching not implemented. Use mock mode to view the demo.")`. It never
  fetches real Google Trends data and never generates synthetic data
  server-side. There is no server-side call into
  `GoogleTrendsStitcher.stitch_multiple_intervals` on this route at all —
  that function lives in `data/trends_stitcher.py` and is exercised by its
  own Python test suite (`tests/test_trends_stitcher.py`), not by this
  endpoint.
- **Why 501 instead of a real implementation:** live SVI fetching (pytrends
  or an equivalent) was never wired up for this demo route, and returning a
  plausible-looking 200 with fabricated numbers would violate this repo's
  CONSTRAINT #4. The honest answer is "not implemented," surfaced as a real
  HTTP error status, not a happy-path payload with made-up values.

### Frontend — the real stitching algorithm runs client-side, in mock mode only
- **`webapp/src/utils/trendsStitch.ts`** — a new, pure, dependency-free
  TypeScript port of `data/trends_stitcher.py::GoogleTrendsStitcher`'s
  `stitch_intervals`/`stitch_multiple_intervals`. It reproduces the same
  algorithm: find the overlapping timestamps between two windows, replace
  zero values with `0.1` within the overlap only (guards a degenerate
  scaling factor), compute `scaling_factor = sum(overlap_A) / sum(overlap_B)`
  (falling back to `1.0` if the overlap-B sum is `<= 1e-9`), rescale the
  *entire* later window by that factor, merge via A-takes-precedence
  (`combine_first`-style) semantics, and smooth the boundary at the overlap
  timestamps by averaging A's original value and B's scaled value.
  `stitchMultipleIntervals` left-folds this pairwise stitch across an
  ordered list of windows. This is genuinely unit-tested in
  `webapp/src/utils/trendsStitch.test.ts` (5 tests: recovering a known
  scaling multiple, a zero-overlap window throwing, an all-zero overlap
  passing through via the `1.0` fallback, chaining across 3 windows, and
  empty/single-input edge cases).
- **`webapp/src/api/mock.ts`'s `getTrendsStitchDemo()`** — since the backend
  is deliberately unimplemented, this mock is the only place the demo's data
  comes from (used whenever the PWA runs with `VITE_USE_MOCK=true`, the
  default). It generates three genuinely overlapping windows, each on a
  *different* absolute baseline — simulating how Google Trends renormalizes
  each query window independently against its own peak, which is exactly the
  distortion the stitching algorithm exists to correct:
  - Window A: days -100..-40, baseline ~50
  - Window B: days -60..-15, baseline ~100 — overlaps A on days -60..-40 (~20 days)
  - Window C: days -25..0, baseline ~30 — overlaps B on days -25..-15 (~10 days)

  Each window is generated via a deterministic seeded PRNG (`mulberry32`) so
  the demo is reproducible across renders rather than reseeding chaos on
  every fetch. The mock then calls the real `stitchMultipleIntervals([windowA,
  windowB, windowC])` from `trendsStitch.ts` to produce `stitched_curve` —
  the stitched output is genuinely derived from the three raw curves, not a
  fourth independently-generated series.
- **`webapp/src/api/types.ts`** — defines `TrendsCurve` (`{ name: string;
  data: [number, number][] }`) and `TrendsStitchDemoResponse` (`{
  raw_curves: TrendsCurve[]; stitched_curve: TrendsCurve }`) as the single
  source of truth for this shape.
- **`webapp/src/components/charts/TrendsStitchChart.tsx`** — previously
  redeclared its own local `TrendsCurve` interface, duplicating the one in
  `types.ts`. It now imports `TrendsCurve` from `../../api/types` (and
  re-exports it for convenience) instead of maintaining a second, divergent
  definition. Renders via `recharts`: the raw windows as low-opacity, dashed
  gray lines and the stitched output as a bold blue solid line, inside a
  `<ResponsiveContainer width="100%" height="100%">` nested inside a parent
  `div` with an explicit `h-[400px]`. Both pieces are required together —
  `ResponsiveContainer` alone with no height-bounded ancestor collapses to
  zero width inside a flex layout, which was an earlier bug in this same PR
  that is now fixed.
- **`TrendsVisualizer.tsx`** — the screen component; handles loading/error
  states and passes `useApi`'s result into the chart.
- **Routing:** registered at `/research/trends-stitcher` in `App.tsx`.

### Nav reachability — previously URL-only, now linked from two places
The screen was originally reachable only by typing the URL directly; no link
to it existed anywhere in the app. This has been fixed in two places:
- **`webapp/src/screens/ResearchHub.tsx`** — has a new card/tile linking to
  `/research/trends-stitcher` ("Google Trends SVI Stitching").
- **`webapp/src/navigation.tsx`**'s `NAV_ITEMS` — has a matching entry
  (`label: "Trends Stitching"`, `section: "research"`), which drives both
  the desktop sidebar and the mobile bottom-nav "More" sheet, so the screen
  is now reachable from both desktop and mobile navigation, not just a
  direct URL.

## Testing & Validation
- **Backend:** `tests/test_data_api.py::test_get_trends_stitch_demo` asserts
  the endpoint returns `resp.status_code == 501` and
  `resp.json()["detail"] == "Live SVI fetching not implemented. Use mock
  mode to view the demo."` — it does **not** assert a 200 OK or any JSON
  array payload; the endpoint never returns one.
- **Frontend algorithm:** `webapp/src/utils/trendsStitch.test.ts` covers the
  stitching math itself in isolation (5 tests, described above) — this is
  what actually proves the algorithm is correct, independent of any chart
  rendering or mock fixture.
- **Frontend components:** written via Vitest + Testing Library,
  `TrendsStitchChart.test.tsx` and `TrendsVisualizer.test.tsx` confirm
  isolated rendering of the chart and screen components.

> [!NOTE]
> The charting library used is `recharts`, which was already a dependency in
> this codebase — an earlier plan had considered `echarts-for-react`, but the
> implementation used `recharts` instead without any feature loss.

> [!NOTE]
> There is no live-mode path for this demo. Running the PWA with
> `VITE_USE_MOCK=false` against the real backend will hit the 501 endpoint
> and surface an honest "not implemented" error in the UI rather than any
> chart — this is deliberate, not a bug to be filed.
