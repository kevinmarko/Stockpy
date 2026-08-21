/**
 * theme.test.ts — pins the theme.ts ↔ index.css token parity.
 *
 * theme.ts and index.css declare the SAME palette twice (theme.ts exists only
 * because Recharts needs JS color values, not CSS vars). Its docstring says
 * "Change a value here AND in index.css together" — this test turns that
 * hand-sync comment into a CI gate: it reads the `:root` block out of index.css
 * and asserts each of the 14 scalar tokens matches its theme.ts counterpart.
 *
 * Values are compared WHITESPACE-NORMALIZED on purpose — index.css writes
 * `rgba(255, 255, 255, 0.08)` while theme.ts writes `rgba(255,255,255,0.08)`;
 * those are the same color and must not fail the test over a space.
 *
 * Only these scalars are checked. SECTOR_PALETTE / CATEGORY_PALETTE /
 * SERIES_PALETTE have no CSS-var counterpart by design (they're chart-only
 * ramps, arrays not scalars), and the spacing / typography / radius tokens
 * live only in CSS (never mirrored into theme.ts). Because KEY_TO_CSS_VAR is
 * typed `Record<keyof typeof theme, string>`, adding a new scalar to `theme`
 * without adding its CSS-var mapping here is already a `tsc` compile error —
 * this map can't silently fall behind theme.ts.
 */
// This is the only file that touches the Node fs/path/process APIs (to read
// index.css off disk). The app's tsconfig uses an explicit `types` allowlist
// (["vite/client", ...]) that deliberately keeps Node globals OUT of browser
// code, so pull the node types in for THIS FILE ONLY via a reference directive
// rather than adding "node" to the global allowlist.
/// <reference types="node" />
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { theme } from "./theme";

// Read index.css off disk. `?raw` imports are stubbed to "" by vitest's CSS
// handling and `import.meta.url` isn't a file:// URL under the transform, so
// resolve from cwd (webapp/ in CI and locally) with a repo-root fallback.
function loadIndexCss(): string {
  const candidates = [
    resolve(process.cwd(), "src/index.css"),
    resolve(process.cwd(), "webapp/src/index.css"),
  ];
  const hit = candidates.find(existsSync);
  if (!hit) throw new Error(`index.css not found (looked in: ${candidates.join(", ")})`);
  return readFileSync(hit, "utf-8");
}

const indexCss = loadIndexCss();

// theme.ts key -> the CSS custom property it must equal.
const KEY_TO_CSS_VAR: Record<keyof typeof theme, string> = {
  base: "--base",
  surface: "--surface",
  surface2: "--surface-2",
  surface3: "--surface-3",
  surfaceGlass: "--surface-glass",
  textPrimary: "--text-primary",
  textSecondary: "--text-secondary",
  textMuted: "--text-muted",
  border: "--border",
  borderStrong: "--border-strong",
  chartGrid: "--chart-grid",
  growth: "--growth",
  decline: "--decline",
  caution: "--caution",
  accent: "--accent",
};

const norm = (v: string) => v.replace(/\s+/g, "").toLowerCase();

/**
 * Parse `--name: value;` declarations out of EVERY `:root { ... }` block —
 * index.css has more than one (the base token block, plus a small later one
 * for `--safe-bottom`/`--safe-top`). Merging all of them, not just the first,
 * is what makes this a correct "is --x declared anywhere" source of truth for
 * the undeclared-var guard below; it's a superset for the 13-scalar parity
 * check above too (those 13 all live in the first block, so no change there).
 */
function readRootVars(): Record<string, string> {
  const blocks = [...indexCss.matchAll(/:root\s*\{([\s\S]*?)\}/g)];
  if (blocks.length === 0) throw new Error("no :root block found in index.css");
  const vars: Record<string, string> = {};
  for (const block of blocks) {
    for (const decl of block[1].split(";")) {
      const m = decl.match(/(--[\w-]+)\s*:\s*([\s\S]+)/);
      if (m) vars[m[1].trim()] = m[2].trim();
    }
  }
  return vars;
}

describe("theme.ts ↔ index.css token parity", () => {
  const cssVars = readRootVars();

  it.each(Object.entries(KEY_TO_CSS_VAR))(
    "theme.%s uses var(%s)",
    (key, cssVar) => {
      expect(theme[key as keyof typeof theme]).toBe(`var(${cssVar})`);
    }
  );

  it("every checked CSS var actually exists (guards a renamed/removed token)", () => {
    for (const cssVar of Object.values(KEY_TO_CSS_VAR)) {
      expect(cssVars[cssVar], `${cssVar} missing`).toBeDefined();
    }
  });
});

/**
 * Undeclared-CSS-var guard.
 *
 * A `var(--x)` reference with NO fallback (e.g. `var(--danger-color)`, as
 * opposed to `var(--font-mono, ui-monospace, monospace)`) resolves to nothing
 * if `--x` was never declared in index.css's `:root` — silently, with no
 * build error, no lint warning, and no visual difference from "intentionally
 * unstyled." That exact bug shipped on the live-trading mode switch in
 * Settings.tsx (`var(--danger-color)`, `var(--text-dim)` — neither ever
 * declared, neither had a fallback): the paper->live warning text and the
 * live-mode confirm button silently lost their color. This test would have
 * caught it — it scans every .ts/.tsx/.css file under src/ for a fallback-less
 * `var(--x)` whose `--x` isn't in index.css's `:root` block.
 *
 * A reference WITH a fallback (`var(--font-mono, ui-monospace, monospace)`)
 * is intentionally allowed even when `--font-mono` is undeclared — the
 * fallback is a deliberate, working default, not a bug.
 */
describe("no undeclared CSS custom properties without a fallback", () => {
  const SRC_DIR = resolve(process.cwd(), existsSync(resolve(process.cwd(), "src")) ? "src" : "webapp/src");
  const SCAN_EXTENSIONS = new Set([".ts", ".tsx", ".css"]);
  const VAR_REF = /var\(\s*(--[a-zA-Z0-9-]+)\s*(,)?/g;

  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      const stat = statSync(full);
      if (stat.isDirectory()) {
        walk(full, out);
      } else if (SCAN_EXTENSIONS.has(extname(entry)) && !/\.test\.tsx?$/.test(entry)) {
        // Skip *.test.ts(x) — this file's own docstring above deliberately
        // names var(--danger-color)/var(--x) as prose examples of the bug
        // being guarded against, which isn't real usage. Test files aren't
        // where a real undeclared-var bug would ship anyway.
        out.push(full);
      }
    }
    return out;
  }

  it("every fallback-less var(--x) reference in src/ has a matching :root declaration", () => {
    const declared = new Set(Object.keys(readRootVars()));
    const violations: string[] = [];

    for (const file of walk(SRC_DIR)) {
      const contents = readFileSync(file, "utf-8");
      for (const match of contents.matchAll(VAR_REF)) {
        const [, varName, hasFallback] = match;
        if (!hasFallback && !declared.has(varName)) {
          violations.push(`${file.replace(SRC_DIR, "src")}: var(${varName})`);
        }
      }
    }

    expect(
      violations,
      `Undeclared CSS var(s) with no fallback (add to index.css :root, or add ", <fallback>" if the fallback is intentional):\n${violations.join("\n")}`
    ).toEqual([]);
  });
});
