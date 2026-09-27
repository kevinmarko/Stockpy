# Options desk cut — step 3f walkthrough (webapp surface)

Part of the shrink-in-place plan, step 3 (PR 0 → 3a → 3b → 3c → 3d → 3d′ → 3e → **3f**).

## What changed
- Routes `/options` and `/symbol/:ticker/options` removed, along with their nav, Research hub, command palette and Marketplace entries.
- Symbol Detail: the "Trade Options" button and the options-premium panel are removed.
- Custom views: the `optionsDirective` widget is removed. Saved views that still reference it load fine and drop the unknown key.
- Paper Broker: every options panel is removed (desk scanners, Greeks, delta hedge, strategy auto-scan,
  settle-expired, manage-exits, roll, meta-labeler, backtest, scenario matrix, and the Positions Greeks and Roll columns).
  Kept: account summary, positions, orders, closed trades, reset, the Retrospective link, and Quick Trade
  (`EquityOrderTicket`, `?quickTradeSymbol=`).
- Symbol Screener: the selection checkboxes and "Send to Strategy Scan" are removed; that button only fed the options auto-scan.
- Models: the `options_meta_labeler` retrain path is removed.
- Deleted: `components/{options,execution,portfolio,ai}/`, the 3-D vol surface and LOB charts, the transformer-vol and
  diffusion-stress views, `optionsMath.ts`, `optionsHonesty.ts` and `chat/formatOptionsContext.ts`.
- API layer: 52 methods removed from `client.ts`/`mock.ts`, plus about 123 types that became orphans.
  Kept: `getPendingLiveTrades`, `approveLiveTrade`, `rejectLiveTrade`.

## Left on purpose
- The OPTION and SHORT tags in the Positions table. They describe what the account holds.
- The command palette's `optionsStrategyRegistry` completion. It comes from the CLI manifest (the options validation harness)
  and goes when the backend drops that field in step 4.
- Mock copies of backend data that mention options (catalog Pilots, feature flags, settings text). These follow the backend.

## Verification
- `tsc --noEmit`: clean.
- `vitest`: 154 files, 1,807 tests pass.
- Live browser check against the real paper account (Pilots and Data APIs from this branch, `VITE_USE_MOCK=false`):
  - Quick Trade paper BUY 1 F filled at $12.71. Available cash fell by $13.71 (including the $1 commission). The position and order both showed.
  - The not-tracked prompt appeared, and "Not now" was chosen.
  - Paper SELL 1 F filled at $12.71. The position is gone, and a closed trade was recorded: -$1.00 (commission), "flatten", with its Autopsy link.
  - Every Pilots API call returned 200. The only console errors were from the Control API (:8601), which wasn't started.
  - Symbol Detail (ABR) renders with no options UI.
