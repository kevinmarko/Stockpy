/**
 * onboarding.test.ts — unit tests for the client-side onboarding completion
 * marker (localStorage-backed, mirrors gui/onboarding.py's concept).
 *
 * This was the one webapp module left with zero test coverage after the
 * 2026-07-14 test-coverage re-audit's Phase 5 pass closed every other item
 * (docs/test_coverage_analysis.md's Phase 5 item 7 originally flagged
 * Onboarding.tsx as "no financial/safety logic" and low priority — true for
 * the screen's UI, but onboarding.ts's localStorage read/write functions are
 * exactly the kind of pure, easily-testable logic this codebase's own
 * convention (see format.test.ts) says should always be covered).
 *
 * Every function here is designed to never throw (localStorage can throw in
 * private-browsing/storage-disabled contexts) -- that degradation path is
 * exercised directly by monkeypatching the global localStorage methods to
 * throw, not just by reasoning about the try/catch from the source.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  completeOnboarding,
  readOnboarding,
  resetOnboarding,
  writeOnboarding,
} from "./onboarding";

const KEY = "stockpy.onboarding.v1";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("readOnboarding", () => {
  it("returns completed:false when nothing is stored", () => {
    expect(readOnboarding()).toEqual({ completed: false });
  });

  it("returns the parsed stored state", () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ completed: true, pilotId: "trend-following", amount: 500 })
    );

    expect(readOnboarding()).toEqual({
      completed: true,
      pilotId: "trend-following",
      amount: 500,
    });
  });

  it("degrades to completed:false on corrupt JSON, never throws", () => {
    localStorage.setItem(KEY, "{not valid json");

    expect(readOnboarding()).toEqual({ completed: false });
  });

  it("degrades to completed:false when localStorage.getItem throws, never throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage disabled");
    });

    expect(readOnboarding()).toEqual({ completed: false });
  });
});

describe("writeOnboarding", () => {
  it("persists the exact state as JSON", () => {
    writeOnboarding({ completed: true, brokerage: "paper", amount: 1000 });

    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({
      completed: true,
      brokerage: "paper",
      amount: 1000,
    });
  });

  it("swallows a localStorage.setItem failure rather than throwing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded");
    });

    expect(() => writeOnboarding({ completed: true })).not.toThrow();
  });
});

describe("completeOnboarding", () => {
  it("merges the partial over existing state and forces completed:true", () => {
    writeOnboarding({ completed: false, pilotId: "trend-following" });

    completeOnboarding({ brokerage: "paper", amount: 500 });

    const stored = readOnboarding();
    expect(stored.completed).toBe(true);
    expect(stored.pilotId).toBe("trend-following"); // preserved, not overridden
    expect(stored.brokerage).toBe("paper");
    expect(stored.amount).toBe(500);
  });

  it("sets a real, parseable ISO completedAt timestamp", () => {
    completeOnboarding({ pilotId: "trend-following" });

    const stored = readOnboarding();
    expect(stored.completedAt).toBeDefined();
    expect(Number.isNaN(new Date(stored.completedAt!).getTime())).toBe(false);
  });

  it("works from a completely empty prior state (first-run case)", () => {
    completeOnboarding({ brokerage: "skip" });

    expect(readOnboarding()).toMatchObject({ completed: true, brokerage: "skip" });
  });

  it("overrides an explicit completed:false in the partial (completed is always forced true)", () => {
    // Defensive: the spread order in the source means completed:true always
    // wins even if a caller mistakenly passes completed:false.
    completeOnboarding({ completed: false } as Partial<
      ReturnType<typeof readOnboarding>
    >);

    expect(readOnboarding().completed).toBe(true);
  });
});

describe("resetOnboarding", () => {
  it("removes the stored key", () => {
    completeOnboarding({ pilotId: "trend-following" });
    expect(readOnboarding().completed).toBe(true);

    resetOnboarding();

    expect(readOnboarding()).toEqual({ completed: false });
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("is a no-op (never throws) when nothing was stored", () => {
    expect(() => resetOnboarding()).not.toThrow();
  });

  it("swallows a localStorage.removeItem failure rather than throwing", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("storage disabled");
    });

    expect(() => resetOnboarding()).not.toThrow();
  });
});
