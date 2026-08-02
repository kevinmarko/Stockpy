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
- **Query-Filtered Execution Queue (`GET /execution-queue`, alias `GET /api/queue`)**:
  - Accepts optional query parameters: `action` (BUY/SELL), `follow_type`, `status_filter` (Blocked/Ready), and `min_conviction` (0.0 to 1.0).
  - Evaluates queue intents dynamically and returns filtered intent objects. `n_intents`/`n_placeable` reflect the FILTERED result set, matching what `intents` actually contains — not the raw snapshot totals.
  - Serves `follow_type` on intent outputs — the REAL per-intent attribution derived from `QueuedIntent.strategy` (`execution/queue_builder.py`'s `"strategy"` key: `"advisory"` for the base advisory engine, `"composed"` when netted across more than one follow, or a real followed Pilot's `pilot_id` parsed off the `"Follow:<pilot_id>"` label). This is **not** guessed from `rationale` free text — an earlier draft of this endpoint did keyword-sniff `rationale` for made-up categories (`composite-signal`/`macd-trend`/`trend-following`), which is a CONSTRAINT #4 fabrication violation and was corrected before merge. `gui/robinhood_execution_panel.py`'s `QueuedIntent` dataclass gained a `strategy: str = ""` field to carry this through (it previously dropped the field entirely when parsing the queue JSON).
  - Also returns `available_follow_types`: every distinct real attribution value present in the UNFILTERED queue, so the webapp can build a correct filter control without hardcoding pilot names (which vary per operator).
- **No `/api/login` / `/api/logout` / `/api/status` aliases exist.** An earlier draft of this PR's description and this doc both claimed these were added; they were never implemented, nothing in the webapp calls them, and they were removed from this doc rather than built, since nothing needs them (`RobinhoodAuthModal`/`RobinhoodConnectForm` already call `POST /brokerage/connect` / `POST /brokerage/disconnect`, and the header status pill calls `GET /brokerage/status`).

### 2. Web App API Client & Types (`webapp/src/api/`)
- `ExecutionQueueIntent`: extended with `follow_type?: string` (the real attribution value above).
- `ExecutionQueue`: extended with `available_follow_types?: string[]`.
- `ExecutionQueueParams`: interface for `action`, `follow_type`, `status_filter`, and `min_conviction`.
- `client.ts`: `getExecutionQueue(params)` serializes parameters into URL search params.
- `mock.ts`: `getExecutionQueue(params)` filters `MOCK_EXECUTION_QUEUE` for offline mock mode, and computes `available_follow_types`/`n_intents`/`n_placeable` from the filtered set the same way the live backend does (this was a mock/live parity bug in the original draft — the live endpoint used to always return the raw snapshot's totals regardless of the applied filter).

### 3. Web App UI Components (`webapp/src/`)
- **`AgenticTrading.tsx`**:
  - **Top Action Bar**: Connection status badge (`Robinhood Connected` / `Robinhood Disconnected`), "Refresh Data 🔄" button (`api.refreshBrokerage()`), and Disconnect control.
  - **`RobinhoodAuthModal`**: Modal collecting Username, Password, and a **required** 6-digit Authenticator app code — see "MFA is required, not optional" below.
  - **`ScanConfigModal`**: Discovery scan launcher modal with **Sector Filter** (`ALL`, `Technology`, `Financial`, `Healthcare`, `Energy`, `Consumer`, `Industrial`) and price/volume controls. `filters` is stored verbatim server-side (`ScanConfigRequest.filters: Dict[str, Any]`) — this API has no knowledge of the Robinhood scanner's real filter schema, so `sector` passes through opaquely to the `agentic-discovery` skill exactly like `min_price`/`min_volume` already did.
- **`ExecutionQueueSection.tsx`** (shared by Commands and Agentic Trading):
  - **Collapsible Toggle**: `Minimize ▼` / `Expand ▲` button; filters stay applied while minimized.
  - **Multi-Attribute Filter Control Bar**: Side dropdown, Strategy dropdown (options sourced live from `available_follow_types`, never hardcoded), Status dropdown, and Min Conviction range slider (0% to 100%). Every filter control now has a properly associated `<label htmlFor>` (an accessibility gap in the original draft).
  - The empty state distinguishes "no queue at all" (`data.reason`) from "filters matched nothing" from "the queue is genuinely empty with no filters applied" — the original draft always showed the filtered-to-nothing copy even with no filters active.

---

## Safety Posture & Architectural Invariants

1. **`ADVISORY_ONLY=true` & Execution Mode Gates**:
   - The platform operates in advisory mode by default.
   - Placing real trades always requires explicit human confirmation via Claude Code skills (`skills/robinhood-execution`). Nothing in the web UI bypasses this quarantine.
2. **MFA is required, not optional — for both Robinhood connect forms.**
   - `POST /brokerage/connect` (`api/pilots_api.py`) always verifies via `data/robinhood_portfolio.py::verify_credentials(..., allow_interactive=False)`. That function's own docstring is explicit: over HTTP it "never falls through to interactive MFA prompting: a headless HTTP request must not block on stdin", so **a missing or empty `mfa_code` is unconditionally treated as a verification failure** — this is a deliberate, load-bearing safety invariant (a hung request thread is worse than a slightly less convenient form), not an oversight to relax.
   - An earlier draft of this PR made `RobinhoodAuthModal`'s MFA field optional ("as requested by the operator") while leaving the backend's hard requirement untouched. That combination meant the form's happy path — submit with just username + password — was **guaranteed to 401 every single time**, for every account, not just ones without 2FA. It was never actually testable end-to-end because the Vitest coverage mocked `api.connectBrokerage` directly, so it never exercised the real backend contract.
   - Fixed by making `RobinhoodAuthModal` require a 6-digit code before the submit button enables, mirroring `RobinhoodConnectForm.tsx`'s existing, correct behavior (both forms now share the same contract). If a genuinely-optional-MFA flow is wanted later, that requires a backend change (accepting the blocking-stdin risk for some other reason, or a different verification mechanism) — flag it to the operator rather than re-introducing a UI promise the API can't keep.

---

## Verification Status

- **Frontend Vitest Suite (`webapp/`)**: 86/86 test files, 977 unit tests passed (added `ExecutionQueueSection.test.tsx` covering the filter bar, the dynamic Strategy options, and the empty-state copy split).
- **`tsc --noEmit`**: clean. The original draft did not actually type-check (`Input` doesn't accept a `required` prop — three call sites in `RobinhoodAuthModal` passed one anyway); the `webapp_typecheck.sh` PostToolUse hook only runs on an edit, so a draft that was never edited again after the failure was introduced wouldn't have been caught by it. `npm run typecheck` was not part of the original verification claim in this doc — add it to any future pre-merge checklist for this file.
- **Backend Pytest Suite (`tests/`)**: full suite passing, plus new coverage in `tests/test_pilots_execution_queue.py` for the query-param filters, `available_follow_types`, the real (non-guessed) `follow_type` attribution, and the `/api/queue` alias — none of this existed in the original draft despite its "368/368 passed" claim (that number reflected zero new tests for the new filtering logic, not verification of it).

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
