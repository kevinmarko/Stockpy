# Implementation Plan: Spot data download + honest "add to watchlist" flow

Phase 1 of a 4-phase body of work scoping "Symbol Screener → universe
integration" (Phase 0, the daemon universe-divergence fix, shipped
separately as [PR #885](https://github.com/kevinmarko/Stockpy/pull/885)).

## Context

Two of the three things the operator asked for map to this phase:
"if [a symbol is] not [in our database], we can add its information as a
spot data download" and the underlying "don't let symbols fall out"
concern, applied here to the existing-but-silent "+ Add to Watchlist"
button on the order ticket.

Research found "spot data download" is a thin wrapper around an existing
capability, not new capability: `HistoricalStore.get_bars()` already does a
full backfill-and-persist on a miss, but only in write mode — every
REST-facing symbol endpoint in `api/data_api.py` uses `readonly=True`,
whose `_get_bars_db_path` short-circuit deliberately live-fetches without
persisting. The write-mode call already exists as an MCP tool
(`investyo_mcp_server.py::trigger_data_engine`) but not as a REST endpoint
the webapp can call.

Separately, `OptionsOrderTicket.tsx`'s "+ Add to Watchlist" button
(`POST /agentic/watch`) was already wired up but silently swallowed
failures (`console.error` only) and never triggered a backfill on success.

## Approach

1. `POST /data/backfill/{symbol}` in `api/data_api.py`, `require_write_token`-gated
   (matching `PUT /data/universe`'s fail-closed posture) — ports
   `trigger_data_engine`'s body onto a write-mode `HistoricalStore()`.
   Never a fabricated success: an unfetchable symbol returns
   `status: "no_data"` with 200, not a 500.
2. `webapp/src/api/client.ts`: new `triggerSymbolBackfill(symbol)`. Also
   fixed `watchCandidate`'s error handling — the shared `http()` helper's
   generic `String(body.detail)` stringifies `POST /agentic/watch`'s
   structured `{"error": tag, "message": ...}` detail as `"[object Object]"`,
   which would have defeated the point of surfacing it. Bypasses `http()`
   for this one call, matching the existing `runForecastBackfill` precedent
   for structured-detail endpoints.
3. `OptionsOrderTicket.tsx`: `handleAddToWatchlist` now sets a visible
   `watchlistError` on failure instead of `console.error`-only, and fires
   `triggerSymbolBackfill` on success (non-blocking secondary note, never
   rolling back the watchlist-add confirmation). A successful fill on an
   untracked symbol (checked via `universeCache.ts`) shows an inline
   "Add / Not now" prompt instead of auto-clearing after 2 seconds.

## Documentation

- `docs/architecture/webapp-and-gui.md`'s `api/data_api.py` entry extended
  with the new endpoint.
- `docs/architecture/data-layer.md`'s `HistoricalStore` entry extended with
  the write-mode-vs-readonly distinction this endpoint depends on.
- `CLAUDE.md` changelog bullet (auto-mirrored to `AGENTS.md`).

## Verification

- New backend tests in `tests/test_data_api.py` (4 tests): fail-closed auth,
  happy path, unknown-symbol dead-letter, store-exception dead-letter.
- New/extended frontend tests in `OptionsOrderTicket.test.tsx` (5 new
  tests): visible watchlist-error, backfill-trigger-on-success, the
  not-tracked prompt appearing/not-appearing, and the Add-from-prompt flow.
- `npm run --prefix webapp typecheck` clean.
- Full webapp vitest suite (1854 tests) and the touched Python test files
  re-run — no regressions.
- Live browser check against the mock backend (`npm run dev`,
  `VITE_USE_MOCK` default): Quick Trade on an untracked mock symbol (XOM) →
  "+ Add to Watchlist" → "✓ Added to Watchlist" + "Backfilled 504 bars of
  price history." both render.
