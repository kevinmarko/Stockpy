# Complete PR 724 Options Tab Build-Out

## Problem Description
PR 724 implements Phases 2, 3, and 4 of the Options Tab in the Pilots PWA. However, two features were left incomplete:
1. **Calendar Spreads:** In `OptionsStrategyBuilder.tsx`, Calendar Spreads only populate the near-term leg. A true Calendar Spread requires buying/selling contracts across two different expirations.
2. **Live Order Modal & Routing:** `OptionsOrderTicket.tsx` is missing the confirmation modal for Live mode and the API routing.

## Resolved Questions
Based on user feedback, the "Live" order routing constraint will be handled via a UI toggle/button. We will implement the Live execution flow (with a confirmation modal) and add a `postOptionsOrder` endpoint.

## Proposed Changes

### 1. Calendar Spreads (Multi-Expiration)

To properly build out Calendar spreads, the strategy builder needs access to a second expiration chain. 

#### [MODIFY] `webapp/src/components/options/OptionsStrategyBuilder.tsx`
- When a Calendar spread is selected, use the `api.getOptionsChain` client method to fetch the next available expiration date (if available) for the current symbol.
- Populate `newLegs` with both the near-term leg (from the primary `chain` prop) and the longer-term leg (from the fetched secondary chain).
- Add loading states for when the secondary chain is fetching.

### 2. Options Order Ticket & Modal

#### [MODIFY] `webapp/src/components/options/OptionsOrderTicket.tsx`
- Add a Confirmation Modal state.
- In Paper mode: show the existing toast confirmation ("Paper order placed").
- In Live mode: intercept the `handleSubmit`, show the confirmation modal with the order details.
- Upon confirmation, execute a call to `api.postOptionsOrder()`.

#### [MODIFY] `webapp/src/api/client.ts` and `webapp/src/api/mock.ts`
- Add `postOptionsOrder(symbol: string, expiration: string, legs: any[])` endpoint.
- In `mock.ts`, simulate a successful network response.
- In `client.ts`, add the routing to `POST /brokerage/options/order`. (Note: The backend endpoint will be expected to handle this, though for now the mock mode handles the immediate PWA requirements).

## Verification Plan
1. **Automated Tests:** Run `npm run --prefix webapp typecheck` to ensure there are no TypeScript regressions.
2. **Manual Visual Check (Calendar Spreads):** Navigate to the Options Chain, open the Builder, and select a Calendar Spread. Verify that *two* legs are populated with different expirations.
3. **Manual Visual Check (Order Ticket):** Select a contract, open the Order Ticket, toggle to "Live", and click Submit. Verify that a confirmation modal appears before proceeding and simulating the network call.
