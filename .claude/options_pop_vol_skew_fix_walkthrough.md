# Walkthrough: Options Matrix POP truncation bug + VolSurface3D skew drift-risk

## 1. POP truncation bug (confirmed, fixed)

### Before

[`OptionsMatrix.tsx`](../webapp/src/screens/OptionsMatrix.tsx)'s `DetailSheet`
computed "Prob of Profit (POP)" as a Riemann sum of a `Normal(spot, sd)`
density over `computePayoff`'s chart grid — `[0.8×spot, 1.2×spot]`, widened
only modestly (`strike×0.9`/`strike×1.1`) by the position's own strikes:

```ts
const popPercent = useMemo(() => {
  const sd = spotPrice * sigma * Math.sqrt(dte / 252);
  if (payoffPoints.length < 2 || sd <= 0) return null;
  let pop = 0;
  const step = payoffPoints[1].price - payoffPoints[0].price;
  payoffPoints.forEach((pt) => {
    if (pt.payoff > 0) {
      const pdfVal = normalProbabilityDensity(pt.price, spotPrice, sd);
      if (!isNaN(pdfVal)) pop += pdfVal * step;
    }
  });
  return Math.min(100, Math.max(0, pop * 100));
}, [payoffPoints, spotPrice, sigma, dte]);
```

That grid is fine for a chart's x-axis; it's wrong for integrating
probability. A credit spread's profitable region is a flat plateau that
runs to the grid edge and *keeps going* — the tail past the edge was
silently dropped, understating POP.

### Verification (before writing the fix)

Reproduced the bug class in a standalone Node script mirroring the exact TS
logic (not committed — ad hoc verification), comparing against
independently-derived closed-form values:

| Case | Old (buggy) | Fixed | True closed-form |
|---|---|---|---|
| Bull Put Credit Spread: short 70P/long 60P @ $3/$1, spot 100, σ=40%, 252 DTE | 48.19% | 83.25% | 83.25% |
| Long Call ATM, spot 100, σ=20%, 45 DTE | 26.45% | 28.19% | 28.19% |
| Iron Condor 85/90/110/115, spot 100, σ=25%, 30 DTE | 83.28% | 83.64% | 83.64% |

The credit-spread case shows the failure mode the audit flagged: the old
code understated POP by **35 points** (48.19% vs. the true 83.25%) — same
order of magnitude and direction as the audit's own 30.6-point example,
worse for longer-dated/higher-vol credit structures because the truncation
grid stays fixed at `[0.8, 1.2] × spot` regardless of how far the real
profitable plateau extends.

### Fix

[`optionsMath.ts`](../webapp/src/optionsMath.ts) gained
`computeProbabilityOfProfit(legs, spotPrice, sigma, dte)`: a closed-form
integration over the TRUE unbounded price domain `(0, +Infinity)`, using
`computeBreakevenPoints` (already correct) to partition the domain into
sign-constant intervals, and `cumulativeNormalDistribution` (already used
by `computeProbabilityZones`) to sum the log-normal probability mass of
every profitable interval — including the two open-ended tails — via
closed-form CDF instead of a truncated numerical sum. Also switches the
underlying price model from Normal to log-normal, matching the rest of
this module (the audit's secondary note).

Along the way, `evaluatePayoffAt` was extracted as a shared, exported
helper — `computePayoff` and `computeBreakevenPoints` each had their own
copy-pasted payoff formula; now there's one, and the new function reuses
it too, so all three price a position identically by construction.

`OptionsMatrix.tsx`'s `DetailSheet` now calls this directly:

```ts
const popPercent = useMemo(() => {
  return computeProbabilityOfProfit(legs, spotPrice, sigma, dte);
}, [legs, spotPrice, sigma, dte]);
```

The chart itself (`payoffPoints`/`chartData`) is untouched — only POP
moved off the truncated grid.

### Test coverage

`optionsMath.test.ts` — new `computeProbabilityOfProfit` describe block:
reproduces the credit-spread discrepancy case above with a
self-computed closed-form reference (`1 - Φ(ln(BE/spot)/periodSigma)`,
computed via the module's own `cumulativeNormalDistribution`, not a magic
number) and asserts the fix lands within 0.1 of it; a near-the-money
single-breakeven sanity check; a multi-breakeven (iron condor) case
proving the interval-partitioning logic generalizes past one breakeven;
degenerate-input handling (empty legs, non-positive spot/sigma/dte, NaN);
and a `[0, 100]` bounds check at an extreme input. Plus an
`evaluatePayoffAt` test confirming it agrees with `computePayoff`'s own
values point-for-point.

## 2. VolSurface3D skew drift-risk (latent, fixed pre-emptively)

### Before

[`VolSurface3D.tsx`](../webapp/src/components/charts/VolSurface3D.tsx)'s
`calculateSurfaceMetrics(mesh)` always recomputed "25Δ Put-Call Skew" via a
fixed-moneyness proxy (nearest strike to `spot×0.95`/`spot×1.05`), never
reading `.delta` at all despite the label — even when a real `volResponse`
prop (carrying the actual backend-computed `skew.skew_25delta`, from
`pilots/volatility_surface.py::compute_25delta_skew`, a real Black-Scholes
delta lookup against the live chain) was available. The sibling 2D screen,
`VolSurfaceView.tsx`, already renders that real backend value under the
identical "25-Delta Put-Call Skew" label.

Currently harmless — the only live call site
(`webapp/src/screens/OptionsChain.tsx`) never passes `volResponse` or
`points`, so this always falls into the synthetic/demo mesh path (flagged
by `<DemoDataBadge />`). But the component's own props/types/tests already
anticipate a real `volResponse` being wired in, and the moment that
happens this would silently show a number that disagrees with the correct
one on the sibling screen.

### Fix

`calculateSurfaceMetrics` now takes an optional second `volResponse`
parameter and returns `skew25d: number | null` plus a new
`skew25dIsReal: boolean`:

- No `volResponse` at all → synthetic/demo mesh → proxy (unchanged
  behavior for the only live call site today).
- `volResponse` present, `skew.skew_25delta` a finite number → use the
  real backend value, `skew25dIsReal: true`.
- `volResponse` present, `skew.skew_25delta` absent/non-finite → `null`
  (renders "—", plus a "Unavailable this cycle" note) — **not** the
  proxy. Falling back to the proxy here would keep the exact
  "two disagreeing numbers under one label" bug, just now visible instead
  of latent.

The card also grew a small "(chain)"/"(proxy)" provenance suffix next to
the "25Δ PUT-CALL SKEW" label, matching `OptionsMatrix.tsx`'s existing
"IVR (chain)"/"IVR Proxy" convention (`optionsHonesty.effectiveIvr`) for
exactly this real-vs-fallback distinction.

### Test coverage

`VolSurface3D.test.tsx` — two new tests: one constructs a `volResponse`
whose real `skew_25delta` is deliberately set to `proxyValue + 0.10` (not
a coincidentally-matching literal — the existing fixture's proxy and real
skew happened to already agree by chance, which would not have caught
this bug) and asserts the displayed value is the real one, not the proxy;
the other supplies a `volResponse` with `skew_25delta: undefined` and
asserts `skew25d` is `null`, `skew25dIsReal` is `false` — never a silent
proxy substitution.

## Verification run

```
npm run --prefix webapp typecheck          # clean
npx vitest run src/optionsMath.test.ts     # 20/20 passed
npx vitest run src/components/charts/VolSurface3D.test.tsx  # 29/29 passed
npx vitest run src/screens/OptionsMatrix.test.tsx            # 17/17 passed
npm test                                    # 169 files / 1869 tests, 0 regressions
```
