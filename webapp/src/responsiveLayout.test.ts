/**
 * responsiveLayout.test.ts — the .split-* layout classes in index.css stay MOBILE-FIRST.
 *
 * jsdom has no layout engine, so a test cannot measure whether a page overflows at 390px.
 * What it can pin is the stylesheet's shape, which is exactly what regressed: a fixed-width
 * rail / two fixed columns outside a min-width media query. Found by a browser audit that
 * loaded all 54 routes at 390px: 17 of them scrolled sideways, all of them because a layout
 * that only works on a wide screen was written as an inline style with no breakpoint.
 *
 * (Re-measure for real with `npm run dev` and a phone-width browser; see /verify-webapp.)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Read from disk, not `import css from "./index.css?raw"`: vitest stubs CSS imports to "".
// process.cwd() is the webapp directory (same convention as UniverseCoverage.test.tsx).
const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf-8");

/** Body of the first `selector {` rule in the file (the base, un-queried rule). */
function baseRule(selector: string): string {
  const m = new RegExp(`(?:^|\\n)${selector.replace(/[.>]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(css);
  if (!m) throw new Error(`no base rule for ${selector} in index.css`);
  return m[1];
}

/** Body of `selector` inside the 768px min-width media block that holds the .split rules. */
function wideRule(selector: string): string {
  const block = /@media \(min-width: 768px\)\s*\{\s*\.split-pane[\s\S]*?\n\}\n/.exec(css);
  if (!block) throw new Error("no (min-width: 768px) block containing .split-pane in index.css");
  const m = new RegExp(`${selector.replace(/[.>]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(block[0]);
  if (!m) throw new Error(`no ${selector} rule inside the 768px block`);
  return m[1];
}

describe("index.css split layouts are mobile-first", () => {
  it(".split-pane is a column by default and a row only from 768px", () => {
    expect(baseRule(".split-pane")).toMatch(/flex-direction:\s*column/);
    expect(wideRule(".split-pane")).toMatch(/flex-direction:\s*row/);
  });

  it("the rail has no fixed width, and is not sticky, until 768px", () => {
    const base = baseRule(".split-rail");
    expect(base).not.toMatch(/(^|[;\s])width\s*:/);
    expect(base).not.toMatch(/position\s*:\s*sticky/);
    const wide = wideRule(".split-rail");
    expect(wide).toMatch(/width:\s*250px/);
    expect(wide).toMatch(/flex-shrink:\s*0/);
  });

  it(".split-cols is one shrinkable column by default and two from 768px", () => {
    // minmax(0, 1fr), not a bare 1fr: a bare 1fr cannot shrink below its content's min width.
    expect(baseRule(".split-cols")).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)\s*;/);
    expect(wideRule(".split-cols")).toMatch(/repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  });
});
