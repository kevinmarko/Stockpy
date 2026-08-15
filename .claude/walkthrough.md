# Memory Leak Investigation & Hardening Walkthrough

## Summary of Completed Work

1. **Automated End-to-End Heap Sweep (Action #1 - Memlab Baseline)**:
   - Profiled the **Stockpy Pilots PWA** (`http://localhost:5173`) across 10 core application screens (`/observability`, `/options`, `/agentic`, `/strategy-health`, `/forecast`, `/signals`, `/models`, `/pipeline`, `/trading`, `/portfolio`) over 3 repeat cycles.
   - Identified that memory growth between cold start and multi-screen navigation was dominated by V8 JIT compilation (`InstructionStream` +2.88MB), DevTools timeline entries (`PerformanceMeasure` +1.95MB), and bytecode arrays (+673KB), with **0 detached DOM or unmounted React component leaks** across general routing.

2. **Preventative Memory Hardening**:
   - **`useLiveTick.ts`**: Implemented an `aliveRef` mount-lifecycle guard on `connect()` and the exponential backoff `setTimeout` callback to guarantee no dangling WebSocket connections or state updates execute after component unmount.
   - **`useLiveTick.test.ts`**: Authored 6 unit tests covering initial connection state, message dispatch, error handling, backoff retries, and unmount cancellation.
   - **`LogStream.tsx`**: Added `export const MAX_LOG_LINES = 2000;` and a sliding buffer on `setLogs` to prevent memory blowup during massive SSE log streams.
   - **`LogStream.test.tsx`**: Added unit test asserting log buffer truncation at `MAX_LOG_LINES`.
   - **`Modal.tsx` & `CommandPaletteModal.tsx`**: Added active element blurring and focus timer cancellation to cleanly release detached focus nodes from the V8 context scope on modal dismissal.

3. **Deep Modal & Screen Profiling (Action #2)**:
   - Authored and executed an intensive Memlab scenario (`memlab-modal-sweep.js`) testing rapid ticker transitions (`AAPL`, `MSFT`, `NVDA`, `TSLA`, `SPY`), AI Chat drawer open/close, and Command Palette hotkey trigger/dismissal across 4 repeat loops.
   - Demonstrated stable memory footprint (`41.0MB -> 41.4MB -> 41.6MB -> 41.8MB`) across repeated modal lifecycles.

---

## Changes Made

| File | Type | Changes |
|---|---|---|
| [`webapp/src/hooks/useLiveTick.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/hooks/useLiveTick.ts) | Modified | Added `aliveRef` mount guard to prevent dangling WebSocket reconnections. |
| [`webapp/src/hooks/useLiveTick.test.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/hooks/useLiveTick.test.ts) | New | 6 unit tests for WebSocket lifecycle, price dispatch, error handling, and unmount cleanup. |
| [`webapp/src/components/LogStream.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/components/LogStream.tsx) | Modified | Added `MAX_LOG_LINES = 2000` sliding window buffer to prevent unbounded array growth. |
| [`webapp/src/components/LogStream.test.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/components/LogStream.test.tsx) | Modified | Added unit test verifying log array truncation beyond `MAX_LOG_LINES`. |
| [`webapp/src/components/CommandPaletteModal.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/components/CommandPaletteModal.tsx) | Modified | Added `focusTimer` cleanup and explicit `inputRef.current.blur()` on close. |
| [`webapp/src/components/Modal.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_memory_leak_issue/webapp/src/components/Modal.tsx) | Modified | Added `activeElement` blur check for elements within unmounting dialog sheets. |

---

## Verification Results

1. **TypeScript Typecheck**:
   ```bash
   npm run --prefix webapp typecheck
   # Output: Clean (0 errors)
   ```

2. **Unit & Integration Test Suite**:
   ```bash
   cd webapp && npm run test
   # Output: 138 test files passed (1,554 tests total)
   ```

3. **Memlab Profiling**:
   - Sweeps verified via Chromium V8 heap snapshots (`s1` baseline through `s11` final).
