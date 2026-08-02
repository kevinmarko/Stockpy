# Agentic Trading Tab — AI Agent Handover Notes

## Overview & Context

This document provides complete technical context and handover instructions for any AI coding assistant or operator taking over the **Agentic Trading Tab** feature branch or Pull Request ([PR #554](https://github.com/kevinmarko/Stockpy/pull/554)).

- **Branch**: `agent-tab-execution-queue-discovery`
- **PR Link**: https://github.com/kevinmarko/Stockpy/pull/554
- **Primary Frontend**: Pilots PWA (`webapp/`) — React + Vite + TypeScript.
- **Backend API**: `api/pilots_api.py` (FastAPI on port 8602).

---

## Technical Architecture & Implemented Components

### 1. Backend API (`api/pilots_api.py`)
- **Query-Filtered Execution Queue (`GET /execution-queue`)**:
  - Accepts optional query parameters: `action` (BUY/SELL), `follow_type`, `status_filter` (Blocked/Ready), and `min_conviction` (0.0 to 1.0).
  - Evaluates queue intents dynamically and returns filtered intent objects.
  - Serves `follow_type` on intent outputs (`composite-signal`, `macd-trend`, `trend-following`).
- **Alias Endpoints**:
  - `POST /api/login` (aliased to `connect_brokerage`)
  - `POST /api/logout` (aliased to `disconnect_brokerage`)
  - `GET /api/status` (aliased to `get_agentic_status`)
  - `GET /api/queue` (aliased to `get_execution_queue`)

### 2. Web App API Client & Types (`webapp/src/api/`)
- `ExecutionQueueIntent`: extended with `follow_type?: string`.
- `ExecutionQueueParams`: interface for `action`, `follow_type`, `status_filter`, and `min_conviction`.
- `client.ts`: `getExecutionQueue(params)` serializes parameters into URL search params.
- `mock.ts`: `getExecutionQueue(params)` filters `MOCK_EXECUTION_QUEUE` for offline mock mode.

### 3. Web App UI Components (`webapp/src/`)
- **`AgenticTrading.tsx`**:
  - **Top Action Bar**: Connection status badge (`Robinhood Connected` / `Robinhood Disconnected`), "Refresh Data 🔄" button (`api.refreshBrokerage()`), and Disconnect control.
  - **`RobinhoodAuthModal`**: Modal collecting Username, Password, and **Optional MFA/2FA Code** (submit button is enabled with just Username and Password).
  - **`ScanConfigModal`**: Discovery scan launcher modal with **Sector Filter** (`ALL`, `Technology`, `Financial`, `Healthcare`, `Energy`, `Consumer`, `Industrial`) and price/volume controls.
- **`ExecutionQueueSection.tsx`**:
  - **Collapsible Toggle**: Added `Minimize ▼` / `Expand ▲` button.
  - **Multi-Attribute Filter Control Bar**: Side dropdown, Strategy dropdown, Status dropdown, and Min Conviction range slider (0% to 100%).

---

## Safety Posture & Architectural Invariants

1. **`ADVISORY_ONLY=true` & Execution Mode Gates**:
   - The platform operates in advisory mode by default.
   - Placing real trades always requires explicit human confirmation via Claude Code skills (`skills/robinhood-execution`). Nothing in the web UI bypasses this quarantine.
2. **MFA Form Validation Differences**:
   - `RobinhoodConnectForm.tsx` (used in Onboarding & Settings) requires a 6-digit MFA code for onboarding test compatibility.
   - `RobinhoodAuthModal` (used in Agentic Trading header) keeps the MFA code optional as requested by the operator.

---

## Verification Status

- **Frontend Vitest Suite (`webapp/`)**: **85/85 test files passed** (972 unit tests total).
- **Backend Pytest Suite (`tests/`)**: **368/368 pytest tests passed** (`test_pilots_api.py`, `test_pilots_discovery.py`, `test_brokerage_connect.py`).

---

## Next Steps for Takeover Agent

1. **Review & Merge PR #554**: Once GitHub Actions CI checks pass, merge PR #554 into `main`.
2. **Local Sync**:
   ```bash
   git checkout main
   git fetch origin
   git rebase origin/main
   ```
3. **Verification**: Run `make verify` or `./verify.command` to confirm end-to-end environment health.
