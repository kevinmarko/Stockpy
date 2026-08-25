# `pilots/options_gex.py` displayed dollar GEX figures 100x too large, plus two CONSTRAINT #4 fabrication fallbacks

**Status: Fixed and verified.** Found during a secondary audit pass (2026-08-24) covering
areas flagged as "not yet deep-audited": the GEX module's own dealer-gamma sign
convention had already been confirmed correct in an earlier pass; this pass covered
everything else.

## How this was found

A full re-audit of `pilots/options_gex.py` beyond the already-confirmed sign
convention, cross-checking every dollar figure the module computes against the
module's own separately-computed `dealer_hedging_flow` field, and checking every
missing-data fallback against CONSTRAINT #4.

## Finding 1 — 100x dollar-scaling bug (High)

`compute_total_net_gex_at_spot`/`calculate_strike_gex` computed dollar GEX as

```
Γ(S) × OI × 100 (contract multiplier) × S²
```

— matching the module's own docstring formula exactly, but **missing the
industry-standard `× 0.01` "per 1% underlying move" normalization**
(SqueezeMetrics/SpotGamma convention). Hand-derivation: a 1% move in the underlying
is `0.01 × S` dollars; the number of shares dealers must transact for that move is
`Γ × OI × 100 × (0.01 × S)`, and the dollar value of that share flow is that
quantity times `S` again — i.e. `Γ × OI × 100 × S² × 0.01`. Omitting the `0.01`
overstated every dollar GEX figure by exactly 100x.

This was internally self-inconsistent, not just wrong in isolation:
`get_options_gex_profile`'s own `dealer_hedging_flow` field computed
`res.net_gex * 0.01` — i.e. the author clearly knew the `0.01` convention was
needed, but only applied it to that one derived field, leaving the **primary,
most prominent** values (`net_gex`, `call_gex`, `put_gex`, every per-strike figure
— everything the webapp's "Total Net Dealer GEX" KPI tile and strike-ladder chart
render) exactly 100x larger than the value the SAME response separately (and
correctly) reported as "dealer hedging flow per 1% move." Worked example: a single
ATM SPY strike, 10,000 OI, 20% IV, 30 DTE computed to **$3.46 billion** of "Net GEX"
under the pre-fix formula for ONE strike — implausible on its face, and 100x larger
than the module's own `mock.ts` webapp fixture, which independently generates
illustrative GEX magnitudes in the hundreds-of-millions range for an entire strike
ladder (further confirming the live backend's pre-fix scale was the outlier, not the
mock).

Fixed by introducing `PERCENT_MOVE_SCALING_FACTOR = 0.01` and applying it once, at
aggregation (`compute_total_net_gex_at_spot`, `calculate_strike_gex`), then removing
the now-redundant re-application in `get_options_gex_profile`'s `dealer_hedging_flow`
computation. The zero-gamma-flip root-finder is unaffected (scale-invariant under a
uniform positive constant — the root of `NetGEX(S) = 0` doesn't move whether `NetGEX`
is scaled by 1 or 0.01), confirmed by the existing root-finding tests still passing
unchanged.

## Finding 2 & 3 — fabricated placeholder IV/DTE on missing data (Medium, CONSTRAINT #4)

`_normalize_chain_data` previously defaulted a missing, zero, or unparseable implied
volatility to a fabricated `0.25` (25%), and a missing/unparseable expiration to a
fabricated `30.0` days — silently pricing a contract with plausible-looking but
entirely made-up inputs instead of excluding it. This is directly reachable on real
data: yfinance option chains routinely report `impliedVolatility=0.0` for
illiquid/stale-quote strikes, and the `or 0.25` fallback fired on that exact case
(among others) before the sigma degenerate-guard even ran.

Fixed by excluding the contract entirely (matching the existing convention for a
strike/option-type parse failure a few lines above) rather than substituting a
plausible default, with an aggregate `logger.warning` (count of excluded contracts,
never per-contract spam) when any are dropped. `_parse_expiration_dte` now returns
`Optional[float]` (`None` on missing/unparseable input, never `30.0`).

## Finding 4 (informational) — reimplements Black-Scholes gamma instead of the canonical implementation

`calculate_black_scholes_gamma` is a second, independent Black-Scholes gamma formula,
not routed through `pilots/options_risk.py` (this repo's single source of truth for
options Greeks). Verified numerically identical to the canonical implementation
across ATM/OTM/near-0DTE cases (diff ≤ 3e-18) — **not a live correctness bug today**,
flagged per this repo's documented policy against a second Greeks implementation
(the exact class of bug this repo has been bitten by before, drifting silently
apart). **Not fixed in this pass** — delegating to
`pilots.options_risk.calculate_black_scholes_greeks` would add the overhead of
computing price/delta/theta/vega/rho on every call inside a hot root-finding loop
(evaluated once per contract per bisection/grid iteration) for a value it only needs
gamma from; a real fix should either add a gamma-only fast path to the canonical
module or accept the added cost — a design decision, not folded into this audit
pass.

## Finding 5 (fixed, webapp) — `GexProfileView.tsx` never surfaced the backend's own honesty fields

The engine and API both already honestly flag a chain-resolution failure
(`chain_source: "synthetic"`) or an unresolvable spot price
(`spot_price_source: "unavailable"`) — but `GexProfileView.tsx` never read either
field, so a fully procedurally-generated GEX profile rendered with zero visual
indication it wasn't real market structure. Fixed by adding an honesty banner
(mirroring this codebase's existing `DemoDataBadge`/`is_synthetic` pattern) that
renders whenever `chain_source !== "live"`, distinguishing the "synthetic fallback"
and "offline mock backend" cases.

## Verification

- `tests/test_options_gex.py`: 25 passed (20 pre-existing + 5 new — the scaling
  regression pinning the exact hand-derived dollar figure, a `net_gex ==
  dealer_hedging_flow` consistency check, IV-exclusion, expiration-exclusion, and
  `_parse_expiration_dte`'s new `None` contract).
- `webapp/src/components/options/GexProfileView.test.tsx`: 9 passed (6 pre-existing +
  3 new — banner absent on `chain_source: "live"`, banner present + correct message
  on `"synthetic"`/`spot_price_source: "unavailable"`, "Demo Data" banner on
  `"mock"`).
- `npm run typecheck` (webapp): clean.

## What this does NOT fix / disclosed scope

- The second Black-Scholes gamma implementation (Finding 4) is disclosed, not
  removed — a real drift risk, but not a live correctness bug today.
- No live-network re-verification was performed — this sandbox has no live-market
  network access; all fixes were verified against the existing mocked/synthetic test
  fixtures.
