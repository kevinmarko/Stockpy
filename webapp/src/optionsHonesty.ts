/**
 * optionsHonesty.ts — shared client-side gate for Realizable_Daily_Theta.
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
