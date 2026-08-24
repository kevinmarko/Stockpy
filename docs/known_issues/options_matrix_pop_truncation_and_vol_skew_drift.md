# Known issue (2026-08-24): Options Matrix "Prob of Profit" silently dropped tail probability + VolSurface3D skew drift-risk

**Status: fixed.** [PR #895](https://github.com/kevinmarko/Stockpy/pull/895)
(branch `fix-options-pop-and-vol-skew-drift`).

Two findings from an external audit of client-side quantitative
calculations in `webapp/src/` — the only two genuine issues found across
the whole frontend. Everything else audited (payoff formulas, Greeks
rollups, P&L displays, microstructure formulas) checked out correct; there
is no client-side Black-Scholes/Greeks formula anywhere in the app — every
delta/gamma/theta/vega comes from the backend.

## 1. POP truncation bug (confirmed, quantified)

`webapp/src/screens/OptionsMatrix.tsx`'s options directive detail modal
computed "Prob of Profit (POP)" via a Riemann-sum numerical integration of
a `Normal(spotPrice, sd)` density over the SAME finite price grid built
for the P/L chart (`webapp/src/optionsMath.ts::computePayoff`) — bounded
to `[0.8×spot, 1.2×spot]`, widened only modestly (`strike×0.9`/`strike×1.1`)
by the position's own strikes.

That grid is sized correctly for a chart's x-axis but is wrong for
integrating probability: whenever a strategy's profit region extends past
the grid edge — true of essentially every credit spread, whose flat
profitable plateau runs to ±∞ past the last strike — the tail probability
mass beyond the grid boundary was silently dropped instead of integrated.
POP was therefore systematically **understated**, worse as IV/DTE grow.

Hand-verified by replicating the exact TS logic in Node and comparing
against the closed-form complementary CDF under the code's own chosen
(Normal) model:

| Case | Old (buggy) | True closed-form |
|---|---|---|
| Bull Put Credit Spread — short 70P/long 60P @ $3/$1, spot 100, σ=40%, 252 DTE | 48.19% | 83.25% |
| Long Call ATM, spot 100, σ=20%, 45 DTE | 26.45% | 28.19% |
| Iron Condor 85/90/110/115, spot 100, σ=25%, 30 DTE | 83.28% | 83.64% |

The credit-spread case is a **35-percentage-point understatement** — a
trader reading "POP 48%" on a trade that's actually ~83% likely to profit
could reasonably reject a good trade or badly misjudge risk/reward. No
backend equivalent existed (no `probability_of_profit`/`prob_of_profit`
anywhere in the Python tree), and the calculation had zero test coverage
before this fix (`optionsMath.test.ts` covered `computePayoff`/
`computeBreakevenPoints`/`computeExpectedMove`/`computeProbabilityZones`
but never this inline POP calculation).

### Fix

`optionsMath.ts` gained `computeProbabilityOfProfit(legs, spotPrice,
sigma, dte)`: a closed-form integration over the TRUE unbounded price
domain `(0, +Infinity)`, not a numerical sum over `computePayoff`'s
finite chart grid. `computeBreakevenPoints` (already correct) partitions
the domain into intervals whose payoff sign cannot change within them (a
multi-leg vanilla-option position is asymptotically linear — never
oscillates — beyond its outermost strike, and breakevens are exactly its
sign changes), so one representative probe point per interval is enough
to classify it profit/loss. The log-normal probability mass
(`cumulativeNormalDistribution`, the same model
`computeProbabilityZones` already used elsewhere in this module) of every
profit interval — including the two open-ended tails — is summed via
closed-form CDF. This also switches the price-at-expiration model from
Normal (the old, buggy code) to log-normal, matching the rest of this
module.

`webapp/src/screens/OptionsMatrix.tsx`'s `DetailSheet` now calls this
directly instead of integrating a PDF over `payoffPoints`; the P/L chart
itself is unaffected (still built from `computePayoff`'s grid, which
remains correctly sized for a chart x-axis).

Along the way, `evaluatePayoffAt` was extracted as one shared, exported
helper out of `computePayoff` and `computeBreakevenPoints` (previously
two independent copy-pasted payoff formulas), so all three functions now
price a position identically by construction rather than by two formulas
happening to agree.

**Why fixed in the frontend, not moved to the backend** (the audit
offered both routes): the existing calculation already runs on
`Sigma_GARCH` (GARCH-forecast vol), not real options-chain IV — moving
the *same* computation to the backend would not improve input-data
quality, only relocate where it runs, at the cost of a new endpoint,
`OptionsDirective` schema field, `config.COLUMN_SCHEMA` addition, and
pipeline recompute for what is purely a client-side numerical-integration
bounds bug, fully fixable in closed form with data already in scope.

### Test coverage

`optionsMath.test.ts`'s new `computeProbabilityOfProfit` suite:
reproduces the credit-spread discrepancy case with a self-computed
closed-form reference (`1 - Φ(ln(BE/spot)/periodSigma)`, computed via the
module's own `cumulativeNormalDistribution` — not a hardcoded magic
number) and asserts the fix lands within a tight tolerance of it; a
near-the-money single-breakeven sanity check; a multi-breakeven (iron
condor) case proving the interval-partitioning logic generalizes past a
single breakeven; degenerate-input handling; and a `[0, 100]` bounds
check at an extreme input.

## 2. VolSurface3D skew drift-risk (latent, fixed pre-emptively)

`webapp/src/components/charts/VolSurface3D.tsx`'s
`calculateSurfaceMetrics()` always computed its own "25Δ Put-Call Skew"
via a fixed-moneyness proxy (nearest available strike to `spot×0.95`/
`spot×1.05`) — never actually reading a `.delta` field despite the label.
The backend already computes the REAL, delta-derived value:
`pilots/volatility_surface.py::compute_25delta_skew`, delivered as
`VolSurfaceResponse.skew.skew_25delta`/`.put_25delta_iv`/
`.call_25delta_iv`. The sibling 2D screen,
`webapp/src/components/options/VolSurfaceView.tsx`, correctly displays
that real backend value under the IDENTICAL label "25-Delta Put-Call
Skew" — so the same label meant two different, disagreeing things on two
different screens for the same symbol.

Confirmed via grep that `VolSurface3D.tsx` never read
`.skew_25delta`/`.put_25delta_iv`/`.call_25delta_iv` anywhere before this
fix. It was latent, not live-impacting, because the only live call site
(`webapp/src/screens/OptionsChain.tsx`, `<VolSurface3D symbol
spotPrice />`) passes neither `volResponse` nor `points`, so it always
fell through to `generateSyntheticVolMesh()` (fabricated demo data, with
a visible `<DemoDataBadge />`) — no real user saw the wrong number. But
it was a live trap: the moment any future change wired real vol-surface
data into this component (which its own props/types/tests already
anticipated), it would have silently shown a number contradicting the
correct one on the sibling screen.

### Fix

`calculateSurfaceMetrics` now takes an optional `volResponse` parameter
and returns `skew25d: number | null` plus a new `skew25dIsReal: boolean`:

- No `volResponse` at all → genuinely synthetic/demo mesh (already
  flagged elsewhere by `<DemoDataBadge />`) → the moneyness proxy is the
  only estimate available, unchanged from before for today's one live
  call site.
- `volResponse` present, `skew.skew_25delta` a finite number → use the
  real backend value.
- `volResponse` present, `skew.skew_25delta` absent/non-finite → an
  honest `null` (renders "—" plus "Unavailable this cycle"), **never**
  the proxy. Falling back to the proxy in this case would have kept the
  exact "two disagreeing numbers under one label" bug, just now visible
  instead of latent.

Mirrors `optionsHonesty.effectiveIvr`'s real-vs-proxy preference pattern,
already used for the analogous IVR field in `OptionsMatrix.tsx`. The card
also gained a small "(chain)"/"(proxy)" provenance suffix next to the
"25Δ PUT-CALL SKEW" label, matching `OptionsMatrix.tsx`'s existing "IVR
(chain)"/"IVR Proxy" convention for the same real-vs-fallback
distinction.

### Test coverage

`VolSurface3D.test.tsx` gained two tests: one supplies a `volResponse`
whose real `skew_25delta` is deliberately set to a value distinct from
whatever the proxy recomputes from the same mesh (the pre-existing test
fixture's proxy and real skew happened to coincidentally agree, which
would not have caught this class of bug) and asserts the displayed value
is the real one; the other supplies a `volResponse` with `skew_25delta:
undefined` and asserts the result is `null`, not the proxy.

## What was NOT changed

No backend/Python changes. No `docs/architecture/*.md` or
`docs/signals/*.md` changes — this is a client-side calculation bug and a
client-side drift-risk, with no backend/schema/signal surface affected.
