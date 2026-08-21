# Revised Functionality Improvements Plan

Thank you for the detailed architectural review. You are completely right—the previous implementation introduced severe architectural drift by duplicating the Robinhood connector, bypassing the Execution gating with ambiguous buttons, and silently defaulting missing data. 

This revised plan rolls back those missteps and implements the functionality using Stockpy's native, advisory-only architecture.

## Open Questions
- Should the "Liquidate" and "Rebalance" buttons open a pre-filled manual ticket modal (instructing the human operator to manually execute on Robinhood), or should we omit them entirely until Phase 5 execution is fully built? *Assuming: they open a manual ticket modal for human execution.*

## Proposed Changes

### Revert Unofficial Robinhood Connector
#### [DELETE] [stockpy_historian.py](file:///Users/kevinlee/Stockpy-live/stockpy_historian.py)
- Delete the `stockpy_historian.py` script. The raw credential scraping via `robin_stocks` is an insecure duplication of the official MCP connector and bypasses the `HistoricalStore` DB. 

### Frontend React Component Fixes
#### [MODIFY] [AccountPerformanceChart.tsx](file:///Users/kevinlee/Stockpy-live/webapp/src/components/AccountPerformanceChart.tsx)
- Re-wire the component to accept `data: CurvePoint[]` as a prop rather than making its own HTTP requests.
- This will allow the chart to use the official, DB-backed `api.getEquityCurve()` data natively provided by Stockpy, maintaining the beautiful Recharts tooltips without bypassing the architecture.

#### [MODIFY] [Dashboard.tsx](file:///Users/kevinlee/Stockpy-live/webapp/src/screens/Dashboard.tsx)
- **Account Performance chart**: Restore the `api.getEquityCurve()` fetching logic and pass `equityCurve` to the new `AccountPerformanceChart`.
- **Top Pilots Sorting**: Explicitly handle missing `sharpe` values in the sorting logic. Pilots with `null` or missing Sharpe ratios will sort to the very bottom to avoid fabricating data (Constraint #4), and will render as `SR: —`.
- **Liquidate/Rebalance Buttons**: Wire these buttons to open a safe, advisory-only `DecisionModal` (already exists in the codebase). This modal will present a pre-filled "Manual Ticket" instructing the operator to execute the trade manually in Robinhood, ensuring no automated broker calls are made (`place_equity_order` will not be invoked).

## Verification Plan

### Automated Tests
- Run `npm run --prefix webapp typecheck` to ensure no typing regressions.

### Manual Verification
1. I will load the dashboard with an empty portfolio state (no DB history) to verify the chart gracefully degrades to the empty state without raising.
2. I will check the Top Pilots section to ensure a pilot with a `null` Sharpe ratio sorts to the bottom.
3. I will click "Liquidate" and verify that it only opens an advisory modal and does not trigger any backend network requests.
