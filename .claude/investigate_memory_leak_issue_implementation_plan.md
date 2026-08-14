# Implementation Plan: Memory Leak Hardening & Deep Modal/Screen Profiling

This plan addresses the two user-requested actions:
1. **Preventative Hardening**: Eliminate dangling WebSocket reconnect races in `useLiveTick.ts` and apply a bounded circular buffer to `LogStream.tsx`.
2. **Deep Single-Screen / Modal Memory Profiling**: Execute an intensive Memlab & V8 heap leak audit targeting high-interaction components (Drawer / Modals / Real-time LiveTick).

## Proposed Changes

### Webapp Hooks & Components

#### [MODIFY] [`webapp/src/hooks/useLiveTick.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/hooks/useLiveTick.ts)
- Add `aliveRef = useRef(true)` to track component mount lifecycle.
- Guard `connect()` and the reconnect `setTimeout` callback with `if (!aliveRef.current) return;`.
- Ensure clean teardown on unmount or symbol changes.

#### [NEW] [`webapp/src/hooks/useLiveTick.test.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/hooks/useLiveTick.test.ts)
- Unit tests verifying WebSocket creation, price dispatch, error handling, backoff retry, and unmount cancellation (ensuring no reconnects occur after unmount).

#### [MODIFY] [`webapp/src/components/LogStream.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/components/LogStream.tsx)
- Define `export const MAX_LOG_LINES = 2000;`.
- Bound `setLogs` state updates using a sliding window: `(prev) => (prev.length >= MAX_LOG_LINES ? [...prev.slice(prev.length - MAX_LOG_LINES + 1), event.data] : [...prev, event.data])`.

#### [MODIFY] [`webapp/src/components/LogStream.test.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/components/LogStream.test.tsx)
- Add test verifying that log stream size is strictly capped at `MAX_LOG_LINES`.

---

### Deep Interaction & Modal Profiling (Action #2)

We will execute an automated Memlab scenario to stress-test high-interaction modals and screen components:
1. **Modals & Drawers**:
   - `TickerDrawer` (opening and closing ticker inspection drawers for `AAPL`, `MSFT`, `NVDA`, `TSLA` repeatedly).
   - `AIChatInterface` (opening, streaming input, and closing).
   - `CommandPaletteModal` (opening and closing with Cmd+K).
2. **Symbol Detail & Live Feeds**:
   - Rapid switching across `/symbol/AAPL`, `/symbol/MSFT`, `/symbol/SPY`, `/symbol/QQQ` with active live ticks.
3. Compare V8 heap snapshots before, during, and after 10 full interaction cycles.

---

## Verification Plan

### Automated Tests
- `npm run --prefix webapp test` (running Vitest for `useLiveTick.test.ts` and `LogStream.test.tsx`).
- `npm run --prefix webapp typecheck` (ensuring TypeScript parity).

### Memlab Modal & Screen Leak Profiling
- Run Memlab with `--chromium-binary "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` on the modal & drawer scenario.
- Parse heapsnapshots with `compare_snapshots.js` and verify zero detached DOM/Fiber leaks.
