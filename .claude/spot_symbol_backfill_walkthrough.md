# Walkthrough: Spot data download + honest "add to watchlist" flow

## What changed and why

Two of the operator's three original asks land here: "add it as a spot
data download" for an untracked symbol, and closing the "silently swallowed
failure" gap in the one place a symbol is already added to the tracked
universe today — the order ticket's "+ Add to Watchlist" button.

## The fix, in order

1. **`api/data_api.py`** gained `POST /data/backfill/{symbol}`, placed right
   after `GET /data/bars/{symbol}` — deliberately constructs
   `HistoricalStore()` in write mode (no `readonly=True`), unlike every
   other symbol endpoint in the file, since the whole point is to persist.
2. **`webapp/src/api/client.ts`**: added `triggerSymbolBackfill`. Also
   rewrote `watchCandidate` to bypass the shared `http()` helper and parse
   `POST /agentic/watch`'s structured error detail directly — I checked
   `http()`'s error path (`msg = String(body.detail)`) and confirmed a dict
   detail stringifies to the literal text `"[object Object]"`, which would
   have made my "surface the error visibly" fix show a useless message.
   Matched the existing `runForecastBackfill` precedent for structured
   403/409 bodies rather than inventing a new pattern.
3. **`OptionsOrderTicket.tsx`**: `handleAddToWatchlist` now sets
   `watchlistError` (rendered) instead of `console.error`, and on success
   fires `triggerSymbolBackfill` non-blockingly, rendering a secondary note.
   `executeOrder`'s success branch now checks `universeCache.ts`'s tracked
   universe before deciding whether to auto-clear after 2s (tracked, the
   original behavior) or show a persistent "Add / Not now" prompt
   (untracked) — an explicit action, not a silent add.

## What I verified before calling this done

- Traced `http()`'s error-message construction in `client.ts` line by line
  before assuming `watchCandidate`'s existing error path would work for the
  visible-error requirement — found the `"[object Object]"` bug this way,
  not by guessing.
- Checked every existing consumer of `watchCandidate` (`AgenticTrading.tsx`,
  `PaperBroker.tsx`, `OptionsOrderTicket.tsx`, three test files) before
  rewriting its implementation, to confirm no test asserts on internals that
  would break — `AgenticTrading.test.tsx`'s error test mocks `ApiError`
  directly via `vi.spyOn`, so it never exercises the real `http()`/fetch
  path and was unaffected.
- The `OptionsOrderTicket.test.tsx` mock's `vi.mock("../../api/client")`
  factory didn't include `getUniverse` — `universeCache.ts` (imported by my
  new fill-time-prompt code) calls it through the same mocked module. Ran
  the existing "submits paper order successfully" test first to confirm it
  would have broken without adding `getUniverse`/`triggerSymbolBackfill` to
  the mock and a `beforeEach` default, then added both properly rather than
  discovering the gap via a flaky CI failure later.
- Ran `npm run --prefix webapp typecheck` (clean) and the FULL vitest suite
  (1854 tests, not just the touched files) to catch any indirect regression
  from the `client.ts`/`mock.ts` changes, since both are imported broadly.
- Live-verified against the mock backend in an actual browser (not just
  unit tests): typed an untracked mock symbol (XOM, present in
  `SCREENER_UNIVERSE` but not `SYMBOL_UNIVERSE`), clicked "+ Add to
  Watchlist", and confirmed both "✓ Added to Watchlist" and "Backfilled 504
  bars of price history." rendered together, matching the designed flow.
- Ran a Python `require_write_token`-fail-closed test and an
  unknown-symbol-dead-letter test for the new endpoint specifically (not
  just a happy-path test) since this is a write endpoint touching local
  storage — the two properties CONSTRAINT #4/#6 and this repo's auth
  conventions require most.

## Disclosed, not hidden

- The fill-time "not tracked" prompt is checked against `GET /universe`
  (the pipeline-snapshot universe via `universeCache.ts`) — the same
  definition of "tracked" the rest of the webapp already uses for this
  component. It does not (and cannot, client-side) know about Phase 0's
  daemon-vs-main.py universe distinction; it answers "will the platform's
  own `/symbols/{ticker}` detail page currently show anything for this
  symbol," which is the practically meaningful question for an operator
  deciding whether to click Add.
- I did not attempt to reproduce the visible-error path live in the browser
  (would require forcing a `409`/`422` from the mock, which the mock's
  `watchCandidate` only throws on a genuinely malformed ticker) — covered
  instead by a dedicated unit test
  (`surfaces a watchlist-add failure visibly instead of console-only`)
  which exercises the real component code path with a mocked rejected
  promise, a more deterministic check than trying to force a live failure
  through the UI.
