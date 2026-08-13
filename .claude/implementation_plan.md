# Implementation Plan: Options Tab (Phased)

This plan builds out a fully interactive Options Chain Explorer for the Pilots PWA in **four phases**, each independently shippable and verifiable. Every phase has its own PR, typecheck gate, and verification criteria before proceeding to the next.

## Resolved Requirements

Based on user feedback, the following design decisions are finalized:
1. **API Source**: `yfinance` for the chain (strikes, bid/ask, IV, expirations). FMP quote endpoint for the underlying spot price feeding Black-Scholes Greeks. Structured as a tier/injection point so we can swap in a real options-chain source later.
2. **Execution**: Live / Paper toggle in the UI. Paper by default. Live mode routes to `POST /brokerage/...`.
3. **Strategy Builder**: Auto-selects optimal strikes based on delta targets (e.g., 16-delta strangles), not just a chain filter.
4. **Entry Point**: Dedicated screen via "Trade Options" from `SymbolDetail.tsx`, not a replacement for `OptionsMatrix.tsx`.

---

## Phase 1 — Backend API + Data Layer

> **Goal**: A working, tested `GET /data/options/chain/{symbol}` endpoint with the tiered provider architecture. No frontend changes.

### Files

#### [MODIFY] [`market_data.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/data/market_data.py)
- ✅ Already done: `OptionsDataProvider` ABC, `YFinanceOptionsProvider`, `CompositeOptionsProvider` (chain from yfinance, spot from FMP).
- **Remaining**: Verify the injection point works end-to-end against a real yfinance call (manual, network-gated test).

#### [MODIFY] [`technical_options_engine.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/technical_options_engine.py)
- ✅ Already done: `ChanceOfProfit` statistical calculation via Black-Scholes `d2` / `norm.cdf`.
- **Remaining**: Add a docstring citing the derivation formula so it's documented in-code.

#### [MODIFY] [`api/data_api.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/api/data_api.py)
- ✅ Already done: `GET /data/options/chain/{symbol}` endpoint.
- **Remaining**: Verify the endpoint returns correct JSON shape with Greeks and Chance of Profit.

#### [MODIFY] [`tests/test_data_api_options_chain.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/tests/test_data_api_options_chain.py)
- ✅ Already done: Basic endpoint test with mocked `require_token`.
- **Remaining**: Add a test checking the Chance of Profit derivation against a known reference case (e.g., Black-Scholes with known S, K, σ, T, r → expected PoP).

### Verification Gate (Phase 1)
```bash
pytest tests/test_data_api_options_chain.py -v   # All pass
```
- Chance of Profit test validates against a hand-computed reference value.
- No frontend changes → typecheck is a no-op for this phase.

---

## Phase 2 — Chain Explorer Screen (Core UI)

> **Goal**: A navigable Options Chain screen with expiration selector, calls/puts grid, and spot price header — accessible from `SymbolDetail.tsx`. No Order Ticket or Strategy Builder yet.

### Files

#### [MODIFY] [`webapp/src/api/types.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/api/types.ts)
- ✅ Already done: `OptionContract`, `OptionChainResponse`, `OptionGreeks` types.

#### [MODIFY] [`webapp/src/api/client.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/api/client.ts)
- ✅ Already done: `getOptionsChain(symbol, expiration?)` method.

#### [MODIFY] [`webapp/src/api/mock.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/api/mock.ts)
- ✅ Already done: Mock data with realistic calls/puts and Greeks including `chanceOfProfit`.

#### [MODIFY] [`webapp/src/components/options/OptionsChain.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/components/options/OptionsChain.tsx)
- ✅ Already done: Grid component using `DataTable` with `Column<OptionContract>` definitions.
- **Remaining**: Polish the visual design to match reference screenshots — dark cards, green accent for ITM strikes, premium-feel typography.

#### [MODIFY] [`webapp/src/screens/OptionsChain.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/screens/OptionsChain.tsx)
- ✅ Already done: Screen shell with header, expiration scroller, chain grid integration.
- **Remaining**:
  - Remove Strategy Builder and Order Ticket imports/rendering (those come in Phase 3 & 4).
  - Add a `Share price: $X.XX` sticky banner matching the reference screenshots.
  - Add a Calls / Puts tab toggle (the current grid renders both; the reference UI lets the user pick one at a time).

#### [MODIFY] [`webapp/src/screens/SymbolDetail.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/screens/SymbolDetail.tsx)
- **NEW**: Add a prominent "Trade Options" button that navigates to `/symbol/{ticker}/options`.

#### [MODIFY] [`webapp/src/navigation.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/navigation.tsx)
- **NEW**: Register `<Route path="/symbol/:ticker/options" element={<OptionsChain />} />`.

#### [MODIFY] [`webapp/src/help/helpContent.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/help/helpContent.ts)
- **NEW**: Add a `TAB_HELP` entry for the Options Chain screen with key concepts (delta, theta, IV, chance of profit) and corresponding `GLOSSARY` entries.

### Verification Gate (Phase 2)
```bash
npm run --prefix webapp typecheck     # Clean (exit 0)
npm run --prefix webapp dev           # Visual check: navigate to a symbol → "Trade Options" → chain loads
```
- Expiration pills scroll horizontally, selecting one refreshes the grid.
- ITM strikes visually highlighted.
- `TabGuide` help panel renders on first visit.

---

## Phase 3 — Order Ticket + Metric Selector

> **Goal**: Tapping a strike in the chain opens a detailed Order Ticket bottom sheet showing full contract details, Greeks, Chance of Profit, and a Buy/Sell button with a Live/Paper toggle. Also: a "Metric Selector" settings sheet for the chain grid columns.

### Files

#### [MODIFY] [`webapp/src/components/options/OptionsOrderTicket.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/components/options/OptionsOrderTicket.tsx)
- ✅ Already done: Component shell with Greeks display, bid/ask, volume/OI, Buy/Sell buttons.
- **Remaining**:
  - Add the **Live / Paper toggle** switch at the top of the ticket. Default to Paper.
  - In Paper mode: Buy/Sell shows a toast confirmation ("Paper order placed"). No API call.
  - In Live mode: Buy/Sell routes to `POST /brokerage/...` with a confirmation modal first.
  - Match the reference screenshot layout: contract title ("Sell AGNC $11 Call 8/14"), bid/ask with depth bar chart, Mark/Last trade/IV row, Prev close/High/Low row, Chance of profit/Volume/Open interest row, "The Greeks" section.
  - Add "Add to Watchlist" link below the action button (matches reference screenshot 1).

#### [NEW] `webapp/src/components/options/OptionsMetricSelector.tsx`
- A settings modal/sheet triggered by the ⚙️ icon in the chain header.
- Lets the user pick which two metrics display alongside each strike in the chain grid (matches the new reference screenshot: "Select the metrics for your covered calls").
- **Categories**: Popular, Price, Volume, Greeks, IV & probability.
- **Available metrics**: Breakeven, To breakeven, Implied volatility, Bid price, Ask price, Mark price, Volume, Open interest, Theta, Delta, Gamma, Vega, Rho, Chance of profit.
- Persists selection in `localStorage` (no backend needed).

#### [MODIFY] `webapp/src/screens/OptionsChain.tsx`
- Re-integrate `OptionsOrderTicket` (was removed in Phase 2 to keep scope narrow).
- Wire the ⚙️ settings button to open `OptionsMetricSelector`.
- Apply the user's metric selection to the chain grid columns.

### Verification Gate (Phase 3)
```bash
npm run --prefix webapp typecheck     # Clean
npm run --prefix webapp dev           # Visual check:
```
- Tap a strike → Order Ticket slides up with full contract detail.
- Live/Paper toggle defaults to Paper; Paper orders show toast only.
- ⚙️ opens the metric selector; changing metrics updates chain columns.
- Greeks section matches reference layout (3-col grid: Delta/Gamma/Theta, Vega/Rho).

---

## Phase 4 — Strategy Builder + Auto Delta-Target

> **Goal**: A "Builder" tab (alongside the expiration dates) that shows pre-built multi-leg strategy templates (Straddles, Strangles, Calendar Spreads, Verticals) with payoff diagrams and **automatic strike selection by delta target**.

### Files

#### [MODIFY] [`webapp/src/components/options/OptionsStrategyBuilder.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/webapp/src/components/options/OptionsStrategyBuilder.tsx)
- ✅ Already done: Component shell with strategy categories and leg display.
- **Remaining**:
  - **Auto delta-target logic**: Given a strategy template (e.g., "16 delta strangle"), scan the chain's Greeks to find the call and put closest to Δ = ±0.16 and auto-populate the legs.
  - **Strategy categories** (matching reference screenshot 2):
    - Vertical Spreads: Bull Call Spread, Bear Put Spread, Bull Put Spread, Bear Call Spread.
    - Straddles & Strangles: Long Straddle (Volatile), Long Strangle (Volatile).
    - Calendar Spreads: Long Call Calendar (Neutral), Long Put Calendar (Neutral), Short Put Calendar (Volatile).
  - **Payoff diagrams**: Small SVG/canvas chart per strategy card showing the theoretical P&L curve at expiration (purple fill area with dotted breakeven line, matching the reference aesthetic).
  - **Market outlook labels**: Each card tagged "Volatile", "Neutral", "Bullish", "Bearish".
  - **Category descriptions**: Brief explainer text per section (e.g., "Simultaneously buy and sell similar options with different expiration dates. Calendar spreads profit from differences in implied volatility over time.").

#### [MODIFY] `webapp/src/screens/OptionsChain.tsx`
- Add a "Builder" pill/tab to the expiration scroller (leftmost position, matches reference screenshot 2: "⊞ Builder | Aug 14 | Aug 21 | ...").
- When Builder is selected, swap the chain grid for the `OptionsStrategyBuilder` full-screen view.
- When a strategy card is tapped, auto-populate the Order Ticket with the computed legs.

### Verification Gate (Phase 4)
```bash
npm run --prefix webapp typecheck     # Clean
npm run --prefix webapp dev           # Visual check:
```
- Builder tab visible as first pill in expiration scroller.
- Strategy cards render with payoff diagrams and outlook labels.
- Selecting "Long Strangle" auto-finds strikes nearest Δ = ±0.16 and populates Order Ticket.
- Order Ticket shows multi-leg summary (net debit/credit, combined Greeks).

---

## Cross-Phase Documentation (ships with Phase 4 PR)

#### [MODIFY] `CLAUDE.md`
- ✅ Already done: Options Tab architecture bullet added.
- **Remaining**: Expand with the full file list and tiered provider details once all phases land.

#### [MODIFY] [`docs/architecture/webapp-and-gui.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/docs/architecture/webapp-and-gui.md)
- Add the Options Chain screen, its 3 sub-components, and the `GET /data/options/chain/{symbol}` endpoint.

#### [MODIFY] [`docs/architecture/data-layer.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-main/plan_options_tab_ui/docs/architecture/data-layer.md)
- Document `OptionsDataProvider` ABC, `YFinanceOptionsProvider`, `CompositeOptionsProvider`, and the FMP spot-price integration.

---

## Phase Summary

| Phase | Scope | Key Deliverable | Depends On |
|-------|-------|-----------------|------------|
| **1** | Backend | Tested API endpoint + tiered data provider | — |
| **2** | Core UI | Chain Explorer screen + route + entry point from SymbolDetail | Phase 1 |
| **3** | Order Ticket | Contract detail sheet + Live/Paper toggle + Metric Selector | Phase 2 |
| **4** | Strategy Builder | Auto delta-target multi-leg builder + payoff diagrams | Phase 3 |
