# Known issue / audit (2026-08-14): Webapp Memory Leak Investigation & Preventative Hardening

**Status: Resolved & Verified** (Memlab V8 heap profiling + automated Vitest suite).

## What was investigated

An automated end-to-end memory leak audit was conducted across the **Stockpy Pilots PWA** (`webapp/`) using **Memlab** and multi-stage Chromium V8 heap snapshots (`--expose-gc`).

The investigation evaluated:
1. **Multi-screen navigation transitions**: Repeated sweeps across 10 core screens (`/observability`, `/options`, `/agentic`, `/strategy-health`, `/forecast`, `/signals`, `/models`, `/pipeline`, `/trading`, `/portfolio`).
2. **High-frequency Modal & Drawer lifecycles**: Repeated open/close interactions with `CommandPaletteModal`, `AIChatInterface`, and rapid ticker switching across `/symbol/AAPL`, `/symbol/MSFT`, `/symbol/NVDA`, `/symbol/TSLA`, `/symbol/SPY`.

## Findings & Root Cause Analysis

### 1. General Route Traversal (No Detached DOM Leaks)
- General routing across all 10 screens produced **0 detached DOM or React Fiber leaks**.
- Measured heap growth from cold start baseline (38.6MB) to multi-screen state (44.8MB) was driven by normal V8 JIT compilation (`InstructionStream` +2.88MB, `TrustedByteArray` +673KB), DevTools timeline entries (`PerformanceMeasure` +1.95MB in dev mode), and network bundle cache.

### 2. High-Frequency Modal Dismissal Focus Retention
- Memlab identified that during repeated `CommandPaletteModal` open/close interactions, the input element could be retained via the V8 scope / `document.activeElement` if focus was not explicitly released before the modal container unmounted.
- **Fix**: Updated [`CommandPaletteModal.tsx`](../../webapp/src/components/CommandPaletteModal.tsx) to cancel the focus timer and blur the active input upon unmount, and updated [`Modal.tsx`](../../webapp/src/components/Modal.tsx) to ensure any active element within the unmounting sheet is blurred prior to focus restoration.

### 3. WebSocket Reconnect Race Condition in `useLiveTick.ts`
- [`useLiveTick.ts`](../../webapp/src/hooks/useLiveTick.ts) lacked an `aliveRef` check inside its `connect()` function and `setTimeout` reconnect callback. If a component unmounted or changed symbol while a reconnect backoff was pending, a dangling WebSocket could establish connection in the background.
- **Fix**: Added an `aliveRef` mount-guard pattern matching [`useTrainingStatus.ts`](../../webapp/src/hooks/useTrainingStatus.ts) and authored [`useLiveTick.test.ts`](../../webapp/src/hooks/useLiveTick.test.ts).

### 4. Unbounded SSE Log Stream Buffer in `LogStream.tsx`
- `LogStream.tsx` appended every incoming line to state without an upper bound limit (`setLogs(prev => [...prev, event.data])`), creating a potential memory leak for long-running jobs emitting tens of thousands of log lines.
- **Fix**: Added `MAX_LOG_LINES = 2000` sliding window buffer and unit test in [`LogStream.test.tsx`](../../webapp/src/components/LogStream.test.tsx).

## Verification

- **Memlab Verification Sweep**: Re-run of the modal & drawer scenario verified stable memory (`41.0MB -> 41.4MB -> 41.6MB -> 41.8MB`).
- **TypeScript**: `npm run --prefix webapp typecheck` clean (0 errors).
- **Unit Tests**: Full Vitest test suite passing (138 test files, 1,554 tests).
