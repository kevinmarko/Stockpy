# Implementation Plan: Phase 3 — Pilots PWA, WebSocket Streaming & Frontend Parity

## Goal
Verify and guarantee high-performance real-time streaming, mock/live API parity, and frontend UI resilience across the Pilots PWA (`webapp/`).

## Key Focus Areas
1. **WebSocket Real-Time Streaming & Exponential Reconnection**:
   - `useLiveTick.ts`: Live tick WebSocket subscription with exponential backoff (`Math.min(retryDelay * 2, 30000)`), event handler cleanup on teardown, and fallback to polling.
   - `useGeminiLive.ts` & `useTrainingStatus.ts`: Resilient audio and job-tracking WebSocket lifecycles.
2. **Order Execution Dialog & Queue Views**:
   - `ExecutionQueueSection.tsx`, `ActiveTraderLadder.tsx`, `MultiBrokerGatewayView.tsx`: Live vs paper execution mode safety checks and read-only gating.
3. **Mock / Live API Parity**:
   - Full parity check across `webapp/src/api/client.ts` and `webapp/src/api/mock.ts`.

## Verification Plan
- `vitest run` across all 164 webapp test files (1746 tests).
- TypeScript compilation check (`tsc --noEmit`).
- Bandit SAST scan.
