# RLHF Calibration Plan Data App

This document serves as the master plan for implementing the RLHF Calibration Plan Data App.

## 1. Overview
The RLHF (Reinforcement Learning from Human Feedback) Calibration Data App will be built as a new dashboard within the existing `webapp/` Pilots PWA. This ensures compliance with the project's strategy to use a unified React + Vite frontend for all operator-facing tooling.

## 2. Technical Stack
- **Frontend**: React + Vite (hosted in `webapp/`)
- **Styling**: Vanilla CSS / Tailwind (matching existing zinc palette and minimal chrome style)
- **Visualizations**: ECharts / Recharts (depending on existing dependencies in `webapp/package.json`)
- **Backend API**: Python FastAPI (`api/pilots_api.py` or new dedicated router)

## 3. Implementation Steps

### Phase 1: Foundation
1. **Routing**: Add a new route `/rlhf-calibration` in the main React application router.
2. **Layout**: Create the base page component `RLHFCalibration.tsx` using the standard dashboard layout.

### Phase 2: UI Components
1. **KPI Metrics Panel**: A top-level panel showing key calibration metrics (e.g., Reward Model Score, Policy KL Divergence, Human Alignment Score).
2. **Calibration Chart**: A time-series or scatter plot component (`CalibrationChart.tsx`) to visualize calibration drift over time.
3. **Data Grid**: A tabular view of recent feedback samples and their corresponding model adjustments.

### Phase 3: Backend Integration
1. **API Endpoints**: Expose data from the backend via FastAPI endpoints (e.g., `GET /api/rlhf/metrics`, `GET /api/rlhf/history`).
2. **Data Fetching Hooks**: Implement React hooks (`useRLHFMetrics`, `useRLHFHistory`) to fetch and cache data on the client side.

### Phase 4: Optional AI Chat Integration
1. **Chat Panel**: If requested, integrate a Gemini-powered chat interface allowing natural language queries against the RLHF dataset.

## 4. Unresolved Dependencies (Action Items)
- **Data Source**: Determine which SQLite tables or external APIs hold the raw RLHF data.
- **Specific Visualizations**: Finalize the exact metrics to be displayed on the charts.
