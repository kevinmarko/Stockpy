# Paper Broker — dead-conditional hardcoded spot-price cleanup

Branch: `paper-broker-remove-hardcoded-spot-price-ternaries`

## The bug

`webapp/src/screens/PaperBroker.tsx` passed a `spotPrice` prop to five options-desk
child panels via a dead ternary that always evaluated to the same literal
regardless of its condition:

```tsx
spotPrice={account.data?.equity ? 546.50 : 546.50}   // x4 (SOR, GEX, LOB, MM Agent)
spotPrice={account.data?.equity ? 505.20 : 505.20}   // Gamma Scalper
```

`account.data?.equity` was never actually a proxy for a live SPY quote — a
live-quote wire-up that was clearly started and abandoned.

## Investigation per panel

For each panel I read how the prop is actually consumed internally, to decide
between real live-quote wiring and an honest named-constant simplification.

| Panel | How `spotPrice` is used internally | Verdict |
|---|---|---|
| **GexProfileView** | `const currentSpot = data?.spot_price \|\| initialSpot \|\| 500;` — the component already fetches its own `spot_price` from `api.getOptionsGexProfile()`. The prop is only a placeholder shown before that first fetch resolves. | Already effectively live via its own server fetch. Named constant. |
| **LobDepthView** | Destructured as `spotPrice: _spotPrice` — **not read anywhere in the component**. Its LOB queue simulation (`api.simulateLobQueue`) is fully server-side and driven by its own `priceLevel`/`orderSize`/`depthAhead` inputs. | Prop is entirely dead already. Named constant (keeps the call signature unchanged; no behavior to fix inside the child). |
| **MarketMakerAgentView** | `spot_price: spotPrice` is sent directly to `api.simulateMarketMakerAgent()`. But this component also lets the operator switch the underlying symbol (SPY/QQQ/NVDA/AAPL/TSLA/IWM) — a single spot value from the parent screen would be right only for SPY and wrong for every other symbol choice. Fixing that properly means the component fetching its own per-symbol quote, not this screen supplying one value. | Named constant; real fix is a separate, component-scoped change. |
| **SmartOrderRouterView** | `spot_price: spotPrice` is sent to both `api.analyzeOptionsRouting()` and `api.simulateOptionsLegging()`, and used client-side to compute preset strikes (`getPresetLegs`). The component is hard-locked to SPY (`const [symbol] = useState(initialSymbol)` — no setter used). A real live SPY quote would genuinely improve this panel. | Would benefit from real wiring, but the screen has no live-tick feed already open for it to reuse — the closest candidate, `hooks/useLiveTick.ts`, opens a persistent per-symbol WebSocket that would then run for the entire lifetime of the Paper Broker screen (all 7 always-on `useApi` polls already there are REST, not a held socket) just to seed one collapsible panel the operator may never open. Judged out of proportion to this fix. Named constant. |
| **GammaScalperView** | `const [spot, setSpot] = useState(spotPrice)` seeds an editable numeric input right next to the symbol field — the operator can (and is expected to) type in a different spot/strike before running the simulation. | Genuine UI seed/default for an editable form, not a live-data display. Named constant. |

None of the five panels' own real numbers (routing analysis, GEX profile, LOB
fill probabilities, MM agent P&L, gamma-scalp P&L) are fabricated — they all
come from their own real server calls once the panel is open. `spotPrice` was
only ever an initial/default input to those calls, never itself displayed as
"the live price."

## What changed

**`webapp/src/screens/PaperBroker.tsx`**
- Added two named constants above the component, each with a comment
  explaining the per-panel reasoning above and explicitly noting the dead
  ternary is why they exist:
  - `DEFAULT_DESK_SPOT_PRICE = 546.5` (SmartOrderRouterView, GexProfileView,
    LobDepthView, MarketMakerAgentView)
  - `DEFAULT_GAMMA_SCALPER_SPOT_PRICE = 505.2` (GammaScalperView) — kept as a
    separate constant rather than unifying the two literals, to preserve
    today's exact default value for each panel.
- Replaced all 5 `spotPrice={account.data?.equity ? X : X}` call sites with
  the appropriate named constant. No other lines in this file were touched
  (per the merge-conflict note, a different agent is concurrently adding
  loading/error UI to the 7 core data sections in the same file).

**`webapp/src/components/options/SmartOrderRouterView.tsx`**
- Added a clarifying comment above `getPresetLegs()` explaining that its
  hardcoded bid/ask/mid premiums are illustrative preset seed values, not
  live option quotes — this component has no options-chain/quote fetch of
  its own to price a specific strike, unlike GexProfileView. These legs are
  only the *input* sent to the real backend routing/legging analysis, which
  is what actually prices the decision the panel displays. No functional
  change — same numbers as before, now explained rather than left silently
  ambiguous. Wiring a real per-strike quote source was judged genuinely out
  of scope (would require adding a new options-chain lookup, not "minimal
  wiring").

## Verification

- `npm run --prefix webapp typecheck` — clean, no errors.
- `npm run --prefix webapp dev` (mock mode) — visually confirmed all 5
  affected panels on the Paper Broker screen:
  - **Smart Router**: opens, computes strikes off `DEFAULT_DESK_SPOT_PRICE`
    (542/537 PUT legs, i.e. round(546.5) ± 5/10), routing analysis and MC
    hazard stats render with real numbers, no NaN.
  - **GEX Profile**: opens, header shows "Spot Price: $546.50" sourced from
    its own mock server fetch (not the prop), no NaN.
  - **LOB Depth**: opens, queue-fill simulation renders normally (prop is
    unused as expected), no NaN.
  - **MM Agent Sim**: opens, auto-runs on mount, quoting ladder and PnL
    trajectory render with real simulated numbers, no NaN.
  - **Gamma Scalper**: opens, header shows "SPY $505.20" confirming
    `DEFAULT_GAMMA_SCALPER_SPOT_PRICE` is wired correctly, rebalancing
    ledger renders with real numbers, no NaN.
  - Console: no new errors from these changes (one pre-existing transient
    Vite "Outdated Optimize Dep" 504 from the dev server's first dependency
    pre-bundle, unrelated to this diff, seen identically before and after
    interacting with the panels).
