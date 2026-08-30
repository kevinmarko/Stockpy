# SVI Stitching Visualization Implementation Plan

## Goal Description
Create an interactive, standalone HTML widget for the Antigravity Studio panel that visually demonstrates the continuous Google Trends SVI stitching algorithm. This visualization will recreate the static `svi_stitching_seamless.png` as a "live" interactive chart. It will span a 240-day timeframe and accurately reflect the scaling math (`sum_a / sum_b`) defined in `data/trends_stitcher.py`, displaying the 3 raw periods (A, B, C) and the seamlessly stitched continuous purple curve.

## User Review Required
- **Rendering Approach:** Due to `generative_ui` Content Security Policy (CSP) blocking external CDNs like Chart.js, the live visualization will be implemented using Vanilla JavaScript and an HTML5 `<canvas>` (or inline SVG) alongside the allowlisted Tailwind CSS. 
- **Math Integrity:** In accordance with Constraint #2 (Never fabricate a metric) and Constraint #4 (Single source of truth), the mock data generated for this visualization will strictly apply the `GoogleTrendsStitcher.stitch_intervals` logic (1.80x and 0.57x scaling on the overlap windows) to ensure the visual representation is mathematically honest.

## Open Questions
- None at this time. The requirements clearly dictate an interactive version of the provided static asset, which we will achieve via a custom HTML widget artifact.

## Proposed Changes

### Task Tracker
#### [NEW] [.claude/svi_stitching_visualization_task.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/integrate_svi_stitching_ui/.claude/svi_stitching_visualization_task.md)
Create the project-scoped task tracker to manage execution steps in the repo.

### Backend Data API
#### [MODIFY] api/data_api.py
Add `GET /data/svi-stitching-demo` to return the stitched and raw period data, enforcing the single source of truth and resolving the math on the backend.

### Studio Artifact
#### [NEW] [svi_stitching_visualizer.html](file:///Users/kevinlee/.gemini/antigravity/brain/960bd910-1b15-44d8-843f-81d07b87258c/svi_stitching_visualizer.html)
Create the interactive HTML artifact containing:
- Tailwind CSS via the allowlisted gstatic CDN.
- Vanilla JS logic to generate the 240-day mock dataset with market events (Days 45, 115, 195).
- Canvas/SVG rendering logic to draw the axes, the raw dashed lines (blue, orange, green), the grey overlap rectangles (Days 76-90, 151-165), and the stitched solid purple curve.
- Tooltips or interactive hover states to show the mathematical reconciliation values.

## Verification Plan

### Automated Tests
- Added `GET /data/svi-stitching-demo` to `api/data_api.py` to act as the single source of truth for the visualization data.

### Manual Verification
- Render the `svi_stitching_visualizer.html` artifact in the Studio panel.
- Visually confirm the presence of the 3 raw curves and the unified purple curve.
- Verify the gray overlap windows (Days 76-90 and 151-165) highlight the transitions.
- Ensure the interactive elements (e.g., hover states) function correctly without CSP errors.

## Agent Handoff Notes
- No core pipeline changes are required. This strictly adds an observability/educational visualization artifact to the Studio panel.
- No `docs/architecture/` or `CLAUDE.md` updates are necessary as this does not modify the production system's architecture or constraints.
