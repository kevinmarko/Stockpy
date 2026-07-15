/**
 * theme.ts — Stockpy Pilots dark fintech design tokens.
 *
 * These mirror the CSS custom properties declared in index.css so chart code
 * (Recharts, which needs JS color values, not CSS vars) reads the SAME palette
 * the rest of the UI uses. Change a value here AND in index.css together.
 *
 * Palette provenance:
 *  - Semantic status colors (green/red/amber) are Stockpy's existing gui palette,
 *    kept consistent with the operator console.
 *  - The categorical `sector` ramp was validated with the dataviz skill's
 *    validate_palette.js against the dark surface #12161c:
 *    all 8 slots pass lightness band, chroma floor, CVD separation (worst
 *    adjacent ΔE 23.7), and >=3:1 contrast. Do not reorder without re-validating.
 *  - The categorical `Pilot category` ramp (below) is a SEPARATE 8-hue set —
 *    deliberately distinct from SECTOR_PALETTE so a category chip is never
 *    mistaken for a sector-donut slice on the same Pilot Detail page — also
 *    validated with validate_palette.js against #12161c: all 8 slots pass
 *    lightness band, chroma floor, and >=3:1 contrast; worst *adjacent* CVD ΔE
 *    10.2 (deutan)/7.8 (tritan), worst adjacent normal-vision ΔE 23.3 (>=15
 *    floor). Like the default reference palette, no ordering of the full eight
 *    clears the stricter *all-pairs* check (a hard cap the validator documents
 *    for any 8-hue categorical set) — acceptable here because every category
 *    chip always renders its name as visible text (CategoryChip), so identity
 *    is never color-alone (the required secondary-encoding mitigation).
 */

import type { PilotCategory } from "./api/types";

export const theme = {
  // Surfaces (dark fintech base)
  base: "#0b0e11", // page plane
  surface: "#12161c", // card / chart surface
  surface2: "#1a212b", // raised surface (chips, inputs)
  surface3: "#232c38", // hover / pressed

  // Ink
  textPrimary: "#f2f5f8",
  textSecondary: "#9aa7b4",
  textMuted: "#67727f",

  // Hairlines
  border: "rgba(255,255,255,0.08)",
  borderStrong: "rgba(255,255,255,0.14)",

  // Semantic (status) — reserved meaning, never used as a categorical series slot
  growth: "#10b981", // green — gains / positive / deployable
  decline: "#ef4444", // red — losses / negative / not deployable
  caution: "#f59e0b", // amber — warnings / pending / gated

  // Brand accent (interactive / focus)
  accent: "#38bdf8",
} as const;

/**
 * Categorical palette for the sector-allocation donut.
 * Green (#008300) was deliberately dropped from the standard dataviz dark ramp
 * so a sector slice never impersonates the semantic "growth" green.
 */
export const SECTOR_PALETTE: string[] = [
  "#3987e5", // blue
  "#199e70", // aqua
  "#c98500", // yellow
  "#9085e9", // violet
  "#e66767", // red
  "#d55181", // magenta
  "#d95926", // orange
  "#5a9bd4", // light blue
];

/** Deterministic slot for a sector name (fixed order, never cycled arbitrarily). */
export function sectorColor(index: number): string {
  if (index < SECTOR_PALETTE.length) return SECTOR_PALETTE[index];
  // 9th+ category folds into a neutral "Other" tone rather than a generated hue.
  return theme.textMuted;
}

/**
 * Categorical palette for Pilot-category chips (Marketplace cards, Pilot Detail
 * header) — fixed hue-name assignment, in the SAME order as the `PilotCategory`
 * union in `api/types.ts`; never reorder without re-running validate_palette.js
 * (see the module docstring above for the validation result).
 */
export const CATEGORY_PALETTE: Record<PilotCategory, string> = {
  Momentum: "#6366f1", // indigo
  "Mean Reversion": "#c2410c", // copper
  Factor: "#0d9488", // teal
  Blend: "#e11d48", // rose
  Macro: "#0891b2", // ocean
  Risk: "#a16207", // amber
  Sentiment: "#c026d3", // fuchsia
  Forecast: "#65a30d", // lime
};

/** Deterministic color for a Pilot category (fixed name-keyed slot, never cycled). */
export function categoryColor(category: PilotCategory): string {
  return CATEGORY_PALETTE[category] ?? theme.textMuted;
}

/** Green for gains, red for losses, muted for flat. */
export function pnlColor(value: number): string {
  if (value > 0) return theme.growth;
  if (value < 0) return theme.decline;
  return theme.textSecondary;
}

export type Theme = typeof theme;
