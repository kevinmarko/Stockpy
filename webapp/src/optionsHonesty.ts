/**
 * optionsHonesty.ts — shared client-side gates for the options directive.
 *
 * The engine (technical_options_engine.py) only assigns a real value to the
 * CREDIT structures (Put/Call Credit Spread, Iron Condor); on debit spreads,
 * Covered Call, and Cash it leaves the field uncomputed. Two screens
 * (OptionsMatrix, SymbolDetail) render an options directive, so this lives
 * here rather than duplicated per-screen — a strategy-name list that only one
 * of two call sites remembers to update is exactly the kind of drift that
 * reintroduces the fabricated-0.0 bug this gate exists to prevent.
 */
import type { OptionsDirective } from "./api/types";

const THETA_ASSIGNED = new Set([
  "Put Credit Spread",
  "Call Credit Spread",
  "Iron Condor",
]);

export function realizableTheta(
  d: OptionsDirective
): { value: number | null; note: string | null } {
  if (d.Strategy && THETA_ASSIGNED.has(d.Strategy)) {
    return { value: d.Realizable_Daily_Theta ?? null, note: null };
  }
  return {
    value: null,
    note:
      "Not computed for this strategy — the engine assigns realizable theta only " +
      "to credit structures. A persisted 0.0 (or null) is a default, not a measurement.",
  };
}

/**
 * Resolves the IVR value + provenance a directive should display, preferring
 * the real, options-chain-derived `True_IVR` over the realized-volatility
 * `IVR_Proxy` — mirroring `technical_options_engine.build_premium_directive`'s
 * own preference order (settings.OPTIONS_TRUE_IVR_ENABLED). `isTrue` lets
 * callers label the source honestly per-row: even when the flag is on and
 * SOME symbols resolved a real chain-derived IV, others may still have
 * degraded to the proxy this cycle (no chain data / empty warm-start
 * history) — a per-row check is required, a screen-wide flag would overstate
 * provenance for the fallback rows.
 */
export function effectiveIvr(
  d: OptionsDirective
): { value: number | null; isTrue: boolean } {
  if (typeof d.True_IVR === "number" && Number.isFinite(d.True_IVR)) {
    return { value: d.True_IVR, isTrue: true };
  }
  if (typeof d.IVR_Proxy === "number" && Number.isFinite(d.IVR_Proxy)) {
    return { value: d.IVR_Proxy, isTrue: false };
  }
  return { value: null, isTrue: false };
}
