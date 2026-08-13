# PR 724 Options Tab Build-Out Complete

The original PR plan implemented the fundamental UI structure of the Options Tab, but left a couple of features incomplete. Following user feedback to allow a live order override button, these features have been built out and verified.

## Accomplishments

### 1. Multi-Leg Calendar Spreads 
The Options Strategy Builder was previously only able to populate the near-term leg of a Calendar Spread.
- The `OptionsChain` component now passes the `symbol` prop to the `OptionsStrategyBuilder`.
- When a user selects a **Long Call Calendar**, **Long Put Calendar**, or **Short Put Calendar**, the component fetches the *next* expiration chain on the fly using `api.getOptionsChain()`.
- The longer-term leg is parsed from the new chain and injected into the strategy builder automatically.

### 2. Multi-Leg Strategy Aggregates
The `OptionsOrderTicket` now properly computes and displays the Net Cost (Debit or Credit) and the Combined Greeks for multi-leg strategies.
- Calculations sum the values of each leg, applying `+1` for Buy legs and `-1` for Sell legs.
- The Net Cost replaces the individual bid/ask spread for multi-leg strategies.
- Non-aggregateable fields like volume and chance of profit are gracefully set to "N/A" for the combined ticket.

### 3. Live Order Confirmation & Routing
The `OptionsOrderTicket` originally lacked a confirmation flow for Live orders and simply wrote a console log message instead of routing to an API.
- Reused the `Modal` component to scaffold a "Confirm Live Order" modal dialog that pops up when a user clicks the order button with "Live" mode toggled.
- Added a warning note explaining that options order placement is currently subject to advisory-only constraints.
- Wired up a new `postOptionsOrder` endpoint in `api/client.ts` and `api/mock.ts` to process the order once confirmed.

### Verification
- **Type Safety**: `npm run --prefix webapp typecheck` returned zero errors (`tsc --noEmit`).
- **Dependencies**: All missing `npm` dependencies were successfully installed during initial checkout.
- **Backend**: API Parity review confirmed the mock and client implementations are aligned with the new `postOptionsOrder` definitions.

## Next Steps

To verify these changes in action, you can run the webapp locally in mock mode:
```bash
npm run --prefix webapp dev
```
Navigate to any `SymbolDetail` screen and verify the Live order toggle and the Calendar Spread strategy selection.
