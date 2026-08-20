# Symbol Search & Sector/Industry Screener (FMP-backed) — Implementation Plan

Branch: `claude/paper-broker-fpm-symbols-c803b9` (continues PR #826 — closes the
"deliberately deferred" screener item that PR's own description documented).

## Context

PR #826 shipped a "Quick Trade — Any Symbol" panel and freed the auto-execute scanner
from its `WATCHLIST`-only default, but explicitly deferred a real sector/industry/
strategy screener because no FMP symbol-search-by-name or screener endpoint existed
anywhere in this codebase, and tier availability was unverified.

That gap is closed here. A connected FMP MCP tool (a real, working FMP account) was
used to live-verify FMP's `search-name`, `search-symbol`, `search-company-screener`,
`available-sectors`, and `available-industries` endpoints — confirmed working with
clean response shapes. This plan wraps those endpoints, exposes them as new read-only
Pilots-data endpoints, and builds a new webapp screen to browse/filter them — with a
one-way handoff into the Quick Trade panel and auto-execute scan input PR #826 already
built.

**Scope decision on "Strategy" fit**: FMP's screener has no concept of "strategy" —
that's this platform's own signal/options-engine logic, and running it live across a
broad screener result set is a much heavier, separate feature. Instead, "Strategy" and
"Options" are satisfied by connecting the screener to the two trading flows PR #826
already built (Quick Trade per row, "Send to Strategy Scan" for a multi-select). No new
quant scoring logic was introduced. A small set of pure client-side filter presets
("Large Cap Tech", "Dividend Income", "Low Vol") give a taste of strategy-flavored
discovery without inventing new math.

## What shipped

### Backend
- `data/fmp_client.py`: 5 new thin wrappers (`search_name`, `search_symbol`,
  `company_screener`, `available_sectors`, `available_industries`), each documenting
  the verification-status caveat (verified via an external FMP MCP connector, not yet
  through this repo's own client/key).
- New standalone module `data/fmp_screener.py` (mirrors `data/fmp_universe.py`'s
  structure; deliberately NOT added to `CompositeProvider`/`FMPProvider`, matching the
  `peers()` precedent — a universe-browse capability has no per-symbol fallback shape).
  Never raises (CONSTRAINT #6), degrades to `[]` on any failure.
- `settings.py`: one new flag, `FMP_SCREENER_ENABLED` (default `True`), covering all
  four wrapped capabilities.
- `api/data_api.py`: 3 new `GET` endpoints (`/data/symbol-search`, `/data/screener`,
  `/data/screener/filters`), following `GET /data/peers/{symbol}`'s exact shape (flag
  check → honest `{"reason": ...}` short-circuit, never a 500).
- `docs/FMP_INTEGRATION.md`: new §9, mirroring §7/§8's template (endpoint list,
  explicit verification-status statement, consumers, flag-off-is-byte-identical proof).
  Settings table/count updated. `docs/settings_liveness.json` regenerated
  (`python3 scripts/settings_liveness.py --write`) to include the new flag.
- `CLAUDE.md` (auto-mirrors to `AGENTS.md`): extended the existing Quick Trade
  addendum to note the screener now exists, closing out the previously-flagged
  deferral.
- Tests: `tests/test_fmp_screener.py` (22 tests), `tests/test_data_api_screener.py`
  (15 tests) — gate/degrade coverage for every new function and endpoint.

### Frontend
- `webapp/src/api/types.ts`/`client.ts`/`mock.ts`: new types (`SymbolSearchResult`,
  `ScreenerFilters`, `ScreenerResult`, `ScreenerFilterOptions`, etc.), 3 new client
  methods under `/data/...` (auto-routes to `DATA_BASE_URL`), and a deterministic
  seeded mock fixture (`SCREENER_UNIVERSE`) with an honest empty-result branch and two
  legitimately-null fields.
- New screen `webapp/src/screens/SymbolScreener.tsx`: free-text search, a
  sector/industry/market-cap/price/beta/dividend filter form, 3 filter presets, a
  results table with per-row "Quick Trade →" and multi-select "Send to Strategy Scan".
- `App.tsx` route, `navigation.tsx` nav entry (`section: "research"`), `Marketplace.tsx`
  Explore tile (mobile reachability), `helpContent.ts` `TAB_HELP` entry.
- `webapp/src/screens/PaperBroker.tsx`: `useSearchParams()` handoff — mount-once
  effects read `?quickTradeSymbol=` (fires the existing `handleQuickTradeSubmit`) and
  `?scanSymbols=` (prefills the existing scan input), matching `Commands.tsx`'s
  `?builder=` param as this codebase's one existing cross-screen pattern.
- Tests: `webapp/src/screens/SymbolScreener.test.tsx` (10 tests), 2 new tests added to
  `PaperBroker.test.tsx` for the URL-param handoff.

## Verification

- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run` — 167 files / 1800 tests, all passed (full webapp suite).
- `pytest tests/test_fmp_client.py tests/test_fmp_screener.py
  tests/test_data_api_screener.py tests/test_data_api_peers.py tests/test_data_api.py
  tests/test_settings.py tests/test_settings_keysets.py tests/test_settings_liveness.py
  tests/test_settings_meta.py tests/test_fmp_universe.py` — 318 passed.
- Manual browser check (mock mode, no console errors): searched "Apple" → real result
  + "Quick Trade →"; searched an unmatched query → honest "No matches"; clicked "Large
  Cap Tech" preset → AAPL/MSFT/NVDA (NODIVCO correctly excluded by market-cap
  threshold); clicked "Quick Trade →" on AAPL → landed on Paper Broker with the symbol
  pre-filled, a real (non-fabricated) quote fetched, and the order ticket open; selected
  AAPL+MSFT and clicked "Send to Strategy Scan" → landed on Paper Broker with the
  auto-execute scan input correctly pre-filled ("Scanning 2 symbols you entered (AAPL,
  MSFT)...").

**Residual gap, disclosed on the PR**: live-verified via an external FMP MCP connector
on a separate account — not through this repo's own `_fmp_get` path or the operator's
own `FMP_API_KEY`/tier. Recommend running one live (non-mock) request against
`GET /data/screener` locally to confirm before relying on it in a live-capital
deployment — this sandboxed environment has no live-market network access to do that
verification itself.
