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

## Code Review Follow-up (PR #745 review, applied post-merge-request)

Six findings from `/code-review 745` were applied directly to this branch, minimal-edit style:

1. **CommandPaletteModal.tsx / Modal.tsx — de-duplicated the focus-blur fix.** CommandPaletteModal's own `document.activeElement === inputRef.current` blur was redundant with (and on desktop, made unreachable by) Modal's own blur, since CommandPaletteModal's input always renders inside Modal's `sheetRef` subtree and React cleans up child effects before parent effects. Fixed at the root instead of removing the duplicate outright: `sheetRef` is now also attached to the mobile `Drawer.Content` (vaul forwards its ref), so Modal's single blur-on-unmount effect covers both branches; CommandPaletteModal's local blur block was removed.
2. **useLiveTick.ts — removed the unreachable `aliveRef` check inside `onclose`.** Both teardown sites already null `onclose` before calling `.close()`, so the check could never actually fire.
3. **useLiveTick.ts — kept but explicitly commented `connect()`'s "clean up any existing connection" block as defensive-only,** since `wsRef.current` is always already `null` at both of connect()'s real call sites today.
4. **useLiveTick.ts — replaced the whole `aliveRef` pattern with handler-nulling.** All 4 WS handlers (`onopen`/`onmessage`/`onerror`/`onclose`) are now nulled at both teardown sites (mount-effect cleanup and connect()'s own cleanup block), the same way `onclose` alone already was — a WebSocket only ever invokes whichever handler is *currently assigned* at dispatch time, so this is a strictly simpler, single choke point than checking a shared `aliveRef` in 6 separate places.
5. **LogStream.tsx — the sliding-window cap now does one array copy per message instead of two** once the buffer is at its 2000-line ceiling (steady state for a busy job stream): `prev.slice(prev.length - MAX_LOG_LINES + 1)` + `push` instead of spread-then-slice.
6. **Missing browser-check evidence — closed for real, not just noted.** Ran `npx vite` against this branch's actual code and drove it with the in-app Browser tool: app loads with zero console errors; Cmd+K opens the Command Palette with focus landing correctly in the input; Escape closes it cleanly with zero console errors (exercising the Modal/CommandPaletteModal blur fix from #1); the Console screen renders `LogStream` and a real job run (`mock-job-1`, preflight) completes and displays correctly. See the Verification Results section below for the concrete evidence.

---

## Verification Results

1. **TypeScript Typecheck**:
   ```bash
   npm run --prefix webapp typecheck
   # Output: Clean (0 errors)
   ```

2. **Unit & Integration Test Suite** (re-run after the code-review fixes above):
   ```bash
   npx vitest run
   # Output: 137 of 138 test files passed outright; the 1 "failure"
   # (AdversarialRobustness.test.tsx's 10,000-DOM-node stress test) is a
   # pre-existing 20s-timeout flake under full-suite parallel load, unrelated
   # to any file this PR touches -- confirmed by re-running it in isolation:
   # 13/13 passed in 15.3s. Net: 1554/1554 tests pass.
   ```

3. **Memlab Profiling**:
   - Sweeps verified via Chromium V8 heap snapshots (`s1` baseline through `s11` final).

4. **`npm run dev` + browser check** (the step CLAUDE.md's Agent Workflow section requires for UI-visible changes, added during the code-review follow-up since it was missing from the original pass):
   - Launched `vite` against this branch and opened it in the Browser tool.
   - Console on initial load: **zero errors/warnings** (only the expected Vite HMR/React DevTools info lines).
   - Opened the Command Palette (Cmd+K): input received focus correctly, palette rendered command suggestions and quick-jump tickers.
   - Closed it (Escape): closed cleanly, focus restored, **zero console errors** — this is the exact path the Modal/CommandPaletteModal blur-on-unmount fix runs on.
   - Navigated to the Console screen, launched the "Preflight Check" quick launcher: job ran to `success`, `LogStream`'s panel rendered without error (mock mode shows its documented "Log streaming is only available in live mode" message rather than a live SSE stream, which is expected — `USE_MOCK` gates the real `EventSource` path; the sliding-window cap logic itself is covered by the automated `LogStream.test.tsx` unit test instead).
