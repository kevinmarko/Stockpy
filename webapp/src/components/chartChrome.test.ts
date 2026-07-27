/**
 * chartChrome.test.ts — invariants for the shared chart-chrome layer
 * (SERIES_PALETTE/seriesColor in theme.ts, chartTooltipStyle/chartGridProps
 * in charts.tsx).
 *
 * Machine-checks the two rules that were violated before this layer existed:
 * three files each hand-declared a duplicate categorical color array that
 * used theme.accent/growth/caution directly as series slots (theme.ts's own
 * docstring reserves those for status meaning, "never used as a categorical
 * series slot" — prose nobody was enforcing), and five files each
 * hand-declared a Tooltip contentStyle that disagreed with both charts.tsx's
 * own tooltip AND index.css's `.recharts-default-tooltip` CSS override.
 */
/// <reference types="node" />
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { SERIES_PALETTE, seriesColor, theme } from "../theme";
import { chartTooltipStyle } from "./charts";

function loadIndexCss(): string {
  const candidates = [
    resolve(process.cwd(), "src/index.css"),
    resolve(process.cwd(), "webapp/src/index.css"),
  ];
  const hit = candidates.find(existsSync);
  if (!hit) throw new Error(`index.css not found (looked in: ${candidates.join(", ")})`);
  return readFileSync(hit, "utf-8");
}

describe("SERIES_PALETTE / seriesColor", () => {
  const RESERVED = new Set<string>([theme.accent, theme.growth, theme.decline, theme.caution]);

  it("no slot reuses a reserved status color", () => {
    for (const hue of SERIES_PALETTE) {
      expect(RESERVED.has(hue), `${hue} is a reserved status color, not a series slot`).toBe(false);
    }
  });

  it("stays at the validated 3-hue cap (re-run validate_palette.js --pairs all before raising this)", () => {
    // theme.ts's module docstring records that 5 candidate 4th hues were
    // tried against this base and none cleared the all-pairs CVD/normal-
    // vision floor check in this lightness band. If a future hue DOES clear
    // it, update this alongside SERIES_PALETTE — don't just bump the number.
    expect(SERIES_PALETTE.length).toBe(3);
  });

  it("seriesColor folds any index beyond the palette to theme.textMuted, never a generated hue", () => {
    expect(seriesColor(SERIES_PALETTE.length)).toBe(theme.textMuted);
    expect(seriesColor(SERIES_PALETTE.length + 10)).toBe(theme.textMuted);
  });

  it("seriesColor returns the exact palette hue in-range", () => {
    SERIES_PALETTE.forEach((hue, i) => expect(seriesColor(i)).toBe(hue));
  });
});

describe("chartTooltipStyle", () => {
  it("matches index.css's .recharts-default-tooltip override — one tooltip surface, not two", () => {
    const css = loadIndexCss();
    const block = css.match(/\.recharts-default-tooltip\s*\{([\s\S]*?)\}/);
    expect(block, ".recharts-default-tooltip rule not found in index.css").not.toBeNull();
    const body = block![1];

    // background: var(--surface-3) !important;  ->  must agree with theme.surface3
    expect(chartTooltipStyle.background).toBe(theme.surface3);
    expect(body).toContain("var(--surface-3)");

    // border: 1px solid var(--border-strong) !important;
    expect(chartTooltipStyle.border).toBe(`1px solid ${theme.borderStrong}`);
    expect(body).toContain("var(--border-strong)");

    // borderRadius: 10px !important;
    expect(chartTooltipStyle.borderRadius).toBe(10);
    expect(body).toMatch(/border-radius:\s*10px/);
  });
});
