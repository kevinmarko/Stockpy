/**
 * theme.test.ts — pins the theme.ts ↔ index.css token parity.
 *
 * theme.ts and index.css declare the SAME palette twice (theme.ts exists only
 * because Recharts needs JS color values, not CSS vars). Its docstring says
 * "Change a value here AND in index.css together" — this test turns that
 * hand-sync comment into a CI gate: it reads the `:root` block out of index.css
 * and asserts each of the 13 scalar tokens matches its theme.ts counterpart.
 *
 * Values are compared WHITESPACE-NORMALIZED on purpose — index.css writes
 * `rgba(255, 255, 255, 0.08)` while theme.ts writes `rgba(255,255,255,0.08)`;
 * those are the same color and must not fail the test over a space.
 *
 * Only the 13 scalars are checked. SECTOR_PALETTE / CATEGORY_PALETTE have no
 * CSS-var counterpart by design (they're chart-only ramps), and the spacing /
 * typography / radius tokens live only in CSS (never mirrored into theme.ts).
 */
// This is the only file that touches the Node fs/path/process APIs (to read
// index.css off disk). The app's tsconfig uses an explicit `types` allowlist
// (["vite/client", ...]) that deliberately keeps Node globals OUT of browser
// code, so pull the node types in for THIS FILE ONLY via a reference directive
// rather than adding "node" to the global allowlist.
/// <reference types="node" />
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
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
  textPrimary: "--text-primary",
  textSecondary: "--text-secondary",
  textMuted: "--text-muted",
  border: "--border",
  borderStrong: "--border-strong",
  growth: "--growth",
  decline: "--decline",
  caution: "--caution",
  accent: "--accent",
};

const norm = (v: string) => v.replace(/\s+/g, "").toLowerCase();

/** Parse `--name: value;` declarations out of the first `:root { ... }` block. */
function readRootVars(): Record<string, string> {
  const root = indexCss.match(/:root\s*\{([\s\S]*?)\}/);
  if (!root) throw new Error("no :root block found in index.css");
  const vars: Record<string, string> = {};
  for (const decl of root[1].split(";")) {
    const m = decl.match(/(--[\w-]+)\s*:\s*([\s\S]+)/);
    if (m) vars[m[1].trim()] = m[2].trim();
  }
  return vars;
}

describe("theme.ts ↔ index.css token parity", () => {
  const cssVars = readRootVars();

  it.each(Object.entries(KEY_TO_CSS_VAR))(
    "theme.%s matches %s in index.css",
    (key, cssVar) => {
      const cssValue = cssVars[cssVar];
      expect(cssValue, `${cssVar} missing from index.css :root`).toBeDefined();
      expect(norm(cssValue)).toBe(norm(theme[key as keyof typeof theme]));
    }
  );

  it("every checked CSS var actually exists (guards a renamed/removed token)", () => {
    for (const cssVar of Object.values(KEY_TO_CSS_VAR)) {
      expect(cssVars[cssVar], `${cssVar} missing`).toBeDefined();
    }
  });
});
