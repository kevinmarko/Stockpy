# Paper Broker: trade any FMP symbol, decoupled from the pipeline universe — Implementation Plan

Branch: `claude/paper-broker-fpm-symbols-c803b9`

## Context

The operator asked whether the Paper Broker can trade any symbol available via FMP, noting it
should be able to pull symbols in — by Strategy, Options, or Sector — separately from the main
pipeline's tracked universe (held positions ∪ `WATCHLIST` ∪ `watchlist.txt`).

Research (backend, webapp UI, FMP client) found the backend is already unrestricted:

- `execution/fmp_paper_broker.py` and the `/brokerage/options/order` endpoint
  (`api/pilots_api.py` → `pilots/paper_broker_options_order.py`) accept **any** symbol FMP can
  quote — no watchlist/universe check anywhere in that call chain.
- `webapp/src/api/client.ts`'s `getStrategyOptionsCandidates(symbols?: string[])` /
  `executeStrategyOptions(symbols?: string[], ...)` and the pilots-API endpoints they call
  already accept an explicit `symbols` list — the `WATCHLIST` fallback only fires when `symbols`
  is omitted.
- `api.getDataQuotes(symbols: string[])` already fetches a live quote for any ticker via the
  same unrestricted FMP path.

The gap was entirely in the UI: `PaperBroker.tsx` had no symbol-entry box at all, and the
auto-execute section always called `getStrategyOptionsCandidates()`/`executeStrategyOptions()`
with no arguments, silently inheriting the `WATCHLIST`-only default every time.

There is no FMP symbol-search-by-name or sector/industry screener anywhere in this repo —
`data/fmp_client.py` only wraps per-symbol lookups. Building one is separately-scoped follow-up
work (new, unverified FMP endpoint; unknown tier availability).

**Operator decisions:** (1) ship the "trade any symbol" quick-add UI now, defer the
sector/strategy screener as a documented follow-up; (2) free the "Automated Strategy Options
Execution" auto-scan from its silent `WATCHLIST`-only default via a UI symbol-list input.

This was UI-only — no backend/Python changes were needed, since every API call used already
existed and was already unrestricted.

## What shipped

1. **Quick Trade panel** — `webapp/src/screens/PaperBroker.tsx`: a free-text `SymbolInput` →
   `api.getDataQuotes([symbol])` for a live quote → an inline `OptionsOrderTicket` in
   `assetType="stock"` mode, plus a "View Options Chain" link into the existing
   `/symbol/:ticker/options` route. Fails closed (CONSTRAINT #6): a missing/null-price quote
   shows an honest inline error, never a fabricated `$0` order ticket.
2. **Auto-execute symbol override** — same file: an optional comma-separated symbol-list input
   above the "⚡ Automated Strategy Options Execution" candidates table, threaded into
   `getStrategyOptionsCandidates`/`executeStrategyOptions`'s existing `symbols?: string[]`
   parameter. Blank preserves today's exact `WATCHLIST` default.
3. **Tests** — `webapp/src/screens/PaperBroker.test.tsx`: 3 new tests (quick-trade success,
   quick-trade honest-error, auto-execute symbol-list threading through both API calls).
4. **Docs** — `CLAUDE.md` (auto-mirrored to `AGENTS.md`): addendum to the existing "FMP-based
   paper trading engine" bullet describing the above and explicitly noting the sector/strategy
   screener was scoped out as a follow-up.

## Verification

- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run src/screens/PaperBroker.test.tsx` — 20/20 passed (17 existing + 3 new).
- `npm run dev` (mock mode) + browser check: typed an out-of-universe ticker ("ZZZZ") into Quick
  Trade → live quote fetched, order ticket opened with the real (non-fabricated) spot price;
  typed the mock's dead-lettered symbol ("V") → honest "No live quote available" error, no
  ticket opened; typed a symbol list ("NVDA, TSLA") into the auto-execute scan field → helper
  copy correctly updated to "Scanning 2 symbols you entered (NVDA, TSLA), independent of your
  tracked watchlist." No console errors observed in either flow.
