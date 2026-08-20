# Delta Hedge Preview Fabrication Fix — Walkthrough

## The bug

`pilots/options_hedging.py::get_delta_hedge_preview()` (backs
`GET /pilots/paper-broker/delta-hedge/preview`, which drives the always-visible
"⚖️ Dynamic Delta Hedging" panel on the Pilots PWA's Paper Broker screen) fell back
to a hardcoded `spy_spot = 500.0` whenever the live SPY quote lookup failed or
returned `<= 0` (thin history, missing `FMP_API_KEY`, a quote-provider hiccup, etc.).

That fabricated price then fed `beta_weighted_delta_spy`, `net_dollar_delta`, and
`target_hedge_shares` (all derived from it via `calculate_delta_hedge_order`), and was
echoed straight back in the response as `spy_spot`. The result: on a bad data day the
panel silently rendered a plausible-but-fake hedge recommendation instead of degrading
honestly — a direct violation of this repo's CONSTRAINT #4 ("never fabricate a
measured value — degrade honestly instead").

The sibling write endpoint, `execute_delta_hedge()` in the same file, already refused
correctly in this exact situation (`{"ok": False, "reason": "SPY spot price
unavailable", ...}`). That fix was simply never applied to the read/preview path.

## The fix

### Backend (`pilots/options_hedging.py`)

`get_delta_hedge_preview()` now resolves the live SPY price the same way
`execute_delta_hedge()` already does — via `pilots.price_provider.get_current_price`
— and treats a missing/non-positive/exception-raising lookup as `spy_spot = None`
instead of `500.0`. When `spy_spot` is `None`, the function returns immediately with
an honest "unavailable" response:

```python
{
    "symbol": "SPY",
    "available": False,
    "net_dollar_delta": None,
    "beta_weighted_delta_spy": None,
    "target_hedge_shares": None,
    "tolerance_band_shares": tolerance_band_shares,
    "action": "HOLD",
    "shares": 0.0,
    "required_action": False,
    "reason": "SPY spot price unavailable",
    "message": "Delta hedge preview unavailable: no live SPY quote available (refusing to fabricate a price).",
    "spy_spot": None,
}
```

Every field that would otherwise be derived from the fabricated price is `None`
rather than a fake number — nothing downstream of a missing price is computed at all.
The two existing "success" branches (deadband HOLD, and a real BUY/SELL
recommendation) now also carry `"available": True` for symmetry, so every response
shape is self-describing.

`api/pilots_api.py`'s route (`GET /pilots/paper-broker/delta-hedge/preview`) needed no
change — it already returns `Dict[str, Any]` straight from `get_delta_hedge_preview()`
with no Pydantic response model constraining the shape.

### Frontend (`webapp/src/`)

- **`webapp/src/api/types.ts`**: `DeltaHedgePreview` is now a discriminated union —
  `DeltaHedgePreviewAvailable` (`available: true`, all numeric fields real) and
  `DeltaHedgePreviewUnavailable` (`available: false`, every derived field `null`).
  This gives real TypeScript narrowing at call sites instead of `!`-assertions
  everywhere a field is read.
- **`webapp/src/api/mock.ts`**: the `getDeltaHedgePreview` mock fixture now includes
  `available: true` (offline/mock mode always has a "live" SPY quote).
- **`webapp/src/screens/PaperBroker.tsx`**: the delta-hedge card now branches on
  `deltaHedge.data.available`. When `false`, it renders an honest
  "Hedge data unavailable — no live SPY quote available (refusing to show a
  fabricated recommendation)" message instead of attempting to render (or crash on)
  `null` Greeks. When `true`, the existing card renders unchanged, now reading off a
  locally captured `const hedge = deltaHedge.data` so the `available: true` narrowing
  survives into the render closure.
- **`webapp/src/screens/PaperBroker.test.tsx`**: its `getDeltaHedgePreview` mock now
  includes `available: true` to match the new required field.

## What was verified

- **Python**: `pytest tests/test_options_hedging.py -q` — 16 passed (was 12; added 4
  new tests):
  - `test_get_delta_hedge_preview_refuses_when_spy_spot_unavailable` — patches
    `pilots.price_provider.get_current_price` to return `0.0` and asserts
    `available is False`, every derived field is `None`, and `500.0` never appears
    anywhere in the response values.
  - `test_get_delta_hedge_preview_refuses_when_price_lookup_raises` — same assertion
    when the price provider raises instead of returning a bad value.
  - `test_get_delta_hedge_preview_available_when_spy_spot_provided` — sanity check
    that a real `spy_spot` still produces `available: True` with real numbers (the
    refusal path only engages on an actual lookup failure).
  - `test_execute_delta_hedge_refuses_when_spy_spot_unavailable` — added for parity;
    `execute_delta_hedge` already had this behavior but no direct regression test
    existed for it.
  - Also ran `pytest tests/test_options_hedging.py tests/test_pilots_paper_broker.py -q`
    together — 188 passed (the paper-broker endpoint tests mock
    `pilots.options_hedging.get_delta_hedge_preview` directly, so they were
    unaffected by the internal refusal-path change).
- **Webapp typecheck**: `npm run --prefix webapp typecheck` — clean, no errors, after
  changing `DeltaHedgePreview` to a discriminated union.
- **Webapp targeted test**: `npm run --prefix webapp test -- src/screens/PaperBroker.test.tsx`
  — 11 passed.
- **Full webapp suite** (belt-and-suspenders, since the type change touched a
  shared interface): `npm run --prefix webapp test -- --run` — 168 test files / 1774
  tests passed.

## Follow-up risk

- The fix only closes this one fabrication path. Other options-desk endpoints may
  have similar "fall back to a plausible hardcoded number" patterns that weren't in
  scope for this change — per the task instructions this PR deliberately does not
  touch any other finding from the broader Paper Broker audit.
- `get_delta_hedge_preview()`'s alert dispatch (`dispatch_delta_hedge_alert`) is
  untouched and still only fires on the real BUY/SELL branch — the new unavailable
  branch returns before reaching it, which is correct (there is nothing to alert on
  when no hedge recommendation could be computed).
- The webapp render now shows a static "unavailable" card with no retry affordance;
  the panel already refetches (`deltaHedge.reload()`) after other mutations complete
  elsewhere on the screen, so a subsequent successful quote fetch will naturally
  replace the unavailable card on the next reload rather than requiring a dedicated
  retry button. If operators want an explicit retry button for this state
  specifically, that would be a small, separate follow-up.
