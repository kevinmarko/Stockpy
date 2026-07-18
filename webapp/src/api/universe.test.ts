/**
 * universe.test.ts — the mock GET /universe fixture is an honest, usable
 * autocomplete source: sorted, non-empty, exercises both the decorated
 * (action present) and undecorated (action null) rows, and every suggested
 * symbol resolves to a real symbol-detail page (no dead-end suggestions).
 */
import { describe, expect, it } from "vitest";
import { mockApi } from "./mock";

describe("mock getUniverse fixture", () => {
  it("returns a sorted, non-empty tracked universe", async () => {
    const { symbols } = await mockApi.getUniverse();
    expect(symbols.length).toBeGreaterThan(0);
    const names = symbols.map((s) => s.symbol);
    expect([...names].sort()).toEqual(names);
  });

  it("exercises both decorated and undecorated (null-action) rows", async () => {
    const { symbols } = await mockApi.getUniverse();
    expect(symbols.some((s) => s.action !== null)).toBe(true);
    expect(symbols.some((s) => s.action === null)).toBe(true);
  });

  it("suggests only symbols that resolve to a real detail page", async () => {
    const { symbols } = await mockApi.getUniverse();
    // Every universe symbol must be a live symbol-detail lookup, not a dead end.
    // Resolve in parallel — getSymbol has artificial latency, so a sequential
    // await over the whole universe would blow the test timeout.
    const details = await Promise.all(symbols.map((s) => mockApi.getSymbol(s.symbol)));
    expect(details.map((d) => d.symbol)).toEqual(symbols.map((s) => s.symbol));
  });
});
