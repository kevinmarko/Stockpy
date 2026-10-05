# WebSocket reconnect storm — walkthrough (branch `fix-webapp-ws-reconnect-storm`)

## Symptom (2026-10-02)
`~/.stockpy_local/logs/investyo.log` filled with
`api.ws_api — ws_training_status_endpoint: rejected unauthenticated connection`
(~20 lines per ~5 s from one tab; 124 total that day, bursts at 09:42, 15:54, 16:22, 12:04).

## Root causes
1. **Server closed before `accept()`.** Starlette turns that into an HTTP 403 on the
   handshake; a browser reports it only as close code 1006, the same as "server down".
   The client could never tell a rejection from an outage.
2. **Neither hook stopped on an auth failure.** `useTrainingStatus` and `useLiveTick`
   reconnected on every `onclose`.
3. **Mock mode opened real sockets** (`VITE_USE_MOCK=true` still hit :8601/:8603).
4. **The token mismatch is real, but only in a stale build.** The live dev app
   (`webapp/.env.local`) sends the token the server expects (verified: sockets open, data
   flows). `webapp/.env.production.local` (written Aug 10) carries a *different*
   `VITE_API_TOKEN` than the current `STATE_API_TOKEN`; a tab served from a bundle built
   with it is rejected on every attempt. Not changed here (local, gitignored, secret):
   delete that file and re-run `scripts/build_webapp_prod.sh` to regenerate it.

## Changes
- `api/ws_api.py` — `_reject_ws()` accepts then closes with 4003 so the code reaches the
  browser (nothing is sent). Used by `/ws/ticks/{symbol}` and `/ws/training/status`.
  `/ws/chat/live` already handles 4003 client-side and is unchanged.
- `webapp/src/hooks/wsReconnect.ts` — shared policy: 1 s → 30 s doubling backoff; auth
  codes (4001/4003/1008) are terminal.
- `useTrainingStatus.ts` / `useLiveTick.ts` — use it; open no socket when `USE_MOCK`.
  `useLiveTick` reports `source: 'unauthorized'` + an error after a rejection, and
  `source: 'mock'` with no price in mock mode (never a made-up price). Backoff resets
  only when a socket opens, and per symbol.
- `webapp/src/api/client.ts` / `mock.ts` untouched: no API surface changed, parity holds.

## Verification
- `pytest tests/test_ws_api.py tests/test_gemini_live_chat.py tests/test_data_api.py tests/test_auth.py`:
  130 passed (new: both endpoints accept-then-4003 for wrong/missing token, no quote ever
  sent to a rejected client, valid token still accepted).
- `npm run typecheck` clean; `vitest run` 1744 passed (new `useTrainingStatus.test.ts`;
  `useLiveTick.test.ts` extended: backoff schedule to cap, reset on open, no retry on
  4003/4001/1008, no socket in mock mode). `StrategyMatrix.test.tsx` flaked once under
  full-suite load and passed on re-run and in isolation; it does not touch these hooks.
- Browser, real backend: (a) `:5183`, real token: both sockets open, no hook console
  errors; (b) `:5185` pointed at a token-protected Data API (:8613 running this branch)
  with a deliberately wrong token: one real attempt in 15 s, closed 4003, no retry; the
  server logged 2 lines (the StrictMode double mount) instead of ~3/s; (c) mock build:
  0 sockets, no console errors.
- Not verified: the already-running daemon still runs the old `ws_api.py` until it is
  restarted (`launchctl kickstart -k`); the webapp half works against either.

## Docs
`docs/architecture/webapp-and-gui.md` — reconnect-policy paragraph.
