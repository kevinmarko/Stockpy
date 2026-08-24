# Implementation Plan: Options Matrix POP truncation bug + VolSurface3D skew drift-risk

**Branch:** `fix-options-pop-and-vol-skew-drift`
**Source:** external audit of client-side quantitative calculations in `webapp/src/`

## Scope

Two independent findings from an audit of `webapp/src/`, both confined to the
webapp frontend (no backend/Python changes):

1. **BUG (confirmed, quantified):** `OptionsMatrix.tsx`'s "Prob of Profit
   (POP)" card numerically integrates a `Normal(spot, sd)` density over
   `computePayoff`'s finite charting grid (`[0.8, 1.2] x spot`, widened
   modestly by strikes). That grid is sized for a chart x-axis, not for
   integrating probability — a credit spread's flat profitable plateau runs
   to the grid edge and beyond, so tail probability mass past the edge is
   silently dropped. Hand-verified: understates POP by 30+ percentage points
   on a realistic longer-dated, higher-vol credit spread.

2. **Drift-risk (latent, not yet live-impacting):** `VolSurface3D.tsx`'s
   `calculateSurfaceMetrics()` always recomputes "25Δ Put-Call Skew" via a
   fixed-moneyness proxy (nearest strike to spot×0.95/1.05), never reading
   the real, delta-derived backend value
   (`VolSurfaceResponse.skew.skew_25delta` from
   `pilots/volatility_surface.py::compute_25delta_skew`) even when it's
   supplied as a prop. The sibling 2D screen (`VolSurfaceView.tsx`) already
   renders the real backend value under the identical label. Currently
   latent because the only live call site never passes real vol-surface
   data in, but a future wiring change would silently show two disagreeing
   numbers under one label.

## Design decisions

**Finding 1 — fix in the frontend (option a), not the backend (option b).**
The audit offered both routes. Chose frontend because:
- The existing calculation already uses `Sigma_GARCH` (GARCH-forecast vol),
  not real chain IV — moving the *same* computation to the backend would
  not improve input-data quality, only relocate where it runs.
- The truncation is a pure numerical-integration bug, fully fixable in
  closed form using data already in scope (spot, sigma, dte, legs) — no
  new backend endpoint, `OptionsDirective` schema field,
  `config.COLUMN_SCHEMA` addition, or pipeline recompute is needed.
  Backend routing would pull this into the "engines/signals" tier
  (schema changes, state_snapshot parity tests, docs/signals/*.md) for a
  bug that is entirely a client-side integration-bounds defect.
- Reuses this module's own established closed-form convention
  (`computeProbabilityZones`'s zero-drift log-normal model), so the fix is
  self-consistent with the rest of `optionsMath.ts` rather than introducing
  a second probability model.

**Approach:** replace the truncated Riemann sum with a closed-form
integration over the TRUE unbounded price domain. `computeBreakevenPoints`
already finds every zero-crossing of the (piecewise-linear) payoff curve;
those breakevens partition `(0, +Infinity)` into intervals whose payoff
sign cannot change inside them (a multi-leg vanilla-option position is
asymptotically linear beyond its outermost strike — never oscillates), so
one representative probe point per interval classifies the whole interval
as profit/loss. Sum the log-normal probability mass
(`cumulativeNormalDistribution`, matching `computeProbabilityZones`'s
model) of every profit interval, including the two open-ended tails, via
closed-form CDF. Also switches the price-at-expiration model from Normal
(the old code) to log-normal (matching the rest of this module) per the
audit's secondary note.

Also deduplicates `evaluatePayoffAt` out of `computePayoff` and
`computeBreakevenPoints` (previously two independent copy-pasted payoff
formulas) into one exported helper, since the new function needs the same
evaluation and three divergent copies is worse than one shared one.

**Finding 2 — prefer real, fall back to proxy ONLY for the fully-synthetic
mesh case; never silently substitute the proxy for an ABSENT real value.**
Mirrors `optionsHonesty.effectiveIvr`'s real-vs-proxy preference pattern.
Three cases:
- No `volResponse` prop at all → genuinely synthetic/demo mesh (flagged
  elsewhere by `<DemoDataBadge />`) → moneyness proxy is the only estimate
  available, used as before.
- `volResponse` present, `skew.skew_25delta` a finite number → use it,
  mark `skew25dIsReal: true`.
- `volResponse` present, `skew.skew_25delta` absent/non-finite → render an
  honest "—" (`skew25d: null`), NOT the proxy. Falling back to the proxy
  here would keep the exact "two disagreeing numbers under one label" bug
  the finding describes, just visible instead of latent.

## Files touched

- `webapp/src/optionsMath.ts` — `evaluatePayoffAt` (new, exported,
  deduplicated), `computeProbabilityOfProfit` (new, exported), refactor
  `computePayoff`/`computeBreakevenPoints` to use the shared helper.
- `webapp/src/screens/OptionsMatrix.tsx` — `DetailSheet`'s `popPercent`
  now calls `computeProbabilityOfProfit` instead of the inline truncated
  Riemann sum; drops the now-unused `normalProbabilityDensity` import.
- `webapp/src/components/charts/VolSurface3D.tsx` —
  `calculateSurfaceMetrics` gains an optional `volResponse` param and a
  `skew25dIsReal` return field; skew25d becomes `number | null`; render
  block updated for the null case plus a "(chain)"/"(proxy)" provenance
  label (matching `OptionsMatrix.tsx`'s existing "IVR (chain)"/"IVR Proxy"
  convention).
- `webapp/src/optionsMath.test.ts` — new `computeProbabilityOfProfit` /
  `evaluatePayoffAt` test coverage, including the confirmed discrepancy
  case (single-breakeven credit spread, spot 100/sigma 40%/dte 252) with
  a self-computed closed-form reference value, a near-the-money sanity
  check, a multi-breakeven (iron condor) case, degenerate-input handling,
  and a `[0, 100]` bounds check.
- `webapp/src/components/charts/VolSurface3D.test.tsx` — new tests
  proving `calculateSurfaceMetrics` prefers a real, deliberately-divergent
  backend skew value over the proxy, and reports `null` (not the proxy)
  when `volResponse` is present but its skew field is absent.

## Documentation

No `docs/architecture/*.md` or `docs/signals/*.md` changes required — this
is a client-side calculation bug/drift-risk with no backend/schema/signal
surface. Added `docs/known_issues/options_matrix_pop_truncation_and_vol_skew_drift.md`
per this repo's convention of writing up confirmed quant-correctness bugs
found and fixed, once the PR number is known.

## Verification

- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run src/optionsMath.test.ts` — 20/20 passed.
- `npx vitest run src/components/charts/VolSurface3D.test.tsx` — 29/29
  passed.
- `npx vitest run src/screens/OptionsMatrix.test.tsx` — 17/17 passed
  (no regression; existing suite had no POP-specific coverage).
- Full `npm test` (webapp) — 169 files / 1869 tests passed, no
  regressions elsewhere.
- Fix numerically verified against independently-derived closed-form
  reference values in a standalone Node script before being written into
  the codebase (see the implementation_plan's "Design decisions" section
  above and the walkthrough for the exact numbers).
