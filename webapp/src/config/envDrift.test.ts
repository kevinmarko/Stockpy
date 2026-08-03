/**
 * envDrift.test.ts — structural guard against env-key documentation drift.
 *
 * Before this test, `vite-env.d.ts` and `README.md` each documented only 3 of
 * the 6 `VITE_*` keys the app actually reads, while `.env.example` documented
 * all 6. An undeclared key typechecks only via `vite/client`'s permissive
 * index signature, so a typo'd name is silently `undefined` with no compile
 * error — exactly the class of silent misconfiguration this whole module set
 * exists to prevent.
 *
 * Asserting every key appears in all three files makes that drift impossible
 * to reintroduce without a red test.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** Every VITE_* key `src/config/env.ts` reads. */
const ENV_KEYS = [
  "VITE_API_BASE_URL",
  "VITE_DATA_API_BASE_URL",
  "VITE_METRICS_API_BASE_URL",
  "VITE_CONTROL_API_BASE_URL",
  "VITE_USE_MOCK",
  "VITE_API_TOKEN",
] as const;

/** Resolved relative to THIS file (src/config/), so cwd never matters. */
function readRepoFile(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), "utf8");
}

const FILES: ReadonlyArray<readonly [string, string]> = [
  ["vite-env.d.ts", readRepoFile("../vite-env.d.ts")],
  [".env.example", readRepoFile("../../.env.example")],
  ["README.md", readRepoFile("../../README.md")],
  ["config/env.ts", readRepoFile("./env.ts")],
];

describe("env key documentation drift", () => {
  for (const [filename, contents] of FILES) {
    describe(filename, () => {
      it.each(ENV_KEYS)("documents %s", (key) => {
        expect(contents).toContain(key);
      });
    });
  }

  it("covers exactly the six keys, so adding a 7th forces updating this list", () => {
    expect(new Set(ENV_KEYS).size).toBe(6);
  });

  it("env.ts reads no VITE_* key that is absent from this list", () => {
    const source = readRepoFile("./env.ts");
    const referenced = new Set(source.match(/VITE_[A-Z0-9_]+/g) ?? []);
    const known = new Set<string>(ENV_KEYS);
    const unknown = [...referenced].filter((k) => !known.has(k));
    expect(unknown).toEqual([]);
  });
});
