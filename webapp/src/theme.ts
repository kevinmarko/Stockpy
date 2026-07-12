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
 */

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

/** Green for gains, red for losses, muted for flat. */
export function pnlColor(value: number): string {
  if (value > 0) return theme.growth;
  if (value < 0) return theme.decline;
  return theme.textSecondary;
}

export type Theme = typeof theme;
