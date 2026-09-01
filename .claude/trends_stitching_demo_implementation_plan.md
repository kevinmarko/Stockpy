# Google Trends SVI Stitching Visualization

Integrating the Google Trends stitching logic (from `data/trends_stitcher.py`) into the Pilots PWA to visually demonstrate the overlapping window stitching algorithm.

## Framework Selection: React + Vite
We have chosen **React + Vite** for this implementation because:
1. **Existing Stack**: The user's frontend (Pilots PWA) is already built in React + Vite (`webapp/src`).
2. **Visual Fidelity**: The request requires charting three overlapping raw SVI curves alongside a final stitched curve, which is perfectly suited for **ECharts** (already integrated into the React frontend).
3. **Architecture**: It aligns with the existing decoupled Frontend (React) + Backend API (FastAPI) architecture.

## User Review Required
- **Agent Delegation**: This plan is designed to be executed concurrently by **6 specialized subagents**. Please review the delegation strategy below.
- **Data Source**: The demo endpoint will generate realistic synthetic SVI curves (using `numpy` random walks) to demonstrate the stitching algorithm's seamless transitions, since fetching real Google Trends data requires rate-limited external calls. Let me know if you prefer to hook this up to live data instead.

## Open Questions
- **Navigation**: Where should this new screen be added in the navigation? (e.g., under the "Research" section or the "Data Explorer"?)
- **Chat Interface**: Would you like to include a Gemini-powered chat interface on this new screen to enable natural language queries against the trends data? Let me know and I'll update the plan!

## Proposed Changes

We will execute this plan using **6 autonomous subagents**, each tackling a specific slice of the stack.

### 1. Backend API (Agent 1)
Creates the endpoint to serve the stitching demonstration data.
#### [MODIFY] api/data_api.py
- Add `GET /data/trends/stitch-demo` endpoint.
- Generate 3 overlapping 90-day SVI curves (using realistic random walks).
- Use `GoogleTrendsStitcher.stitch_multiple_intervals` to create the final continuous curve.
- Return the data as JSON (arrays of `[timestamp, value]`).

### 2. Frontend Types & Client (Agent 2)
Wires the frontend API layer to the new backend endpoint.
#### [MODIFY] webapp/src/api/types.ts
- Define `TrendsStitchDemoResponse` interface containing the 3 raw curves and the stitched curve.
#### [MODIFY] webapp/src/api/client.ts
- Add `getTrendsStitchDemo()` client method.
#### [MODIFY] webapp/src/api/mock.ts
- Provide a mocked implementation for `getTrendsStitchDemo()`.

### 3. Frontend Visualization Component (Agent 3)
Builds the ECharts visualization.
#### [NEW] webapp/src/components/charts/TrendsStitchChart.tsx
- Create an ECharts React component.
- Plot the 3 raw unscaled SVI curves (e.g., as dotted lines in different colors).
- Plot the final stitched continuous curve (as a solid, bold line).
- Follow the zinc color palette and `JetBrains Mono` typography.

### 4. Frontend Screen & Routing (Agent 4)
Creates the page and wires it into the app navigation.
#### [NEW] webapp/src/screens/TrendsVisualizer.tsx
- Fetch data using `useApi(getTrendsStitchDemo)`.
- Render the `TrendsStitchChart` inside a standard card layout.
#### [MODIFY] webapp/src/App.tsx
- Add a new route `/research/trends-stitcher` for the screen.

### 5. Backend Tests (Agent 5)
Ensures backend reliability.
#### [MODIFY] tests/test_data_api.py
- Add `test_get_trends_stitch_demo` to verify the endpoint returns the correct JSON structure and HTTP 200.

### 6. Frontend Tests (Agent 6)
Ensures frontend component rendering.
#### [NEW] webapp/src/components/charts/TrendsStitchChart.test.tsx
- Write a Vitest/React Testing Library test to verify the chart renders without crashing when provided with mock data.
#### [NEW] webapp/src/screens/TrendsVisualizer.test.tsx
- Write a test to ensure the screen fetches data and renders the chart.

## Verification Plan

### Automated Tests
- Run `pytest tests/test_data_api.py`
- Run `npm run --prefix webapp test`

### Manual Verification
- Start the backend and frontend (`./launch_webapp.command`).
- Navigate to the new Trends Visualizer screen.
- Visually confirm that the overlapping boundary steps are completely seamless on the stitched curve, as requested.

### 7. Documentation Updates
#### [MODIFY] docs/signals/google_trends_asvi.md
- Add a new "Visualizations" section documenting the newly created TrendsVisualizer screen and how it demonstrates the stitching algorithm using the frontend `mock.ts` integration.

## Post-Merge Remediation (Audit Findings)

The sections above are the historical record of what was originally proposed and built by
the 6 subagents. They are left intact. This section documents two remediation passes that
happened afterward, both of which changed what actually shipped relative to "### 1. Backend
API (Agent 1)" above — read that section as the original proposal, and this section as what
replaced part of it and why.

### Pass 1: CONSTRAINT #4 fix — backend fails closed instead of fabricating SVI data

An independent audit found that "### 1. Backend API (Agent 1)" as originally proposed and
built violated CONSTRAINT #4 (never fabricate a metric on a live endpoint,
`.claude/skills/stockpy-quant-integrity/`): `GET /data/trends/stitch-demo` in
`api/data_api.py` generated 3 synthetic SVI curves server-side via `numpy` random walks and
ran the real `GoogleTrendsStitcher.stitch_multiple_intervals` over them, then served the
result as if it were real data over a live API endpoint. A live endpoint returning
plausible-looking, entirely fabricated Google Trends SVI data is exactly the failure mode
CONSTRAINT #4 exists to prevent — nothing in the response distinguished "measured" from
"invented," and a caller (or an operator glancing at the Research Hub) had no way to tell
the difference.

**Fix**: `GET /data/trends/stitch-demo` in `api/data_api.py` now unconditionally raises
`HTTPException(501, detail="Live SVI fetching not implemented. Use mock mode to view the
demo.")`. No server-side generation, no server-side stitching — the endpoint fails closed
per CONSTRAINT #6 rather than serving fabricated data. `tests/test_data_api.py`'s
`test_get_trends_stitch_demo` was updated to assert the 501 status and the exact detail
string, replacing whatever assertion originally checked for a 200 + curve payload.

This means the demo is mock-mode-only until a real Google Trends data source is wired up —
a deliberate, disclosed limitation, not an oversight. The "Data Source" note in the
"User Review Required" section above (which floated the idea of hooking this up to live
data) was never acted on; this fix formalizes the "not yet" answer as an explicit 501
rather than a silent fabrication.

### Pass 2: This session's fixes — mock demo must demonstrate the real algorithm, plus reachability

A follow-up audit of the mock-mode path (the only path left standing after Pass 1) found
three further problems, all fixed in this session:

1. **Mock data didn't actually demonstrate the stitching algorithm.** `webapp/src/api/mock.ts`'s
   `getTrendsStitchDemo()` generated 3 overlapping random-walk curves plus a FOURTH,
   unrelated random walk mislabeled as `stitched_curve` — the "Stitched Output" line on the
   chart was never actually derived from the 3 raw curves at all. This defeats the entire
   purpose of the demo: a mock that doesn't run the real algorithm doesn't demonstrate it, it
   just draws a plausible-looking line next to some other lines. Per the same reasoning
   CONSTRAINT #4/#6 apply to live endpoints — the mock demo must actually demonstrate the
   algorithm it claims to, not fabricate an unrelated curve standing in for it.

   **Fix**: added `webapp/src/utils/trendsStitch.ts`, a pure TypeScript port of
   `data/trends_stitcher.py::GoogleTrendsStitcher` (`stitchIntervals`/`stitchMultipleIntervals`,
   including the overlapping-window scaling-factor computation), unit-tested in
   `webapp/src/utils/trendsStitch.test.ts`. `mock.ts`'s `getTrendsStitchDemo()` now generates
   3 genuinely overlapping windows on different absolute baselines (simulating how Google
   Trends renormalizes each query window to its own 0-100 scale) and calls the real
   `stitchMultipleIntervals` to derive `stitched_curve` — so the mock demo now genuinely
   demonstrates the same stitching math the Python engine performs, run client-side.

2. **Duplicated `TrendsCurve` type.** `webapp/src/components/charts/TrendsStitchChart.tsx`
   redeclared its own local `TrendsCurve` interface instead of importing the canonical one
   from `webapp/src/api/types.ts` — a drift risk (the two could silently diverge). Fixed:
   the component now imports/re-exports the canonical type from `types.ts`.

3. **Screen was unreachable from navigation.** The route `/research/trends-stitcher` (added
   in "### 4. Frontend Screen & Routing (Agent 4)" above) was never linked from anywhere in
   the app — reachable only by typing the URL directly, which meant an operator would never
   discover it. Fixed: `webapp/src/screens/ResearchHub.tsx` and `webapp/src/navigation.tsx`
   (`NAV_ITEMS`, which drives both the desktop sidebar and the mobile bottom-nav "More"
   sheet) now both link to `/research/trends-stitcher`.

**Note on charting library**: the plan above (see "Framework Selection") originally
considered `echarts-for-react`. The component that actually shipped
(`TrendsStitchChart.tsx`) uses `recharts` instead — it was already a dependency of the
webapp, so this was a substitution made during the original build with no feature loss, not
part of either remediation pass.
