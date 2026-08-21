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
 *    validate_palette.js against the dark surface #12161c: lightness band,
 *    chroma floor, and >=3:1 contrast all PASS for all 8 slots. CVD
 *    separation is a WARN (worst adjacent ΔE 7.5, deutan) and the
 *    normal-vision floor check FAILS at the #d55181<->#e66767 adjacent pair
 *    (ΔE 7.8, below the 15 floor) — legal only because SectorDonut always
 *    pairs every slice with a direct text label in its legend (the required
 *    secondary-encoding mitigation), never color-alone. [Corrected 2026-07 —
 *    a prior version of this comment claimed "worst adjacent ΔE 23.7, all
 *    pass," which does not reproduce; re-run validate_palette.js yourself
 *    before trusting either version.] Do not reorder without re-validating.
 *  - The categorical `series` ramp (SERIES_PALETTE, for arbitrary N-way
 *    comparisons — symbols, strategies, models — where entries are NOT
 *    sectors or Pilot categories) is capped at 3 hues, not 8: validated at
 *    all-pairs (the stricter check, since a series legend has no fixed
 *    adjacency the way a donut's slice order does) and ALL FIVE checks pass
 *    cleanly. Five candidate 4th hues were tried and none cleared all-pairs
 *    CVD or the normal-vision floor in this lightness band — so a 4th+
 *    series folds to theme.textMuted (mirroring sectorColor's 9th+
 *    fallback) rather than shipping an unvalidated color. Re-validate before
 *    changing either the hues or the 3-slot cap.
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
 *
 * NOTE: Values here are now mapped to CSS variables to support light/dark
 * modes dynamically via the CSS :root cascade rather than JS state.
 */

import type { PilotCategory } from "./api/types";

export const theme = {
  // Surfaces (dark fintech base)
  base: "var(--base)", // page plane
  surface: "var(--surface)", // card / chart surface
  surface2: "var(--surface-2)", // raised surface (chips, inputs)
  surface3: "var(--surface-3)", // hover / pressed

  // Mirrors index.css's --surface-glass (same value, same "why"): a
  // translucent overlay surface for glassmorphic UI that needs backdrop
  // blur over scrolling content beneath it -- e.g. the settings sticky-save
  // footer (inline style) and the .glass-panel utility class -- available
  // here for any future JS consumer (Recharts et al.) that needs the color
  // as a value rather than a CSS var. NOT used by charts.tsx's chart
  // tooltips: chartChrome.test.ts pins those to the opaque theme.surface3
  // to stay byte-equal with index.css's `.recharts-default-tooltip`
  // fallback rule (see that const's doc comment in charts.tsx).
  surfaceGlass: "var(--surface-glass)",

  // Ink
  textPrimary: "var(--text-primary)",
  textSecondary: "var(--text-secondary)",
  textMuted: "var(--text-muted)",

  // Hairlines
  border: "var(--border)",
  borderStrong: "var(--border-strong)",

  // Chart gridlines — its own token because chart grid strokes need to be
  // recessive against --border (0.08): two competing untokenized literals
  // (rgba(...,0.05) and rgba(...,0.06)) were in use across chart files
  // before this was declared. 0.06 was the majority value; kept as-is.
  chartGrid: "var(--chart-grid)",

  // Semantic (status) — reserved meaning, never used as a categorical series slot
  growth: "var(--growth)", // green — gains / positive / deployable
  decline: "var(--decline)", // red — losses / negative / not deployable
  caution: "var(--caution)", // amber — warnings / pending / gated

  // Brand accent (interactive / focus)
  accent: "var(--accent)",

  // Ink for text/icons placed on top of a SOLID semantic-color fill (e.g.
  // GlobalStatusBanner's amber "Advisory Only" bar, a solid accent button).
  // Deliberately NOT theme-reactive (no :root[data-theme="light"] override
  // in index.css) -- growth/decline/caution/accent are all mid-to-bright
  // saturated colors in BOTH themes (that's what makes a good status-fill
  // color), so the ink on top of one needs to stay dark regardless of which
  // theme is active. A prior version of this code used theme.base for this
  // (it happened to equal near-black, since that was the only theme), which
  // silently broke to near-white-on-amber once --base became theme-reactive.
  onAccent: "var(--ink-on-accent)",
} as const;

/**
 * Translucent tint of a semantic/status color, for a background/border fill
 * (e.g. a "growth" badge background, a "caution" chip tint).
 *
 * Pre-migration, `theme.ts` held literal hex strings, so call sites built a
 * translucent variant by string-concatenating a 2-digit hex alpha suffix
 * directly onto the color (`` `${theme.growth}20` `` -> `"#10b98120"`, a
 * valid 8-digit hex-with-alpha color). Now that every `theme.X` value is a
 * CSS custom-property reference like `var(--growth)` (see the module
 * docstring above), that same concatenation produces the STRING
 * `"var(--growth)20"` -- not a color at all. That's invalid CSS the browser
 * silently drops (the background/border falls back to
 * transparent/inherited, no error, no visual difference from "unstyled")
 * -- this shipped broken across ~20 components before being caught by
 * manual browser verification.
 *
 * `alpha()` is the `var()`-safe replacement, via `color-mix()` (baseline
 * across evergreen browsers since 2023) so the tint stays reactive to the
 * live theme exactly like every other `var()` reference here. `hexAlpha` is
 * the SAME 2-digit hex alpha byte the old suffix trick used (e.g. "20",
 * "25") -- kept in hex so every call site's exact original opacity carries
 * over unchanged; this just converts it to the percentage `color-mix()`
 * wants.
 */
export function alpha(cssVar: string, hexAlpha: string): string {
  const pct = (parseInt(hexAlpha, 16) / 255) * 100;
  return `color-mix(in srgb, ${cssVar} ${pct}%, transparent)`;
}

/**
 * Categorical palette for the sector-allocation donut.
 * Green (#008300) was deliberately dropped from the standard dataviz dark ramp
 * so a sector slice never impersonates the semantic "growth" green.
 */
export const SECTOR_PALETTE: string[] = [
  "var(--sector-0)", // blue
  "var(--sector-1)", // aqua
  "var(--sector-2)", // yellow
  "var(--sector-3)", // violet
  "var(--sector-4)", // red
  "var(--sector-5)", // magenta
  "var(--sector-6)", // orange
  "var(--sector-7)", // light blue
];

/** Deterministic slot for a sector name (fixed order, never cycled arbitrarily). */
export function sectorColor(index: number): string {
  if (index < SECTOR_PALETTE.length) return SECTOR_PALETTE[index];
  // 9th+ category folds into a neutral "Other" tone rather than a generated hue.
  return theme.textMuted;
}

/**
 * Categorical palette for arbitrary N-way chart series (comparing symbols,
 * strategies, or models — NOT sectors or Pilot categories, which have their
 * own dedicated ramps above/below). Deliberately does NOT include any of
 * theme.accent/growth/decline/caution — those carry reserved status meaning
 * (see the `theme` object above) and must never double as "series 2." Only 3
 * hues: see the module docstring for why a 4th could not be validated.
 */
export const SERIES_PALETTE: string[] = [
  "var(--series-0)", // blue
  "var(--series-1)", // mustard
  "var(--series-2)", // teal
];

/**
 * Deterministic slot for a chart series (fixed order, never cycled
 * arbitrarily — same contract as sectorColor). A 4th+ series folds to a
 * neutral "Other" tone rather than an unvalidated color; the caller should
 * pair that fold with a direct label (never rely on the muted tone alone to
 * carry identity for more than a couple of folded series).
 */
export function seriesColor(index: number): string {
  if (index < SERIES_PALETTE.length) return SERIES_PALETTE[index];
  return theme.textMuted;
}

/**
 * Categorical palette for Pilot-category chips (Marketplace cards, Pilot Detail
 * header) — fixed hue-name assignment, in the SAME order as the `PilotCategory`
 * union in `api/types.ts`; never reorder without re-running validate_palette.js
 * (see the module docstring above for the validation result).
 */
export const CATEGORY_PALETTE: Record<PilotCategory, string> = {
  Momentum: "var(--cat-momentum)", // indigo
  "Mean Reversion": "var(--cat-mean-reversion)", // copper
  Factor: "var(--cat-factor)", // teal
  Blend: "var(--cat-blend)", // rose
  Macro: "var(--cat-macro)", // ocean
  Risk: "var(--cat-risk)", // amber
  Sentiment: "var(--cat-sentiment)", // fuchsia
  Forecast: "var(--cat-forecast)", // lime
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
