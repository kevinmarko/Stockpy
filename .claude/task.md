# Options Tab — Task Tracker

## Phase 1 — Backend API + Data Layer
- [x] Add docstring to `chance_of_profit` in `technical_options_engine.py` citing the Black-Scholes derivation
- [x] Add reference-case PoP test in `tests/test_data_api_options_chain.py`
- [x] Verify Phase 1 gate: `pytest tests/test_data_api_options_chain.py -v` ✅ 1 passed

## Phase 2 — Chain Explorer Screen (Core UI)
- [x] Simplify `screens/OptionsChain.tsx` — remove Strategy Builder and Order Ticket (Phase 3 & 4)
- [x] Add Calls / Puts tab toggle to the chain screen
- [x] Add `Share price: $X.XX` sticky banner
- [x] Polish `OptionsChain` grid for dark premium aesthetic
- [x] Add "Trade Options" button to `SymbolDetail.tsx`
- [x] Register `/symbol/:ticker/options` route in `navigation.tsx`
- [x] Add `TAB_HELP` + `GLOSSARY` entries in `helpContent.ts`
- [x] Verify Phase 2 gate: `npm run --prefix webapp typecheck` clean + visual check

### Phase 3: Order Ticket & Config
- [x] Build `OptionsOrderTicket.tsx`
  - [x] Mock bid/ask depth bar
  - [x] Contract details grid (Mark, Last, IV, Greeks, PoP)
  - [x] Live / Paper toggle
  - [x] Submit button (disabled state, color based on live/paper)
- [x] Build `OptionsMetricSelector.tsx`
  - [x] Modal/Sheet overlay
  - [x] List of available metrics (Volume, OI, Greeks, PoP)
  - [x] Toggle for each metric
- [x] Integrate into `OptionsChain.tsx`
  - [x] State for selected metrics
  - [x] State for selected legs (to show order ticket)
  - [x] Wire settings icon to metric selector
  - [x] Render floating order ticket when legs are selected

## Phase 4: Strategy Builder & Advanced Order Types
- [x] Create `OptionsStrategyBuilder` component for multi-leg strategies.
- [x] Implement strategy templates (Vertical, Calendar, Strangle).
- [x] Create `OptionsPayoffChart` component to visualize max profit/loss and breakevens.
- [x] Update `OptionsChain` to toggle between Standard Grid and Strategy Builder.
- [x] Add auto-delta targeting logic (`findClosestStrikeByDelta`).
- [x] **Documentation Updates:**
  - [x] Update `docs/architecture/data-layer.md` with new `OptionsDataProvider` details.
  - [x] Finalize `CLAUDE.md` and `docs/architecture/webapp-and-gui.md` for Options Tab features.

## Cross-Phase Documentation
- [x] Expand `CLAUDE.md` Options Tab bullet with full details
- [x] Update `docs/architecture/webapp-and-gui.md`
- [x] Update `docs/architecture/data-layer.md`
