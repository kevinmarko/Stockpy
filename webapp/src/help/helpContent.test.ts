/**
 * helpContent.test.ts — glossaryDef's live-threshold templating: a static
 * entry passes through unchanged, a threshold-bearing entry interpolates the
 * given live values, and the same entry degrades every number to "—" (never a
 * guessed value) when thresholds are unavailable.
 */
import { describe, expect, it } from "vitest";
import { GLOSSARY, TAB_HELP, glossaryDef } from "./helpContent";
import type { Thresholds } from "../api/types";

const LIVE: Thresholds = {
  pbo_max: 0.5,
  dsr_min: 0.95,
  net_sharpe_min: 0.5,
  max_drawdown_max: 0.3,
  stress_max_drawdown: 0.5,
  kelly_fraction: 0.5,
  kelly_cap: 0.2,
};

describe("glossaryDef", () => {
  it("returns undefined for an unknown key", () => {
    expect(glossaryDef("not-a-real-term")).toBeUndefined();
  });

  it("passes a static entry through unchanged, ignoring thresholds", () => {
    expect(glossaryDef("conviction")).toBe(GLOSSARY["conviction"]);
    expect(glossaryDef("conviction", LIVE)).toBe(GLOSSARY["conviction"]);
  });

  it("interpolates live threshold values into a function entry", () => {
    const def = glossaryDef("deployable", LIVE)!;
    expect(def).toContain("PBO < 0.5");
    expect(def).toContain("DSR > 0.95");
    expect(def).toContain("net-of-cost Sharpe > 0.5");
    expect(def).toContain("Max Drawdown < 30%");
  });

  it("degrades every number to '—' when thresholds are null (not a guess)", () => {
    const def = glossaryDef("deployable", null)!;
    expect(def).toContain("PBO < —");
    expect(def).toContain("DSR > —");
    expect(def).toContain("Max Drawdown < —");
    expect(def).not.toMatch(/\d/); // no digit anywhere — nothing fabricated
  });

  it("defaults to null thresholds when none are passed", () => {
    expect(glossaryDef("pbo")).toBe(glossaryDef("pbo", null));
  });

  it("renders distinct live values for pbo, dsr, sharpe ratio, max drawdown, kelly target", () => {
    expect(glossaryDef("pbo", LIVE)).toContain("< 0.5");
    expect(glossaryDef("dsr", LIVE)).toContain("> 0.95");
    expect(glossaryDef("sharpe ratio", LIVE)).toContain("> 0.5");
    expect(glossaryDef("max drawdown", LIVE)).toContain("30%");
    expect(glossaryDef("max drawdown", LIVE)).toContain("50%"); // stress gate
    expect(glossaryDef("kelly target", LIVE)).toContain("20%");
  });
});

describe("TAB_HELP content integrity", () => {
  it("every keyConcept resolves to a real GLOSSARY entry", () => {
    for (const [tabKey, help] of Object.entries(TAB_HELP)) {
      for (const key of help.keyConcepts) {
        expect(GLOSSARY, `${tabKey} references unknown glossary key "${key}"`).toHaveProperty(key);
      }
    }
  });
});
